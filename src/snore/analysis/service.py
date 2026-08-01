"""
Analysis service for orchestrating programmatic analysis.

This module provides the main interface for running comprehensive CPAP session
analysis, loading data from the database, and storing results.

I/O–compute separation (§7)
-----------------------------
``AnalysisService.load_session_inputs()`` performs **only** database reads,
returning a plain ``AnalysisInputs`` dataclass.  The ORM session is closed before
any numpy/scipy work begins.

``AnalysisService.compute_analysis()`` is pure Python/numpy — it takes an
``AnalysisInputs`` DTO and returns an ``AnalysisResult``.  No session is held.

``AnalysisService.analyze_session()`` is the convenience wrapper that calls
``load_session_inputs`` then ``compute_analysis`` in one call (used by
single-session paths where session lifetime is already bounded by the request
context).  Batch analysis (``BatchAnalysisCoordinator``) calls the two phases
separately so the session is released before compute.
"""

import logging
import time

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from sqlalchemy import select
from sqlalchemy.orm import Session

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
from snore.analysis.types import AnalysisEvent, AnalysisResult
from snore.constants import BreathSegmentationConstants as BSC
from snore.constants import FlowLimitationConstants as FLC
from snore.constants import PatternDetectionConstants as PDC
from snore.constants import PulseChangeConstants as PCC
from snore.database import models

logger = logging.getLogger(__name__)

__all__ = ["AnalysisInputs", "RawSessionBlobs", "AnalysisService", "AnalysisResult"]


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
    modes: list[str] = field(default_factory=lambda: [DEFAULT_MODE])


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
    modes: list[str] = field(default_factory=lambda: [DEFAULT_MODE])


class AnalysisService:
    """
    Service for running programmatic analysis on CPAP sessions.

    This service handles:
    - Loading waveform data from database
    - Running the programmatic analysis engine
    - Storing results in the database
    - Providing structured results for consumption

    Pass ``db_session=None`` to construct a compute-only instance: only
    ``compute_analysis()`` and ``prepare_inputs()`` may be called — any method
    that accesses the DB will raise.  Use this to avoid the
    ``object.__new__()`` escape hatch previously required.

    Example:
        >>> service = AnalysisService(db_session)
        >>> result = service.analyze_session(session_id=123)
        >>> print(f"AHI: {result['event_timeline']['ahi']}")
    """

    def __init__(
        self,
        db_session: Session | None = None,
        min_breath_duration: float = BSC.MIN_BREATH_DURATION,
        confidence_threshold: float = FLC.CONFIDENCE_THRESHOLD,
    ):
        """
        Initialize analysis service.

        Args:
            db_session: SQLAlchemy database session
            min_breath_duration: Minimum breath duration for segmentation (seconds)
            confidence_threshold: Minimum confidence for reliable findings
        """
        self.db_session = db_session
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

    @classmethod
    def _make_compute_only(
        cls,
        min_breath_duration: float = BSC.MIN_BREATH_DURATION,
        confidence_threshold: float = FLC.CONFIDENCE_THRESHOLD,
    ) -> "AnalysisService":
        """Construct a compute-only instance (no DB session).

        Deprecated in favour of ``AnalysisService()`` (no args) which now
        accepts ``db_session=None`` directly.  Kept for any callers that
        haven't migrated; delegates to the normal constructor so no
        ``object.__new__()`` escape hatch is needed.
        """
        return cls(
            db_session=None,
            min_breath_duration=min_breath_duration,
            confidence_threshold=confidence_threshold,
        )

    def _load_machine_events(self, session_id: int) -> list[AnalysisEvent]:
        """
        Load machine-flagged events from database.

        Args:
            session_id: Database session ID

        Returns:
            List of respiratory events with session-relative timestamps
        """
        assert self.db_session is not None, "_load_machine_events requires a DB session"
        session = (
            self.db_session.execute(select(models.Session).filter_by(id=session_id))
            .scalars()
            .first()
        )
        if not session:
            return []

        session_start_ts = session.start_time.timestamp()

        events = (
            self.db_session.execute(
                select(models.Event)
                .filter_by(session_id=session_id)
                .order_by(models.Event.start_time)
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

    def load_session_inputs(
        self,
        session_id: int,
        modes: list[str] | None = None,
    ) -> AnalysisInputs:
        """Load all DB inputs for a session and return them as a detached DTO.

        **I/O only** — all work done here is database reads.  No numpy/scipy
        computation is performed.  The caller is expected to close the ORM
        session immediately after this call so that compute runs without a
        held session.

        Internally calls ``load_session_inputs_raw()`` (pure I/O) and then
        ``prepare_inputs()`` (pure compute) so that single-session callers get a
        ready-to-use ``AnalysisInputs`` DTO in one call.  Batch callers that want
        the session closed between I/O and compute should call those two methods
        separately — see ``BatchAnalysisCoordinator.analyze_one``.

        Args:
            session_id: Database session ID
            modes: Detection modes to include in the DTO (None = default mode)

        Returns:
            ``AnalysisInputs`` DTO with copied numpy arrays.

        Raises:
            ValueError: If session not found or has no flow waveform data.
        """
        raw = self.load_session_inputs_raw(session_id, modes=modes)
        return AnalysisService.prepare_inputs(raw)

    def load_session_inputs_raw(
        self,
        session_id: int,
        modes: list[str] | None = None,
    ) -> RawSessionBlobs:
        """Fetch all DB inputs for a session as raw bytes — **I/O phase only**.

        No NumPy, no deserialization, no artifact detection.  The ORM session is
        NOT needed after this call returns.  Call ``AnalysisService.prepare_inputs()``
        outside the session scope to convert blobs to numpy arrays.

        Args:
            session_id: Database session ID.
            modes: Detection modes (``None`` = default mode).

        Returns:
            ``RawSessionBlobs`` DTO with raw bytes and scalar metadata.

        Raises:
            ValueError: If session not found or has no flow waveform data.
        """
        assert self.db_session is not None, (
            "load_session_inputs_raw requires a DB session"
        )
        modes_list = list(modes) if modes is not None else [DEFAULT_MODE]

        session = (
            self.db_session.execute(select(models.Session).filter_by(id=session_id))
            .scalars()
            .first()
        )
        if not session:
            raise ValueError(f"Session {session_id} not found")

        try:
            flow_blob, flow_sample_count, flow_metadata = fetch_waveform_blob(
                self.db_session, session_id, "flow"
            )
        except Exception as e:
            logger.error(f"Failed to load flow waveform for session {session_id}: {e}")
            raise ValueError(
                f"No flow waveform data available for session {session_id}"
            ) from e

        if flow_sample_count == 0:
            raise ValueError(f"Empty flow waveform data for session {session_id}")

        machine_events = self._load_machine_events(session_id)

        spo2_blob: bytes | None = None
        spo2_sample_count = 0
        spo2_metadata: dict[str, Any] = {}
        try:
            spo2_blob, spo2_sample_count, spo2_metadata = fetch_waveform_blob(
                self.db_session, session_id, "spo2"
            )
        except Exception as e:
            logger.info(f"No SpO2 data available: {e}")

        pulse_blob: bytes | None = None
        pulse_sample_count = 0
        pulse_metadata: dict[str, Any] = {}
        try:
            pulse_blob, pulse_sample_count, pulse_metadata = fetch_waveform_blob(
                self.db_session, session_id, "pulse"
            )
        except Exception as e:
            logger.debug(f"Pulse waveform not available: {e}")

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
            modes=modes_list,
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

        return AnalysisInputs(
            session_id=raw.session_id,
            flow_timestamps=flow_timestamps.copy(),
            flow_values=flow_values.copy(),
            sample_rate=sample_rate,
            machine_events=raw.machine_events,
            spo2_values=spo2_values,
            pulse_timestamps=pulse_timestamps,
            pulse_values=pulse_values,
            modes=raw.modes,
        )

    def compute_analysis(self, inputs: AnalysisInputs) -> AnalysisResult:
        """Run pure analysis compute on a detached ``AnalysisInputs`` DTO.

        **No database access** — all inputs come from the DTO.  Safe to call
        after the ORM session has been closed.

        Args:
            inputs: Detached DTO from ``load_session_inputs()``.

        Returns:
            ``AnalysisResult`` (Pydantic model).

        Raises:
            ValueError: If no breaths can be segmented.
        """
        timestamps = inputs.flow_timestamps
        flow_values = inputs.flow_values
        sample_rate = inputs.sample_rate
        session_id = inputs.session_id

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

        return AnalysisResult(
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

    def analyze_session(
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
        raw = self.load_session_inputs_raw(session_id, modes=modes_list)
        # Compute phase: deserialization + NumPy work — no session needed.
        inputs = AnalysisService.prepare_inputs(raw)
        result = self.compute_analysis(inputs)

        processing_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"Analysis complete in {processing_time_ms}ms")

        if store_results:
            self.store_result(result, processing_time_ms)

        return result

    def get_analysis_result(self, session_id: int) -> AnalysisResult | None:
        """
        Retrieve stored analysis result for a session.

        Args:
            session_id: Database session ID

        Returns:
            AnalysisResult dataclass or None if not found
        """
        assert self.db_session is not None, "get_analysis_result requires a DB session"
        analysis = (
            self.db_session.execute(
                select(models.AnalysisResult)
                .filter_by(session_id=session_id)
                .order_by(models.AnalysisResult.created_at.desc())
            )
            .scalars()
            .first()
        )

        if not analysis:
            return None

        return AnalysisResult.model_validate(analysis.programmatic_result_json)

    def _store_result(self, result: AnalysisResult, processing_time_ms: int) -> int:
        """Store analysis result to database.

        Args:
            result: Analysis result to store
            processing_time_ms: Processing time in milliseconds

        Returns:
            Database analysis result ID
        """
        return self.store_result(result, processing_time_ms)

    def store_result(self, result: AnalysisResult, processing_time_ms: int) -> int:
        """
        Store analysis result to database.

        Public write seam — callers (including ``BatchAnalysisCoordinator``) use
        this method so the write phase is not tied to a private implementation detail.

        Args:
            result: Analysis result to store
            processing_time_ms: Processing time in milliseconds

        Returns:
            Database analysis result ID
        """
        assert self.db_session is not None, "store_result requires a DB session"
        analysis = models.AnalysisResult(
            session_id=result.session_id,
            timestamp_start=datetime.fromtimestamp(result.timestamp_start),
            timestamp_end=datetime.fromtimestamp(result.timestamp_end),
            programmatic_result_json=result.model_dump(),
            processing_time_ms=processing_time_ms,
            engine_versions_json={
                "format_version": 2,
                "modes": list(result.mode_results.keys()),
            },
        )

        self.db_session.add(analysis)

        logger.info(f"Stored analysis result with ID {analysis.id}")
        return analysis.id
