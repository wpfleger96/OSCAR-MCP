"""Waveform service for listing and loading waveform data."""

from bisect import bisect_left, bisect_right
from typing import Any

import numpy as np

from sqlalchemy import select
from sqlalchemy.orm import Session

from snore.analysis.data.waveform_loader import (
    WaveformLoader,
    deserialize_waveform_blob,
    fetch_waveform_blob,
)
from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.lttb import lttb_downsample
from snore.services.schemas import (
    EventComparisonDetail,
    EventComparisonResult,
    WaveformInfo,
)

__all__ = ["WaveformService"]


class WaveformService:
    """Service for waveform listing and loading operations."""

    def __init__(self, db_session: Session):
        """
        Initialize waveform service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session
        self._loader = WaveformLoader(db_session)

    def list_waveforms(self, session_id: int) -> list[WaveformInfo]:
        """
        List available waveform types for a session.

        Returns empty list if no waveforms found.

        Args:
            session_id: Database session ID

        Returns:
            List of WaveformInfo objects with metadata
        """
        waveforms = (
            self.db_session.execute(
                select(models.Waveform)
                .where(models.Waveform.session_id == session_id)
                .order_by(models.Waveform.waveform_type)
            )
            .scalars()
            .all()
        )

        result = []
        for wf in waveforms:
            sample_count = wf.sample_count or 0
            duration_seconds = (
                sample_count / wf.sample_rate if wf.sample_rate > 0 else 0
            )
            result.append(
                WaveformInfo(
                    waveform_type=wf.waveform_type,
                    sample_rate=wf.sample_rate,
                    sample_count=sample_count,
                    unit=wf.unit,
                    duration_hours=duration_seconds / 3600,
                )
            )
        return result

    def get_waveform_data(
        self,
        session_id: int,
        waveform_type: str,
        max_points: int | None = None,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        """
        Load waveform data with optional windowing and LTTB downsampling.

        Structured as two explicit phases (§7 I/O–compute split):

        1. **I/O phase** (``fetch_waveform_blob``): DB query returns raw bytes and
           scalar metadata.  The injected session is used only here.
        2. **Compute phase** (``deserialize_waveform_blob``): converts raw bytes to
           numpy arrays.  No DB session access occurs here.

        Args:
            session_id: Database session ID
            waveform_type: Type of waveform to load
            max_points: If set, downsample to this many points using LTTB
            start_seconds: If set, filter timestamps >= this value
            end_seconds: If set, filter timestamps <= this value

        Returns:
            Tuple of (timestamps, values, metadata)

        Raises:
            ValueError: If waveform not found
        """
        # --- I/O phase: DB access only ---
        try:
            data_blob, sample_count, metadata = fetch_waveform_blob(
                self.db_session, session_id, waveform_type
            )
        except ValueError as e:
            raise NotFoundError(str(e)) from e

        # --- Compute phase: no DB session needed after this point ---
        timestamps, values = deserialize_waveform_blob(data_blob, sample_count)

        if start_seconds is not None or end_seconds is not None:
            mask = np.ones(len(timestamps), dtype=bool)
            if start_seconds is not None:
                mask &= timestamps >= start_seconds
            if end_seconds is not None:
                mask &= timestamps <= end_seconds
            timestamps = timestamps[mask]
            values = values[mask]

        if max_points and len(timestamps) > max_points:
            timestamps, values = lttb_downsample(timestamps, values, max_points)

        return timestamps, values, metadata

    def compare_events(
        self,
        session_id: int,
        mode: str = "aasm",
        tolerance_seconds: float = 5.0,
    ) -> EventComparisonResult:
        """
        Compare machine vs programmatic events for a session.

        Args:
            session_id: Database session ID
            mode: Detection mode to compare
            tolerance_seconds: Time tolerance in seconds for matching events

        Returns:
            EventComparisonResult with false negatives and false positives

        Raises:
            NotFoundError: If no analysis result found or mode not available
        """
        from snore.analysis.service import AnalysisService
        from snore.analysis.utils import convert_machine_events

        result = AnalysisService(self.db_session).get_analysis_result(session_id)

        if result is None:
            raise NotFoundError("No analysis results found for this session")

        if mode not in result.mode_results:
            raise NotFoundError(f"Mode '{mode}' not found in analysis results")

        mode_result = result.mode_results[mode]

        machine_events_raw = result.machine_events or []
        machine_apneas, machine_hypopneas = convert_machine_events(machine_events_raw)
        all_machine = machine_apneas + machine_hypopneas

        prog_apneas = list(mode_result.apneas)
        prog_hypopneas = list(mode_result.hypopneas)
        all_prog = prog_apneas + prog_hypopneas

        false_negatives: list[EventComparisonDetail] = []
        false_positives_apnea: list[EventComparisonDetail] = []
        false_positives_hypopnea: list[EventComparisonDetail] = []

        all_machine_sorted = sorted(all_machine, key=lambda e: e.start_time)
        prog_times = sorted(e.start_time for e in all_prog)
        machine_times = [e.start_time for e in all_machine_sorted]

        for m_event in all_machine_sorted:
            lo = bisect_left(prog_times, m_event.start_time - tolerance_seconds)
            hi = bisect_right(prog_times, m_event.start_time + tolerance_seconds)
            if lo >= hi:
                false_negatives.append(
                    EventComparisonDetail(
                        event_type=getattr(m_event, "event_type", "unknown"),
                        start_time=m_event.start_time,
                        duration=getattr(m_event, "duration", 0.0),
                        confidence=None,
                        flow_reduction=None,
                    )
                )

        for apnea_event in prog_apneas:
            lo = bisect_left(machine_times, apnea_event.start_time - tolerance_seconds)
            hi = bisect_right(machine_times, apnea_event.start_time + tolerance_seconds)
            if lo >= hi:
                false_positives_apnea.append(
                    EventComparisonDetail(
                        event_type=apnea_event.event_type,
                        start_time=apnea_event.start_time,
                        duration=apnea_event.duration,
                        confidence=getattr(apnea_event, "confidence", None),
                        flow_reduction=getattr(apnea_event, "flow_reduction", None),
                    )
                )

        for hypopnea_event in prog_hypopneas:
            lo = bisect_left(
                machine_times, hypopnea_event.start_time - tolerance_seconds
            )
            hi = bisect_right(
                machine_times, hypopnea_event.start_time + tolerance_seconds
            )
            if lo >= hi:
                false_positives_hypopnea.append(
                    EventComparisonDetail(
                        event_type="H",
                        start_time=hypopnea_event.start_time,
                        duration=hypopnea_event.duration,
                        confidence=hypopnea_event.confidence,
                        flow_reduction=hypopnea_event.flow_reduction,
                    )
                )

        return EventComparisonResult(
            session_id=session_id,
            mode=mode,
            machine_event_count=len(machine_events_raw),
            programmatic_event_count=len(prog_apneas) + len(prog_hypopneas),
            false_negatives=false_negatives,
            false_positives_apnea=false_positives_apnea,
            false_positives_hypopnea=false_positives_hypopnea,
        )
