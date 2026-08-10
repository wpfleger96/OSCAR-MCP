"""Unit tests for ResmedEDFParser._parse_csl_files_for_night.

Covers:
- Synthetic CSL with matched CSR Start/End pairs → PB events with correct
  start_time and duration.
- CSL files where all annotations are 'Recording starts' stubs (as seen on
  real AirSense 11 devices) → zero events added.
- Missing CSL file list (empty list) → no-op.
- Orphan 'CSR End' without preceding 'CSR Start' → skipped.
- CSR span outside the session window → filtered out.
- Multiple CSR spans across two CSL files → all added.

Note: Real CSL files from the user's devices contain only 'Recording starts'
stubs, so the positive-path tests use synthetic EDF+ files created with
pyedflib to exercise the parsing logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from snore.parsers.resmed_edf import ResmedEDFParser
from snore.parsers.unified import DeviceInfo, RespiratoryEventType, UnifiedSession

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parser():
    return ResmedEDFParser()


def _make_session(
    start: datetime = datetime(2025, 6, 1, 23, 0, 0),
    duration_hours: float = 7.0,
) -> UnifiedSession:
    device_info = DeviceInfo(
        manufacturer="ResMed",
        model="AirSense 11 APAP",
        serial_number="TESTCSL",
    )
    end = start + timedelta(hours=duration_hours)
    return UnifiedSession(device_info=device_info, start_time=start, end_time=end)


def _write_annotation_edf(
    tmp_path: Path,
    filename: str,
    start_dt: datetime,
    annotations: list[tuple[float, float | None, str]],
) -> Path:
    """Create a minimal EDF+ annotation file (no signal channels).

    Each annotation is (onset_seconds, duration_seconds|None, text).
    pyedflib writes the EDF+ TAL (Time-stamped Annotation Lists) automatically
    when signal annotations are provided via writeAnnotation.
    """
    import pyedflib

    edf_path = tmp_path / filename
    # EDF+ with 1 annotation channel and 0 ordinary signal channels.
    with pyedflib.EdfWriter(str(edf_path), 0, file_type=pyedflib.FILETYPE_EDFPLUS) as f:
        f.setStartdatetime(start_dt)
        f.setDatarecordDuration(1)
        for onset, duration, text in annotations:
            dur = duration if duration is not None else -1.0
            f.writeAnnotation(onset, dur, text)
    return edf_path


# ---------------------------------------------------------------------------
# Positive path: CSR Start/End pairs → PB events
# ---------------------------------------------------------------------------


class TestCSLPositivePath:
    """Synthetic CSL files with real CSR spans produce PB events."""

    def test_single_csr_span_creates_pb_event(self, parser, tmp_path):
        """One CSR Start / CSR End pair → one PERIODIC_BREATHING event."""
        session_start = datetime(2025, 6, 1, 23, 0, 0)
        session = _make_session(start=session_start)

        # CSR span: 60s into session, duration 90s.
        onset_start = 60.0
        onset_end = 150.0
        csl_file = _write_annotation_edf(
            tmp_path,
            "20250601_230000_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (onset_start, None, "CSR Start"),
                (onset_end, None, "CSR End"),
            ],
        )

        parser._parse_csl_files_for_night([csl_file], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 1
        expected_start = session_start + timedelta(seconds=onset_start)
        assert pb_events[0].start_time == expected_start
        assert pb_events[0].duration_seconds == pytest.approx(onset_end - onset_start)

    def test_two_csr_spans_create_two_events(self, parser, tmp_path):
        """Two paired CSR spans in one file → two PB events."""
        session_start = datetime(2025, 6, 1, 23, 0, 0)
        session = _make_session(start=session_start)

        csl_file = _write_annotation_edf(
            tmp_path,
            "20250601_230000_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (100.0, None, "CSR Start"),
                (200.0, None, "CSR End"),
                (500.0, None, "CSR Start"),
                (620.0, None, "CSR End"),
            ],
        )

        parser._parse_csl_files_for_night([csl_file], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 2
        assert pb_events[0].duration_seconds == pytest.approx(100.0)
        assert pb_events[1].duration_seconds == pytest.approx(120.0)

    def test_spans_from_two_csl_files_all_added(self, parser, tmp_path):
        """CSR spans in two separate CSL segment files are both imported."""
        session_start = datetime(2025, 6, 1, 23, 0, 0)
        session = _make_session(start=session_start)

        csl1 = _write_annotation_edf(
            tmp_path,
            "seg1_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (60.0, None, "CSR Start"),
                (120.0, None, "CSR End"),
            ],
        )
        csl2 = _write_annotation_edf(
            tmp_path,
            "seg2_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (3600.0, None, "CSR Start"),
                (3720.0, None, "CSR End"),
            ],
        )

        parser._parse_csl_files_for_night([csl1, csl2], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 2


# ---------------------------------------------------------------------------
# Stub / no-event path
# ---------------------------------------------------------------------------


class TestCSLStubFile:
    """CSL files that contain only 'Recording starts' produce no events.

    This is the common case for AirSense 11 devices that emit an empty CSL
    each session even when no Cheyne-Stokes events occurred.
    """

    def test_recording_starts_only_gives_no_events(self, parser, tmp_path):
        """'Recording starts' annotation stub → zero PB events."""
        session = _make_session()
        session_start = session.start_time
        csl_file = _write_annotation_edf(
            tmp_path,
            "stub_CSL.edf",
            session_start,
            [(0.0, None, "Recording starts")],
        )

        parser._parse_csl_files_for_night([csl_file], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 0

    def test_empty_csl_list_is_noop(self, parser):
        """Passing an empty list does nothing and does not raise."""
        session = _make_session()
        parser._parse_csl_files_for_night([], session)
        assert session.events == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestCSLEdgeCases:
    """Orphan events, out-of-session spans, and zero-record files."""

    def test_orphan_csr_end_is_skipped(self, parser, tmp_path):
        """'CSR End' with no preceding 'CSR Start' is silently dropped."""
        session = _make_session()
        session_start = session.start_time
        csl_file = _write_annotation_edf(
            tmp_path,
            "orphan_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                # No matching CSR Start
                (200.0, None, "CSR End"),
            ],
        )

        parser._parse_csl_files_for_night([csl_file], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 0

    def test_csr_span_outside_session_window_filtered(self, parser, tmp_path):
        """CSR Start time outside session → event dropped."""
        # Session runs 23:00–06:00 (7 h).
        session_start = datetime(2025, 6, 1, 23, 0, 0)
        session = _make_session(start=session_start)
        # CSL file starts at the same time but the CSR span is way after session end.
        csl_file = _write_annotation_edf(
            tmp_path,
            "late_CSL.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                # 9 h past session start → after session.end_time (7 h)
                (9 * 3600.0, None, "CSR Start"),
                (9 * 3600.0 + 120.0, None, "CSR End"),
            ],
        )

        parser._parse_csl_files_for_night([csl_file], session)

        pb_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.PERIODIC_BREATHING
        ]
        assert len(pb_events) == 0
