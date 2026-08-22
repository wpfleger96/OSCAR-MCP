"""Zero-byte EDF stubs are a normal device artifact, not corruption.

ResMed machines write zero-byte EVE/CSL (and waveform) files for very brief
mask-on segments.  The event parsers must skip them silently (debug level)
instead of routing them through the generic open-failure path, which logs a
WARNING per file on every import and demo bootstrap.
"""

from __future__ import annotations

import logging

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from snore.parsers.resmed_edf import ResmedEDFParser
from snore.parsers.unified import DeviceInfo, RespiratoryEventType, UnifiedSession

pytestmark = pytest.mark.unit

PARSER_LOGGER = "snore.parsers.resmed_edf"


def _make_session(
    start: datetime = datetime(2025, 1, 1, 22, 0, 0),
    duration_hours: float = 7.0,
) -> UnifiedSession:
    device_info = DeviceInfo(
        manufacturer="ResMed",
        model="AirSense 11 APAP",
        serial_number="TESTEMPTYSTUB",
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


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestEmptyEveStubSkipped:
    def test_empty_eve_in_night_path_skipped_without_warning(self, tmp_path, caplog):
        session = _make_session()
        empty_eve = tmp_path / "20250101_223000_EVE.edf"
        empty_eve.touch()

        with caplog.at_level(logging.DEBUG, logger=PARSER_LOGGER):
            ResmedEDFParser()._parse_eve_files_for_night([empty_eve], session)

        assert session.events == []
        assert _warnings(caplog) == []

    def test_empty_eve_does_not_abort_valid_sibling_in_night_path(
        self, tmp_path, caplog
    ):
        """A zero-byte stub earlier in the list must not stop later files parsing."""
        session_start = datetime(2025, 1, 1, 22, 0, 0)
        session = _make_session(start=session_start)

        empty_eve = tmp_path / "20250101_223000_EVE.edf"
        empty_eve.touch()
        valid_eve = _write_annotation_edf(
            tmp_path,
            "20250101_230000_EVE.edf",
            session_start,
            [
                (0.0, None, "Recording starts"),
                (600.0, 15.0, "Obstructive Apnea"),
            ],
        )

        with caplog.at_level(logging.DEBUG, logger=PARSER_LOGGER):
            ResmedEDFParser()._parse_eve_files_for_night(
                [empty_eve, valid_eve], session
            )

        oa_events = [
            e
            for e in session.events
            if e.event_type == RespiratoryEventType.OBSTRUCTIVE_APNEA
        ]
        assert len(oa_events) == 1
        assert _warnings(caplog) == []

    def test_empty_eve_in_session_path_skipped_without_warning_or_notes(
        self, tmp_path, caplog
    ):
        session = _make_session()
        empty_eve = tmp_path / "20250101_223000_EVE.edf"
        empty_eve.touch()

        with caplog.at_level(logging.DEBUG, logger=PARSER_LOGGER):
            ResmedEDFParser()._parse_eve_files_for_night([empty_eve], session)

        assert session.events == []
        assert _warnings(caplog) == []
        assert session.data_quality_notes == []


class TestEmptyCslStubSkipped:
    def test_empty_csl_skipped_without_warning(self, tmp_path, caplog):
        session = _make_session()
        empty_csl = tmp_path / "20250101_223000_CSL.edf"
        empty_csl.touch()

        with caplog.at_level(logging.DEBUG, logger=PARSER_LOGGER):
            ResmedEDFParser()._parse_csl_files_for_night([empty_csl], session)

        assert session.events == []
        assert _warnings(caplog) == []
