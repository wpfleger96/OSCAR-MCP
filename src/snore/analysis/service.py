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

import numpy as np

from sqlalchemy import select
from sqlalchemy.orm import Session

from snore.analysis.data.waveform_loader import WaveformLoader
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

__all__ = ["AnalysisInputs", "AnalysisService", "AnalysisResult"]


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

    Example:
        >>> service = AnalysisService(db_session)
        >>> result = service.analyze_session(session_id=123)
        >>> print(f"AHI: {result['event_timeline']['ahi']}")
    """

    def __init__(
        self,
        db_session: Session,
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
        self.waveform_loader = WaveformLoader(db_session)
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
        """Create an ``AnalysisService`` instance that has no DB session.

        Only ``compute_analysis()`` may be called on the returned instance.
        Attempting ``load_session_inputs()`` or ``analyze_session()`` will fail
        because ``db_session`` is ``None``.  Used by ``BatchAnalysisCoordinator``
        to run pure compute after the read session has been closed.
        """
        obj = object.__new__(cls)
        obj.db_session = None  # type: ignore[assignment]
        obj.waveform_loader = None  # type: ignore[assignment]
        obj.breath_segmenter = BreathSegmenter(min_breath_duration=min_breath_duration)
        obj.feature_extractor = WaveformFeatureExtractor()
        obj.flow_classifier = FlowLimitationClassifier(
            confidence_threshold=confidence_threshold
        )
        obj.pattern_detector = ComplexPatternDetector()
        obj.pulse_detector = PulseChangeDetector(
            bpm_threshold=PCC.BPM_THRESHOLD,
            duration_threshold=PCC.DURATION_THRESHOLD,
        )
        return obj

    def _load_machine_events(self, session_id: int) -> list[AnalysisEvent]:
        """
        Load machine-flagged events from database.

        Args:
            session_id: Database session ID

        Returns:
            List of respiratory events with session-relative timestamps
        """
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

        Args:
            session_id: Database session ID
            modes: Detection modes to include in the DTO (None = default mode)

        Returns:
            ``AnalysisInputs`` DTO with copied numpy arrays.

        Raises:
            ValueError: If session not found or has no flow waveform data.
        """
        modes_list = list(modes) if modes is not None else [DEFAULT_MODE]

        session = (
            self.db_session.execute(select(models.Session).filter_by(id=session_id))
            .scalars()
            .first()
        )
        if not session:
            raise ValueError(f"Session {session_id} not found")

        try:
            timestamps, flow_values, metadata = self.waveform_loader.load_waveform(
                session_id=session_id, waveform_type="flow", apply_filter=False
            )
        except Exception as e:
            logger.error(f"Failed to load flow waveform for session {session_id}: {e}")
            raise ValueError(
                f"No flow waveform data available for session {session_id}"
            ) from e

        if len(timestamps) == 0:
            raise ValueError(f"Empty flow waveform data for session {session_id}")

        sample_rate = float(metadata.get("sample_rate", 25.0))

        machine_events = self._load_machine_events(session_id)

        spo2_values: np.ndarray | None = None
        try:
            _, spo2_raw, _ = self.waveform_loader.load_waveform(
                session_id=session_id, waveform_type="spo2", apply_filter=False
            )
            if len(spo2_raw) > 0 and len(spo2_raw) == len(timestamps):
                spo2_values = spo2_raw.copy()
            elif len(spo2_raw) > 0:
                logger.warning(
                    f"SpO2 length mismatch ({len(spo2_raw)} vs {len(timestamps)}), skipping"
                )
        except Exception as e:
            logger.info(f"No SpO2 data available: {e}")

        pulse_timestamps: np.ndarray | None = None
        pulse_values: np.ndarray | None = None
        try:
            pt, pv, _ = self.waveform_loader.load_waveform(
                session_id=session_id, waveform_type="pulse", apply_filter=False
            )
            pulse_timestamps = pt.copy()
            pulse_values = pv.copy()
        except Exception as e:
            logger.debug(f"Pulse waveform not available: {e}")

        return AnalysisInputs(
            session_id=session_id,
            flow_timestamps=timestamps.copy(),
            flow_values=flow_values.copy(),
            sample_rate=sample_rate,
            machine_events=machine_events,
            spo2_values=spo2_values,
            pulse_timestamps=pulse_timestamps,
            pulse_values=pulse_values,
            modes=modes_list,
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

        Convenience wrapper that calls ``load_session_inputs`` then
        ``compute_analysis`` in sequence.  Used by single-session paths where
        the session lifetime is already bounded by the request context.  Batch
        analysis uses the two phases separately so the session is released
        before compute.

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

        inputs = self.load_session_inputs(session_id, modes=modes_list)
        result = self.compute_analysis(inputs)

        processing_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"Analysis complete in {processing_time_ms}ms")

        if store_results:
            self._store_result(result, processing_time_ms)

        return result

    def get_analysis_result(self, session_id: int) -> AnalysisResult | None:
        """
        Retrieve stored analysis result for a session.

        Args:
            session_id: Database session ID

        Returns:
            AnalysisResult dataclass or None if not found
        """
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
        """
        Store analysis result to database.

        Args:
            result: Analysis result to store
            processing_time_ms: Processing time in milliseconds

        Returns:
            Database analysis result ID
        """
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
