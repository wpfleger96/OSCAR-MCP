"""
Analysis service for orchestrating programmatic analysis.

This module provides the main interface for running comprehensive CPAP session
analysis, loading data from the database, and storing results.

I/O–compute separation (§7)
-----------------------------
``AnalysisService.load_session_inputs_raw()`` performs **only** database reads,
returning raw bytes in a ``RawSessionBlobs`` dataclass.

``AnalysisService.prepare_inputs()`` deserialises blobs into numpy arrays
(compute phase — no DB session required).

``AnalysisService.load_session_inputs()`` is a convenience wrapper that calls
both methods in sequence; the session remains open across the NumPy work.
Callers that need the session closed before compute should call the two methods
separately — see ``AnalysisFacade.run_analysis`` and ``BatchAnalysisCoordinator``.

``AnalysisService.compute_analysis()`` runs the full analysis pipeline on a
detached ``AnalysisInputs`` DTO.  No session is held.

``AnalysisService.analyze_session()`` orchestrates the three phases
(raw-fetch + deserialise + compute + persist) with the caller-provided session.
"""

import logging
import time

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.data.waveform_loader import (
    WaveformLoader,
    deserialize_waveform_blob,
    detect_and_mark_artifacts,
    fetch_waveform_blob,
)
from snore.analysis.modes import DEFAULT_MODE, get_mode
from snore.analysis.shared.breath_segmenter import BreathSegmenter
from snore.analysis.shared.feature_extractors import WaveformFeatureExtractor
from snore.analysis.shared.flow_limitation import FlowLimitationClassifier
from snore.analysis.shared.pattern_detector import ComplexPatternDetector
from snore.analysis.shared.pulse_detector import PulseChangeDetector
from snore.analysis.shared.versioning import (
    LEAK_VALID_MAX_ALIGNMENT_GAP_S,
    LEAK_VALID_THRESHOLD_LPM,
    AlgorithmIdentity,
    AlgoVersions,
    AnalysisRunMetadata,
)
from snore.analysis.types import (
    AnalysisComputation,
    AnalysisEvent,
    AnalysisResult,
    ComputedBreath,
)
from snore.constants import BreathSegmentationConstants as BSC
from snore.constants import FlowLimitationConstants as FLC
from snore.constants import PatternDetectionConstants as PDC
from snore.constants import PulseChangeConstants as PCC
from snore.database import models

logger = logging.getLogger(__name__)

__all__ = [
    "AnalysisInputs",
    "AnalysisComputation",
    "RawSessionBlobs",
    "AnalysisService",
    "AnalysisResult",
]


@dataclass
class RawSessionBlobs:
    """Raw DB-fetched bytes for one session — no NumPy, no ORM references.

    Created by ``AnalysisService.load_session_inputs_raw()`` (I/O phase only).
    Passed to ``AnalysisService.prepare_inputs()`` outside the ORM session so
    that all NumPy work (deserialization, artifact detection) runs without a
    held session lock.
    """

    session_id: int
    flow_blob: bytes
    flow_sample_count: int
    flow_metadata: dict[str, Any]
    machine_events: list[AnalysisEvent]
    spo2_blob: bytes | None = None
    spo2_sample_count: int = 0
    spo2_metadata: dict[str, Any] = field(default_factory=dict)
    pulse_blob: bytes | None = None
    pulse_sample_count: int = 0
    pulse_metadata: dict[str, Any] = field(default_factory=dict)
    # Quality-flag inputs (plan step 3): leak channel for leak_valid derivation.
    leak_blob: bytes | None = None
    leak_sample_count: int = 0
    leak_metadata: dict[str, Any] = field(default_factory=dict)
    modes: list[str] = field(default_factory=lambda: [DEFAULT_MODE])
    # Primary mode for RERA/recovery-marker storage (plan step 4).
    primary_mode: str = DEFAULT_MODE


@dataclass
class AnalysisInputs:
    """Detached DTO carrying all DB-loaded inputs needed for analysis compute.

    Created by ``AnalysisService.load_session_inputs()``.  Contains only plain
    Python types and numpy arrays — no ORM objects, no live session references.
    Safe to pass across thread boundaries or between executor tasks.
    """

    session_id: int
    flow_timestamps: np.ndarray
    flow_values: np.ndarray
    sample_rate: float
    machine_events: list[AnalysisEvent]
    spo2_values: np.ndarray | None = None
    pulse_timestamps: np.ndarray | None = None
    pulse_values: np.ndarray | None = None
    # Quality-flag inputs: leak waveform (timestamps, values) for leak_valid derivation.
    leak_timestamps: np.ndarray | None = None
    leak_values: np.ndarray | None = None
    modes: list[str] = field(default_factory=lambda: [DEFAULT_MODE])
    # Primary mode for RERA/recovery-marker storage (plan step 4).
    primary_mode: str = DEFAULT_MODE


def _resolve_primary_mode(modes: list[str], primary_mode: str | None) -> str:
    """Resolve and validate primary_mode against modes list.

    Rules (plan step 4):
    - If primary_mode is supplied, it MUST be in modes → ValueError if not.
    - If not supplied and DEFAULT_MODE is in modes → DEFAULT_MODE.
    - If not supplied and DEFAULT_MODE is NOT in modes → ValueError (caller must
      supply primary_mode explicitly for non-default mode sets).
    """
    if primary_mode is not None:
        if primary_mode not in modes:
            raise ValueError(
                f"primary_mode {primary_mode!r} must be a member of modes {modes}"
            )
        return primary_mode
    # No primary_mode supplied: default only when DEFAULT_MODE is present.
    if DEFAULT_MODE in modes:
        return DEFAULT_MODE
    raise ValueError(
        f"primary_mode must be supplied explicitly when modes {modes!r} "
        f"exclude the DEFAULT_MODE {DEFAULT_MODE!r}"
    )


class AnalysisService:
    """
    Service for running programmatic analysis on CPAP sessions.

    This service handles:
    - Loading waveform data from database
    - Running the programmatic analysis engine
    - Storing results in the database
    - Providing structured results for consumption

    Two construction modes:
    - **DB mode**: ``AnalysisService(db_session, profile_id=n)`` — ``profile_id`` is
      required when a session is provided; all DB queries are scoped to that profile.
    - **Compute mode**: ``AnalysisService()`` — ``db_session=None``; only
      ``compute_analysis()`` and ``prepare_inputs()`` may be called.

    Example:
        >>> service = AnalysisService(db_session, profile_id=42)
        >>> result = await service.analyze_session(session_id=123)
        >>> print(f"AHI: {result['event_timeline']['ahi']}")
    """

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        profile_id: int | None = None,
        min_breath_duration: float = BSC.MIN_BREATH_DURATION,
        confidence_threshold: float = FLC.CONFIDENCE_THRESHOLD,
    ):
        """
        Initialize analysis service.

        Args:
            db_session: SQLAlchemy async database session.  Pass ``None`` for a
                compute-only instance (``compute_analysis``/``prepare_inputs`` only).
            profile_id: Required when ``db_session`` is provided.  All DB queries
                are scoped to this profile — sessions not owned by it raise or
                return ``None`` exactly as if missing.
            min_breath_duration: Minimum breath duration for segmentation (seconds)
            confidence_threshold: Minimum confidence for reliable findings
        """
        if db_session is not None and profile_id is None:
            raise ValueError(
                "AnalysisService requires profile_id when db_session is provided"
            )
        self.db_session = db_session
        self.profile_id = profile_id
        self.waveform_loader = (
            WaveformLoader(db_session) if db_session is not None else None
        )
        self.breath_segmenter = BreathSegmenter(min_breath_duration=min_breath_duration)
        self.feature_extractor = WaveformFeatureExtractor()
        self.flow_classifier = FlowLimitationClassifier(
            confidence_threshold=confidence_threshold
        )
        self.pattern_detector = ComplexPatternDetector()
        self.pulse_detector = PulseChangeDetector(
            bpm_threshold=PCC.BPM_THRESHOLD,
            duration_threshold=PCC.DURATION_THRESHOLD,
        )

    async def _load_machine_events(self, session_id: int) -> list[AnalysisEvent]:
        """
        Load machine-flagged events from database.

        Args:
            session_id: Database session ID

        Returns:
            List of respiratory events with session-relative timestamps
        """
        assert self.db_session is not None, "_load_machine_events requires a DB session"
        # Scope session lookup to this profile — consistent with load_session_inputs_raw.
        stmt = select(models.Session).where(models.Session.id == session_id)
        if self.profile_id is not None:
            stmt = stmt.join(
                models.Device, models.Session.device_id == models.Device.id
            ).where(models.Device.profile_id == self.profile_id)
        session = (await self.db_session.execute(stmt)).scalars().first()
        if not session:
            return []

        session_start_ts = session.start_time.timestamp()

        events = (
            (
                await self.db_session.execute(
                    select(models.Event)
                    .filter_by(session_id=session_id)
                    .order_by(models.Event.start_time)
                )
            )
            .scalars()
            .all()
        )

        respiratory_events = []
        for event in events:
            offset_seconds = event.start_time.timestamp() - session_start_ts

            respiratory_events.append(
                AnalysisEvent(
                    event_type=event.event_type,
                    start_time=offset_seconds,
                    duration=event.duration_seconds or 10.0,
                    source="machine",
                    confidence=1.0,
                )
            )

        return respiratory_events

    async def load_session_inputs(
        self,
        session_id: int,
        modes: list[str] | None = None,
    ) -> AnalysisInputs:
        """Load all DB inputs for a session and return them as a detached DTO.

        Convenience wrapper: calls ``load_session_inputs_raw()`` (DB I/O only)
        then ``prepare_inputs()`` (NumPy deserialization + artifact detection).
        Both phases run while the ORM session is still held.  Callers that need
        the session closed *before* NumPy work should call those two methods
        directly — see ``AnalysisFacade.run_analysis`` and the batch coordinator.

        Args:
            session_id: Database session ID
            modes: Detection modes to include in the DTO (None = default mode)

        Returns:
            ``AnalysisInputs`` DTO with copied numpy arrays.

        Raises:
            ValueError: If session not found or has no flow waveform data.
        """
        raw = await self.load_session_inputs_raw(session_id, modes=modes)
        return AnalysisService.prepare_inputs(raw)

    async def load_session_inputs_raw(
        self,
        session_id: int,
        modes: list[str] | None = None,
        primary_mode: str | None = None,
    ) -> RawSessionBlobs:
        """Fetch all DB inputs for a session as raw bytes — **I/O phase only**.

        No NumPy, no deserialization, no artifact detection.  The ORM session is
        NOT needed after this call returns.  Call ``AnalysisService.prepare_inputs()``
        outside the session scope to convert blobs to numpy arrays.

        Args:
            session_id: Database session ID.
            modes: Detection modes (``None`` = default mode).
            primary_mode: Mode whose recovery markers are persisted (must be in
                ``modes``; defaults to ``DEFAULT_MODE`` when it is present in
                ``modes``).

        Returns:
            ``RawSessionBlobs`` DTO with raw bytes and scalar metadata.

        Raises:
            ValueError: If session not found or has no flow waveform data, or
                if ``primary_mode`` is not a member of ``modes``.
        """
        assert self.db_session is not None, (
            "load_session_inputs_raw requires a DB session"
        )
        modes_list = list(modes) if modes is not None else [DEFAULT_MODE]
        resolved_primary = _resolve_primary_mode(modes_list, primary_mode)

        # Scope the session lookup to this profile so foreign IDs raise ValueError
        # rather than loading another profile's data.
        stmt = select(models.Session).where(models.Session.id == session_id)
        if self.profile_id is not None:
            stmt = stmt.join(
                models.Device, models.Session.device_id == models.Device.id
            ).where(models.Device.profile_id == self.profile_id)
        session = (await self.db_session.execute(stmt)).scalars().first()
        if not session:
            raise ValueError(f"Session {session_id} not found")

        try:
            flow_blob, flow_sample_count, flow_metadata = await fetch_waveform_blob(
                self.db_session, session_id, "flow"
            )
        except Exception as e:
            logger.error(f"Failed to load flow waveform for session {session_id}: {e}")
            raise ValueError(
                f"No flow waveform data available for session {session_id}"
            ) from e

        if flow_sample_count == 0:
            raise ValueError(f"Empty flow waveform data for session {session_id}")

        machine_events = await self._load_machine_events(session_id)

        spo2_blob: bytes | None = None
        spo2_sample_count = 0
        spo2_metadata: dict[str, Any] = {}
        try:
            spo2_blob, spo2_sample_count, spo2_metadata = await fetch_waveform_blob(
                self.db_session, session_id, "spo2"
            )
        except Exception as e:
            logger.info(f"No SpO2 data available: {e}")

        pulse_blob: bytes | None = None
        pulse_sample_count = 0
        pulse_metadata: dict[str, Any] = {}
        try:
            pulse_blob, pulse_sample_count, pulse_metadata = await fetch_waveform_blob(
                self.db_session, session_id, "pulse"
            )
        except Exception as e:
            logger.debug(f"Pulse waveform not available: {e}")

        # Leak channel for quality-flag derivation (plan step 3).
        leak_blob: bytes | None = None
        leak_sample_count = 0
        leak_metadata: dict[str, Any] = {}
        try:
            leak_blob, leak_sample_count, leak_metadata = await fetch_waveform_blob(
                self.db_session, session_id, "leak"
            )
        except Exception as e:
            logger.debug(f"Leak waveform not available: {e}")

        return RawSessionBlobs(
            session_id=session_id,
            flow_blob=flow_blob,
            flow_sample_count=flow_sample_count,
            flow_metadata=flow_metadata,
            machine_events=machine_events,
            spo2_blob=spo2_blob,
            spo2_sample_count=spo2_sample_count,
            spo2_metadata=spo2_metadata,
            pulse_blob=pulse_blob,
            pulse_sample_count=pulse_sample_count,
            pulse_metadata=pulse_metadata,
            leak_blob=leak_blob,
            leak_sample_count=leak_sample_count,
            leak_metadata=leak_metadata,
            modes=modes_list,
            primary_mode=resolved_primary,
        )

    @staticmethod
    def prepare_inputs(raw: RawSessionBlobs) -> AnalysisInputs:
        """Convert a ``RawSessionBlobs`` DTO into a compute-ready ``AnalysisInputs`` DTO.

        **Compute phase only — no DB access.**  Performs deserialization
        (``np.frombuffer``) and artifact detection (NumPy).  Safe to call after
        the ORM session has been closed.

        Args:
            raw: Raw blobs from ``load_session_inputs_raw()``.

        Returns:
            ``AnalysisInputs`` with copied numpy arrays.
        """
        # Deserialise flow waveform (compute — no session).
        flow_timestamps, flow_values = deserialize_waveform_blob(
            raw.flow_blob, raw.flow_sample_count
        )
        # Artifact detection (NumPy — no session).
        artifact_mask = detect_and_mark_artifacts(flow_values, "flow")
        _ = artifact_mask  # mask available to callers via metadata if needed

        sample_rate = float(raw.flow_metadata.get("sample_rate", 25.0))

        spo2_values: np.ndarray | None = None
        if raw.spo2_blob is not None and raw.spo2_sample_count > 0:
            try:
                _, spo2_raw = deserialize_waveform_blob(
                    raw.spo2_blob, raw.spo2_sample_count
                )
                if len(spo2_raw) > 0 and len(spo2_raw) == len(flow_timestamps):
                    spo2_values = spo2_raw.copy()
                elif len(spo2_raw) > 0:
                    logger.warning(
                        f"SpO2 length mismatch ({len(spo2_raw)} vs {len(flow_timestamps)}), skipping"
                    )
            except Exception as e:
                logger.warning(f"Failed to deserialise SpO2 blob: {e}")

        pulse_timestamps: np.ndarray | None = None
        pulse_values: np.ndarray | None = None
        if raw.pulse_blob is not None and raw.pulse_sample_count > 0:
            try:
                pt, pv = deserialize_waveform_blob(
                    raw.pulse_blob, raw.pulse_sample_count
                )
                pulse_timestamps = pt.copy()
                pulse_values = pv.copy()
            except Exception as e:
                logger.debug(f"Failed to deserialise pulse blob: {e}")

        # Leak waveform for quality-flag derivation.
        leak_timestamps: np.ndarray | None = None
        leak_values: np.ndarray | None = None
        if raw.leak_blob is not None and raw.leak_sample_count > 0:
            try:
                lt, lv = deserialize_waveform_blob(raw.leak_blob, raw.leak_sample_count)
                leak_timestamps = lt.copy()
                leak_values = lv.copy()
            except Exception as e:
                logger.debug(f"Failed to deserialise leak blob: {e}")

        return AnalysisInputs(
            session_id=raw.session_id,
            flow_timestamps=flow_timestamps.copy(),
            flow_values=flow_values.copy(),
            sample_rate=sample_rate,
            machine_events=raw.machine_events,
            spo2_values=spo2_values,
            pulse_timestamps=pulse_timestamps,
            pulse_values=pulse_values,
            leak_timestamps=leak_timestamps,
            leak_values=leak_values,
            modes=raw.modes,
            primary_mode=raw.primary_mode,
        )

    def compute_analysis(self, inputs: AnalysisInputs) -> AnalysisComputation:
        """Run pure analysis compute on a detached ``AnalysisInputs`` DTO.

        **No database access** — all inputs come from the DTO.  Safe to call
        after the ORM session has been closed.

        Returns an ``AnalysisComputation`` private envelope containing:
        - ``summary``: the public ``AnalysisResult`` (stored in
          ``programmatic_result_json``) — unchanged shape, no breath rows.
        - ``breaths``: per-breath ``ComputedBreath`` list (persisted as
          ``models.Breath`` children of the ``AnalysisResult`` row).

        Breaths NEVER enter ``summary``; ``programmatic_result_json`` stays
        exactly the same size as before.

        Args:
            inputs: Detached DTO from ``load_session_inputs()``.

        Returns:
            ``AnalysisComputation`` envelope.

        Raises:
            ValueError: If no breaths can be segmented.
        """
        timestamps = inputs.flow_timestamps
        flow_values = inputs.flow_values
        sample_rate = inputs.sample_rate
        session_id = inputs.session_id
        primary_mode = inputs.primary_mode

        session_duration_hours = len(timestamps) / sample_rate / 3600
        logger.info(
            f"Loaded {len(timestamps)} flow samples at {sample_rate}Hz "
            f"({session_duration_hours:.1f} hours)"
        )
        logger.info(f"Loaded {len(inputs.machine_events)} machine-flagged events")

        breaths = self.breath_segmenter.segment_breaths(
            timestamps, flow_values, sample_rate
        )
        logger.info(f"Segmented {len(breaths)} breaths")

        if not breaths:
            raise ValueError(f"No breaths segmented for session {session_id}")

        breath_features = []
        for breath in breaths:
            breath_start_idx = np.searchsorted(timestamps, breath.start_time)
            breath_end_idx = np.searchsorted(timestamps, breath.end_time)

            breath_flow = flow_values[breath_start_idx:breath_end_idx]
            insp_flow = breath_flow[breath_flow > 0]

            if len(insp_flow) > 10:
                shape = self.feature_extractor.extract_shape_features(
                    insp_flow, sample_rate
                )
                peaks = self.feature_extractor.extract_peak_features(
                    insp_flow, sample_rate
                )
                breath_features.append((breath.breath_number, shape, peaks))

        flow_analysis = self.flow_classifier.analyze_session(breath_features)
        logger.info(f"Flow limitation index: {flow_analysis.flow_limitation_index:.3f}")

        # Map breath_number → FlowPattern for per-breath ComputedBreath assembly.
        flow_pattern_by_number: dict[int, Any] = {
            p.breath_number: p for p in flow_analysis.patterns
        }

        tidal_volumes = np.array([b.tidal_volume for b in breaths])
        breath_timestamps = np.array([b.start_time for b in breaths])
        respiratory_rates = np.array([b.respiratory_rate_rolling for b in breaths])

        csr_detection = self.pattern_detector.detect_csr(
            breath_timestamps, tidal_volumes, window_minutes=10.0
        )

        csr_episodes_list = self.pattern_detector.detect_csr_episodes(
            breath_timestamps,
            tidal_volumes,
            window_minutes=PDC.CSR_WINDOW_MINUTES,
            step_minutes=PDC.CSR_WINDOW_STEP_MINUTES,
        )

        periodic_breathing = self.pattern_detector.detect_periodic_breathing(
            breath_timestamps, tidal_volumes, respiratory_rates
        )

        pb_episodes_list = self.pattern_detector.detect_periodic_breathing_episodes(
            breath_timestamps,
            tidal_volumes,
            respiratory_rates,
            window_minutes=PDC.CSR_WINDOW_MINUTES,
            step_minutes=PDC.CSR_WINDOW_STEP_MINUTES,
        )

        pulse_change_count = None
        pulse_change_index = None
        if inputs.pulse_timestamps is not None and inputs.pulse_values is not None:
            try:
                pulse_events = self.pulse_detector.detect(
                    inputs.pulse_timestamps, inputs.pulse_values
                )
                pulse_change_count = len(pulse_events)
                pulse_change_index = (
                    pulse_change_count / session_duration_hours
                    if session_duration_hours > 0
                    else 0.0
                )
                logger.info(
                    f"Pulse changes: {pulse_change_count} total, "
                    f"{pulse_change_index:.1f} per hour"
                )
            except Exception as e:
                logger.debug(f"Pulse detection failed: {e}")

        mode_results = {}
        for mode_name in inputs.modes:
            try:
                mode = get_mode(mode_name)
                mode_result = mode.detect_events(
                    breaths=breaths,
                    flow_data=(timestamps, flow_values),
                    sample_rate=sample_rate,
                    session_duration_hours=session_duration_hours,
                )
                mode_results[mode_name] = mode_result
                logger.info(
                    f"Mode '{mode_name}': Detected {len(mode_result.apneas)} apneas, "
                    f"{len(mode_result.hypopneas)} hypopneas, AHI={mode_result.ahi:.1f}"
                )
            except Exception as e:
                logger.error(f"Failed to run mode '{mode_name}': {e}")
                continue

        summary = AnalysisResult(
            session_id=session_id,
            session_duration_hours=session_duration_hours,
            total_breaths=len(breaths),
            machine_events=inputs.machine_events,
            mode_results=mode_results,
            flow_analysis=flow_analysis.model_dump(),
            csr_detection=csr_detection.model_dump() if csr_detection else None,
            periodic_breathing=periodic_breathing.model_dump()
            if periodic_breathing
            else None,
            csr_episodes=[ep.model_dump() for ep in csr_episodes_list],
            periodic_breathing_episodes=[ep.model_dump() for ep in pb_episodes_list],
            pulse_change_count=pulse_change_count,
            pulse_change_index=pulse_change_index,
            timestamp_start=float(timestamps[0]) if len(timestamps) > 0 else 0.0,
            timestamp_end=float(timestamps[-1]) if len(timestamps) > 0 else 0.0,
        )

        # Build per-breath ComputedBreath objects (plan step 5).
        # Recovery breaths are sourced from the primary mode's RERA detector only.
        recovery_breath_indices = _collect_recovery_breath_indices(
            breaths, mode_results.get(primary_mode)
        )

        computed_breaths = _build_computed_breaths(
            breaths=breaths,
            timestamps=timestamps,
            flow_values=flow_values,
            flow_pattern_by_number=flow_pattern_by_number,
            recovery_breath_indices=recovery_breath_indices,
            leak_timestamps=inputs.leak_timestamps,
            leak_values=inputs.leak_values,
        )

        return AnalysisComputation(
            summary=summary, breaths=computed_breaths, primary_mode=primary_mode
        )

    async def analyze_session(
        self,
        session_id: int,
        modes: list[str] | None = None,
        store_results: bool = True,
    ) -> AnalysisResult:
        """Analyze session with specified detection mode(s).

        Uses the same three-phase pipeline as batch analysis:

        1. **I/O phase** (``load_session_inputs_raw``): DB reads only — raw blobs
           and scalar metadata.  ORM session not needed after this returns.
        2. **Compute phase** (``prepare_inputs`` + ``compute_analysis``): NumPy /
           scipy work on detached data.  No session held.
        3. **Write phase** (``store_result``): short INSERT via the injected session.

        Args:
            session_id: Database session ID
            modes: Detection modes to run (None = default mode)
            store_results: Whether to persist results

        Returns:
            AnalysisResult with results from all modes

        Raises:
            ValueError: If session not found or has no waveform data
        """
        modes_list = list(modes) if modes is not None else [DEFAULT_MODE]
        logger.info(
            f"Starting analysis for session {session_id} with modes: {modes_list}"
        )
        start_time = time.time()

        # I/O phase: fetch raw blobs while the session is held.
        raw = await self.load_session_inputs_raw(session_id, modes=modes_list)
        # Compute phase: deserialization + NumPy work — no session needed.
        inputs = AnalysisService.prepare_inputs(raw)
        computation = self.compute_analysis(inputs)

        processing_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"Analysis complete in {processing_time_ms}ms")

        if store_results:
            await self.store_result(computation, processing_time_ms)

        return computation.summary

    async def get_analysis_result(self, session_id: int) -> AnalysisResult | None:
        """
        Retrieve stored analysis result for a session.

        Scoped to this profile — returns None for foreign session IDs (treats
        "not owned" and "not analyzed" identically to avoid oracle attacks).

        Args:
            session_id: Database session ID

        Returns:
            AnalysisResult dataclass or None if not found
        """
        assert self.db_session is not None, "get_analysis_result requires a DB session"
        # Validate ownership before fetching analysis rows.
        if self.profile_id is not None:
            owned = (
                await self.db_session.execute(
                    select(models.Session.id)
                    .join(models.Device, models.Session.device_id == models.Device.id)
                    .where(
                        models.Session.id == session_id,
                        models.Device.profile_id == self.profile_id,
                    )
                )
            ).scalar_one_or_none()
            if owned is None:
                return None

        analysis = (
            (
                await self.db_session.execute(
                    select(models.AnalysisResult)
                    .filter_by(session_id=session_id)
                    .order_by(models.AnalysisResult.created_at.desc())
                )
            )
            .scalars()
            .first()
        )

        if not analysis:
            return None

        return AnalysisResult.model_validate(analysis.programmatic_result_json)

    async def store_result(
        self, computation: AnalysisComputation, processing_time_ms: int
    ) -> int:
        """Store analysis result + breath children to database atomically.

        Public write seam — callers (including ``BatchAnalysisCoordinator``) use
        this method so the write phase is not tied to a private implementation detail.

        Enforces profile ownership: raises ``NotFoundError`` when the target
        session does not belong to ``self.profile_id``, preventing cross-profile
        writes.

        Persistence contract (plan step 2):
        - ``AnalysisResult`` parent row is added and flushed to assign its PK.
        - ``Breath`` children are added with the flushed parent ID in one transaction.
        - ``programmatic_result_json`` contains only the public ``AnalysisResult``
          summary — breaths are NEVER stored there (plan step 5, no duplication).
        - ``engine_versions_json`` uses the nested
          ``{"identity": ..., "run": ...}`` shape (plan step 4, §14 note 5).

        Args:
            computation: AnalysisComputation envelope (summary + breaths).
            processing_time_ms: Processing time in milliseconds.

        Returns:
            Database analysis result ID.

        Raises:
            NotFoundError: If the session does not belong to this profile.
        """
        from snore.exceptions import NotFoundError as _NotFoundError  # noqa: PLC0415

        assert self.db_session is not None, "store_result requires a DB session"

        result = computation.summary

        # Verify the target session is owned by this profile before writing.
        owned = (
            await self.db_session.execute(
                select(models.Session.id)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    models.Session.id == result.session_id,
                    models.Device.profile_id == self.profile_id,
                )
            )
        ).scalar_one_or_none()
        if owned is None:
            raise _NotFoundError(
                f"Session {result.session_id} not found or not owned by profile {self.profile_id}"
            )

        # Build the nested engine_versions_json (plan step 4 / §14 note 5).
        mode_keys = list(result.mode_results.keys())
        identity = AlgorithmIdentity.current()
        run_metadata = AnalysisRunMetadata(
            primary_mode=computation.primary_mode,
            modes=mode_keys,
        )
        algo_versions = AlgoVersions(identity=identity, run=run_metadata)

        analysis = models.AnalysisResult(
            session_id=result.session_id,
            timestamp_start=datetime.fromtimestamp(result.timestamp_start),
            timestamp_end=datetime.fromtimestamp(result.timestamp_end),
            programmatic_result_json=result.model_dump(),
            processing_time_ms=processing_time_ms,
            engine_versions_json=algo_versions.model_dump(),
        )

        self.db_session.add(analysis)
        # Flush to assign analysis.id so Breath children can reference it.
        await self.db_session.flush()

        # Persist breath children atomically in the same transaction (plan step 2).
        if computation.breaths:
            breath_rows = [
                models.Breath(
                    analysis_result_id=analysis.id,
                    session_id=result.session_id,
                    breath_number=cb.breath_number,
                    start_offset_s=cb.start_offset_s,
                    end_offset_s=cb.end_offset_s,
                    inspiration_time_s=cb.inspiration_time_s,
                    expiration_time_s=cb.expiration_time_s,
                    total_time_s=cb.total_time_s,
                    i_e_ratio=cb.i_e_ratio,
                    duty_cycle=cb.duty_cycle,
                    peak_flow_lpm=cb.peak_flow_lpm,
                    tidal_volume_ml=cb.tidal_volume_ml,
                    respiratory_rate_rolling=cb.respiratory_rate_rolling,
                    flatness_index=cb.flatness_index,
                    mid_insp_flattening=cb.mid_insp_flattening,
                    flow_class=cb.flow_class,
                    flow_confidence=cb.flow_confidence,
                    is_recovery_breath=cb.is_recovery_breath,
                    inferred_trigger_type=cb.inferred_trigger_type,
                    trigger_confidence=cb.trigger_confidence,
                    inferred_cycle_type=cb.inferred_cycle_type,
                    cycle_confidence=cb.cycle_confidence,
                    trigger_cycle_applicable=cb.trigger_cycle_applicable,
                    trigger_cycle_reason=cb.trigger_cycle_reason,
                    leak_valid=cb.leak_valid,
                    leak_valid_reason=cb.leak_valid_reason,
                    ramp_active=cb.ramp_active,
                    ramp_active_reason=cb.ramp_active_reason,
                    mask_off=cb.mask_off,
                    mask_off_reason=cb.mask_off_reason,
                )
                for cb in computation.breaths
            ]
            self.db_session.add_all(breath_rows)

        logger.info(
            f"Stored analysis result {analysis.id} with {len(computation.breaths)} breath rows"
        )
        return analysis.id


# ---------------------------------------------------------------------------
# Module-level compute helpers (private to this module)
# ---------------------------------------------------------------------------


def _collect_recovery_breath_indices(
    breaths: list[Any],
    primary_mode_result: Any | None,
) -> set[int]:
    """Return breath_numbers that are recovery breaths in the primary mode's RERAs.

    A recovery breath ends a RERA — it is the last breath in the sequence,
    identified by end_time proximity to the RERA's end_time.
    """
    if primary_mode_result is None:
        return set()
    recovery_numbers: set[int] = set()
    for rera in getattr(primary_mode_result, "reras", []):
        for b in breaths:
            if abs(b.end_time - rera.end_time) < 0.5:
                recovery_numbers.add(b.breath_number)
    return recovery_numbers


def _build_computed_breaths(
    *,
    breaths: list[Any],
    timestamps: Any,
    flow_values: Any,
    flow_pattern_by_number: dict[int, Any],
    recovery_breath_indices: set[int],
    leak_timestamps: Any | None,
    leak_values: Any | None,
) -> list[ComputedBreath]:
    """Build the list of ComputedBreath for one session's analysis.

    Args:
        breaths: List of BreathMetrics from the segmenter.
        timestamps: Flow timestamps array (unused here; kept for signature symmetry).
        flow_values: Full session flow array — used to extract per-breath insp flow.
        flow_pattern_by_number: Maps breath_number → FlowPattern (from classifier).
        recovery_breath_indices: Set of breath_numbers identified as recovery breaths.
        leak_timestamps: Leak waveform timestamps (may be None).
        leak_values: Leak waveform values (may be None).

    Returns:
        list of ComputedBreath (same length as breaths).
    """
    from snore.analysis.shared.feature_extractors import (  # noqa: PLC0415
        WaveformFeatureExtractor,
        compute_mid_insp_flattening,
    )
    from snore.analysis.shared.trigger_cycle import infer_trigger_cycle  # noqa: PLC0415

    extractor = WaveformFeatureExtractor()
    sample_rate: float = (
        25.0  # default; the exact rate doesn't affect per-breath slicing
    )

    computed: list[ComputedBreath] = []
    for idx, breath in enumerate(breaths):
        # Slice the inspiratory flow for shape features.
        b_start = np.searchsorted(timestamps, breath.start_time)
        b_end = np.searchsorted(timestamps, breath.end_time)
        breath_flow = flow_values[b_start:b_end]
        insp_flow: np.ndarray = breath_flow[breath_flow > 0]

        flatness_idx: float | None = None
        mid_insp: float | None = None
        if len(insp_flow) > 10:
            shape = extractor.extract_shape_features(insp_flow, sample_rate)
            flatness_idx = shape.flatness_index
            mid_insp = compute_mid_insp_flattening(insp_flow)

        fl = flow_pattern_by_number.get(breath.breath_number)
        flow_cls = fl.flow_class if fl is not None else None
        flow_conf = fl.confidence if fl is not None else None

        gap_before: float | None = None
        if idx > 0:
            gap_before = breath.start_time - breaths[idx - 1].end_time

        insp_arr = insp_flow if len(insp_flow) > 0 else None
        tc = infer_trigger_cycle(
            inspiration_time_s=breath.inspiration_time,
            gap_before_s=gap_before,
            insp_flow_array=insp_arr,
            vendor_applicability="validated",
        )

        leak_valid, leak_valid_reason = _compute_leak_valid(
            breath_start=breath.start_time,
            breath_end=breath.end_time,
            leak_timestamps=leak_timestamps,
            leak_values=leak_values,
        )

        duty_cycle: float | None = None
        if (
            breath.duration is not None
            and breath.duration > 0
            and breath.inspiration_time is not None
        ):
            duty_cycle = float(breath.inspiration_time / breath.duration)

        computed.append(
            ComputedBreath(
                breath_number=breath.breath_number,
                start_offset_s=float(breath.start_time),
                end_offset_s=float(breath.end_time),
                inspiration_time_s=(
                    float(breath.inspiration_time)
                    if breath.inspiration_time is not None
                    else None
                ),
                expiration_time_s=(
                    float(breath.expiration_time)
                    if breath.expiration_time is not None
                    else None
                ),
                total_time_s=float(breath.duration),
                i_e_ratio=float(breath.i_e_ratio),
                duty_cycle=duty_cycle,
                peak_flow_lpm=float(breath.peak_inspiratory_flow),
                tidal_volume_ml=float(breath.tidal_volume),
                respiratory_rate_rolling=float(breath.respiratory_rate_rolling),
                flatness_index=flatness_idx,
                mid_insp_flattening=mid_insp,
                flow_class=flow_cls,
                flow_confidence=flow_conf,
                is_recovery_breath=(breath.breath_number in recovery_breath_indices),
                inferred_trigger_type=tc.inferred_trigger_type,
                trigger_confidence=tc.trigger_confidence,
                inferred_cycle_type=tc.inferred_cycle_type,
                cycle_confidence=tc.cycle_confidence,
                trigger_cycle_applicable=tc.trigger_cycle_applicable,
                trigger_cycle_reason=tc.trigger_cycle_reason,
                leak_valid=leak_valid,
                leak_valid_reason=leak_valid_reason,
                ramp_active=None,
                ramp_active_reason="not_available",
                mask_off=None,
                mask_off_reason="not_available",
            )
        )
    return computed


def _compute_leak_valid(
    *,
    breath_start: float,
    breath_end: float,
    leak_timestamps: Any | None,
    leak_values: Any | None,
) -> tuple[bool | None, str | None]:
    """Derive the ``leak_valid`` quality flag for one breath.

    Logic (plan step 3, v1 spec):
    - No leak channel present  →  (None, "channel_absent")
    - Overlapping samples found →  mean of overlapping samples < threshold
    - No overlap, nearest neighbour ≤ 5 s away  →  that sample's value < threshold
    - No overlap, nearest neighbour > 5 s away  →  (None, "channel_unaligned")

    Returns:
        (leak_valid: bool | None, reason: str | None)
    """

    if leak_timestamps is None or leak_values is None or len(leak_timestamps) == 0:
        return None, "channel_absent"

    # Find overlapping samples: leak_timestamps within [breath_start, breath_end).
    mask = (leak_timestamps >= breath_start) & (leak_timestamps < breath_end)
    if mask.any():
        mean_leak = float(np.mean(leak_values[mask]))
        return mean_leak < LEAK_VALID_THRESHOLD_LPM, None

    # No overlap — nearest neighbour.
    breath_mid = (breath_start + breath_end) / 2.0
    dists = np.abs(leak_timestamps - breath_mid)
    nearest_idx = int(np.argmin(dists))
    nearest_dist = float(dists[nearest_idx])

    if nearest_dist <= LEAK_VALID_MAX_ALIGNMENT_GAP_S:
        leak_sample = float(leak_values[nearest_idx])
        return leak_sample < LEAK_VALID_THRESHOLD_LPM, None

    return None, "channel_unaligned"
