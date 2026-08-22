"""Tests for the public ``ResmedEDFParser.parse_night_session`` wrapper.

The demo-fixture import path (``DemoService``) calls the public
``parse_night_session`` directly rather than the normal
``_parse_single_session_bundle`` bundle path. Only the bundle path used to
finalize statistics, so demo-profile sessions imported with ``has_statistics``
False and null AHI / usage hours. The wrapper must return a complete
``UnifiedSession`` with statistics finalized.
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from snore.parsers.resmed_edf import ResmedEDFParser
from snore.parsers.unified import (
    DeviceInfo,
    RespiratoryEvent,
    RespiratoryEventType,
    UnifiedSession,
)


def _device_info() -> DeviceInfo:
    return DeviceInfo(
        manufacturer="ResMed",
        model="AirSense 10",
        serial_number=f"SN_{uuid.uuid4().hex[:8]}",
    )


def _segment_with_events(start: datetime, duration_s: float) -> UnifiedSession:
    """A single-segment session carrying two obstructive apneas."""
    session = UnifiedSession(
        device_info=_device_info(),
        device_session_id=start.strftime("%Y%m%d_%H%M%S"),
        start_time=start,
        end_time=start + timedelta(seconds=duration_s),
    )
    for offset in (600.0, 1800.0):
        session.add_event(
            RespiratoryEvent(
                event_type=RespiratoryEventType.OBSTRUCTIVE_APNEA,
                start_time=start + timedelta(seconds=offset),
                duration_seconds=15.0,
            )
        )
    return session


class TestPublicParseNightSessionFinalizes:
    def test_public_wrapper_finalizes_statistics(self, monkeypatch):
        """parse_night_session returns a session with statistics finalized."""
        start = datetime(2025, 9, 10, 22, 0, 0)
        segment = _segment_with_events(start, 3600.0)  # 1 mask-on hour

        parser = ResmedEDFParser()
        monkeypatch.setattr(
            parser,
            "_parse_session_group",
            lambda *args, **kwargs: segment,
        )
        segments = {
            segment.device_session_id: {
                "BRP": Path(f"/nonexistent/{segment.device_session_id}.edf")
            }
        }

        night = parser.parse_night_session(
            night_date="20250910",
            segments=segments,
            device_info=_device_info(),
            base_path=Path("/nonexistent"),
        )

        assert night is not None
        assert night.has_statistics is True
        # 2 obstructive apneas over 1 mask-on hour.
        assert night.statistics.ahi == pytest.approx(2.0)
        assert night.statistics.usage_hours == pytest.approx(1.0)
