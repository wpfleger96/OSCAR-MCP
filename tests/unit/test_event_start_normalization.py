"""Regression tests: RespiratoryEvent.start_time is always the true event start.

ResMed EVE EDF annotations and OSCAR binary event lists both timestamp events at
their END.  The parsers compensate by subtracting the duration so
RespiratoryEvent.start_time always represents the true start.

The CSL path already stores the true start (it pairs the "CSR Start" / "CSR End"
annotation texts itself and never sees an end-flagged timestamp), so it must NOT
receive any duration shift.

These tests guard all three paths against regressions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from snore.constants import CPAP_OBSTRUCTIVE
from snore.parsers.oscar_device import OscarDeviceParser
from snore.parsers.resmed_edf import ResmedEDFParser
from snore.parsers.types import EventList, EventListType, SessionEvents, SessionSummary
from snore.parsers.unified import DeviceInfo, RespiratoryEventType, UnifiedSession

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    start: datetime = datetime(2025, 1, 1, 22, 0, 0),
    duration_hours: float = 7.0,
) -> UnifiedSession:
    device_info = DeviceInfo(
        manufacturer="ResMed",
        model="AirSense 11 APAP",
        serial_number="TESTEVENORM",
    )
    return UnifiedSession(
        device_info=device_info,
        start_time=start,
        end_time=start + timedelta(hours=duration_hours),
    )


def _write_annotation_edf(
    tmp_path: Path,
    filename: str,
    start_dt: datetime,
    annotations: list[tuple[float, float | None, str]],
) -> Path:
    """Create a minimal EDF+ annotation file.

    Each annotation is (onset_seconds, duration_seconds|None, text).
    """
    import pyedflib

    edf_path = tmp_path / filename
    with pyedflib.EdfWriter(str(edf_path), 0, file_type=pyedflib.FILETYPE_EDFPLUS) as f:
        f.setStartdatetime(start_dt)
        f.setDatarecordDuration(1)
        for onset, duration, text in annotations:
            dur = duration if duration is not None else -1.0
            f.writeAnnotation(onset, dur, text)
    return edf_path


def _make_oscar_summary(first_ms: int, last_ms: int) -> SessionSummary:
    return SessionSummary(
        magic=0xC73216AB,
        version=10,
        file_type=0,
        machine_id=1,
        session_id=first_ms // 1000,
        first_timestamp=first_ms,
        last_timestamp=last_ms,
    )


def _make_oscar_events(
    first_ms: int, last_ms: int, event_at_ms: int, duration_s: int
) -> SessionEvents:
    """Build a SessionEvents with one OA event at event_at_ms with the given duration (seconds)."""
    event_list = EventList(
        channel_id=CPAP_OBSTRUCTIVE,
        first_timestamp=event_at_ms,
        last_timestamp=event_at_ms,
        count=1,
        event_type=EventListType.EVENT,
        sample_rate=0.0,
        gain=1.0,
        offset=0.0,
        min_value=0.0,
        max_value=60.0,
        dimension="s",
        data=[duration_s],
        time_deltas=[0],
    )
    return SessionEvents(
        magic=0xC73216AB,
        version=10,
        file_type=1,
        machine_id=1,
        session_id=first_ms // 1000,
        first_timestamp=first_ms,
        last_timestamp=last_ms,
        event_lists={CPAP_OBSTRUCTIVE: [event_list]},
    )


# ---------------------------------------------------------------------------
# EVE annotation path
# ---------------------------------------------------------------------------


class TestEVEEventStartNormalization:
    """_parse_events: ResMed flags events at end; parser shifts to true start."""

    def test_eve_explicit_duration_stores_flag_time_minus_duration(self, tmp_path):
        """OA annotation at flag_time T with explicit duration D → start_time == T - D."""
        session_start = datetime(2025, 1, 1, 22, 0, 0)
        session = _make_session(start=session_start)
        parser = ResmedEDFParser()

        # Annotation: 60 seconds into the session, explicit duration 20 s.
        # The annotation onset is the flag time (end of event).
        flag_onset_s = 60.0
        duration_s = 20.0
        eve_file = _write_annotation_edf(
            tmp_path,
            "20250101_220000_EVE.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (flag_onset_s, duration_s, "Obstructive Apnea"),
            ],
        )

        parser._parse_events(eve_file, session)

        events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.OBSTRUCTIVE_APNEA
        ]
        assert len(events) == 1
        flag_time = session_start + timedelta(seconds=flag_onset_s)
        assert events[0].start_time == flag_time - timedelta(seconds=duration_s)
        assert events[0].duration_seconds == pytest.approx(duration_s)

    def test_eve_no_duration_applies_10s_default_shift(self, tmp_path):
        """Annotation with no duration falls back to the 10 s default and shifts by that amount."""
        session_start = datetime(2025, 1, 1, 22, 0, 0)
        session = _make_session(start=session_start)
        parser = ResmedEDFParser()

        # H annotation: 300 seconds into the session, no explicit duration.
        flag_onset_s = 300.0
        default_duration_s = 10.0
        eve_file = _write_annotation_edf(
            tmp_path,
            "20250101_220000_EVE_nodur.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (flag_onset_s, None, "Hypopnea"),
            ],
        )

        parser._parse_events(eve_file, session)

        events = [
            e for e in session.events if e.event_type == RespiratoryEventType.HYPOPNEA
        ]
        assert len(events) == 1
        flag_time = session_start + timedelta(seconds=flag_onset_s)
        assert events[0].start_time == flag_time - timedelta(seconds=default_duration_s)
        assert events[0].duration_seconds == pytest.approx(default_duration_s)


# ---------------------------------------------------------------------------
# OSCAR binary event list path
# ---------------------------------------------------------------------------


class TestOSCAREventStartNormalization:
    """_populate_events_from_events: OSCAR timestamps are end-of-event; parser shifts to true start."""

    def test_oscar_event_flag_time_shifted_to_true_start(self, tmp_path):
        """OA event at flag_time T with duration D → start_time == T - D (UTC wall clock)."""
        # Session: 2024-01-15 00:30 UTC to 06:30 UTC
        first_ms = int(datetime(2024, 1, 15, 0, 30, tzinfo=UTC).timestamp() * 1000)
        last_ms = first_ms + 6 * 3600 * 1000

        # Event flagged 1 hour into the session; duration 30 seconds.
        event_at_ms = first_ms + 3600 * 1000
        duration_s = 30

        summary = _make_oscar_summary(first_ms, last_ms)
        events_data = _make_oscar_events(first_ms, last_ms, event_at_ms, duration_s)

        summary_path = tmp_path / "1.000"
        summary_path.touch()
        events_path = tmp_path / "1.001"
        events_path.touch()

        device_info = DeviceInfo(
            manufacturer="OSCAR", model="Test", serial_number="TESTNORM"
        )

        with (
            patch(
                "snore.parsers.oscar_device.parse_summary_file", return_value=summary
            ),
            patch(
                "snore.parsers.oscar_device.parse_events_file", return_value=events_data
            ),
        ):
            session = OscarDeviceParser()._parse_single_session(
                session_id=first_ms // 1000,
                summary_path=summary_path,
                events_path=events_path,
                device_info=device_info,
                base_path=tmp_path,
                timezone_name=None,  # UTC wall clock
            )

        assert len(session.events) == 1
        event = session.events[0]
        assert event.duration_seconds == pytest.approx(float(duration_s))
        # UTC wall clock: flag_time is 01:30:00; true start is 01:29:30.
        flag_time = datetime(2024, 1, 15, 1, 30, 0)
        assert event.start_time == flag_time - timedelta(seconds=duration_s)


# ---------------------------------------------------------------------------
# CSL path guard
# ---------------------------------------------------------------------------


class TestCSLStartTimeUnchanged:
    """Guard: the CSL parser stores the CSR Start time as-is — no duration shift.

    The CSL path already pairs "CSR Start" / "CSR End" annotations and stores
    the true start directly.  Applying the EVE-style shift here would be wrong
    and would move the start BEFORE the CSR Start annotation.
    """

    def test_csl_pb_event_start_equals_csr_start_not_csr_end(self, tmp_path):
        """PB start_time == CSR Start onset, not CSR End and not CSR Start - duration."""
        session_start = datetime(2025, 6, 1, 23, 0, 0)
        device_info = DeviceInfo(
            manufacturer="ResMed",
            model="AirSense 11 APAP",
            serial_number="TESTGUARD",
        )
        session = UnifiedSession(
            device_info=device_info,
            start_time=session_start,
            end_time=session_start + timedelta(hours=7),
        )
        parser = ResmedEDFParser()

        # CSR span: starts 60 s in, ends 150 s in (duration 90 s).
        csr_start_onset = 60.0
        csr_end_onset = 150.0
        csl_file = _write_annotation_edf(
            tmp_path,
            "guard_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (csr_start_onset, None, "CSR Start"),
                (csr_end_onset, None, "CSR End"),
            ],
        )

        parser._parse_csl_files_for_night([csl_file], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 1
        expected_start = session_start + timedelta(seconds=csr_start_onset)
        expected_end_time = session_start + timedelta(seconds=csr_end_onset)
        # True start is the CSR Start annotation time.
        assert pb_events[0].start_time == expected_start
        # Guard: must NOT be the CSR End time (which would mean the shift was misapplied).
        assert pb_events[0].start_time != expected_end_time
        assert pb_events[0].duration_seconds == pytest.approx(
            csr_end_onset - csr_start_onset
        )
