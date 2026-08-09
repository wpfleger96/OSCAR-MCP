"""OSCAR importer timezone conversion tests (A6).

OSCAR stores epoch-ms instants.  With a profile-declared IANA timezone the
parser converts them to that zone's wall clock (matching ResMed device-local
semantics); without one it keeps the legacy naive UTC wall-clock.  These tests
pin both behaviors and the noon-cutoff therapy-date off-by-one regression.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

import pytest

from snore.constants import CPAP_OBSTRUCTIVE
from snore.database.day_manager import DayManager
from snore.parsers.oscar_device import OscarDeviceParser, _epoch_to_wall_clock
from snore.parsers.types import (
    EventList,
    EventListType,
    SessionEvents,
    SessionSummary,
)
from snore.parsers.unified import DeviceInfo, UnifiedSession

pytestmark = pytest.mark.unit

# Fixed winter instant: 2024-01-15 00:30 UTC == 2024-01-14 19:30 EST (UTC-5,
# no DST ambiguity).
WINTER_START_MS = int(datetime(2024, 1, 15, 0, 30, tzinfo=UTC).timestamp() * 1000)
WINTER_END_MS = WINTER_START_MS + 6 * 3600 * 1000  # 06:30 UTC == 01:30 EST


def _make_summary(first_ms: int, last_ms: int) -> SessionSummary:
    return SessionSummary(
        magic=0xC73216AB,
        version=10,
        file_type=0,
        machine_id=1,
        session_id=first_ms // 1000,
        first_timestamp=first_ms,
        last_timestamp=last_ms,
    )


def _make_events(first_ms: int, last_ms: int, event_at_ms: int) -> SessionEvents:
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
        max_value=30.0,
        dimension="s",
        data=[15],
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


def _parse_session(
    tmp_path: Path,
    first_ms: int,
    last_ms: int,
    timezone_name: str | None,
    events: SessionEvents | None = None,
) -> UnifiedSession:
    """Run _parse_single_session against synthetic summary/events data."""
    summary_path = tmp_path / "1.000"
    summary_path.touch()
    events_path = None
    if events is not None:
        events_path = tmp_path / "1.001"
        events_path.touch()

    device_info = DeviceInfo(
        manufacturer="OSCAR", model="Test", serial_number="TEST123"
    )

    with (
        patch(
            "snore.parsers.oscar_device.parse_summary_file",
            return_value=_make_summary(first_ms, last_ms),
        ),
        patch(
            "snore.parsers.oscar_device.parse_events_file",
            return_value=events,
        ),
    ):
        return OscarDeviceParser()._parse_single_session(
            session_id=first_ms // 1000,
            summary_path=summary_path,
            events_path=events_path,
            device_info=device_info,
            base_path=tmp_path,
            timezone_name=timezone_name,
        )


class TestDeclaredTimezoneConversion:
    def test_declared_timezone_yields_local_wall_clock(self, tmp_path):
        session = _parse_session(
            tmp_path, WINTER_START_MS, WINTER_END_MS, "America/New_York"
        )

        # 00:30 UTC Jan 15 == 19:30 EST Jan 14 — naive, matching ResMed semantics
        assert session.start_time == datetime(2024, 1, 14, 19, 30)
        assert session.start_time.tzinfo is None
        assert session.end_time == datetime(2024, 1, 15, 1, 30)
        assert session.end_time.tzinfo is None

        # 19:30 is after the noon cutoff → therapy night of Jan 14
        assert DayManager.get_day_for_session(session.start_time) == date(2024, 1, 14)

    def test_no_timezone_keeps_utc_wall_clock(self, tmp_path):
        session = _parse_session(tmp_path, WINTER_START_MS, WINTER_END_MS, None)

        assert session.start_time == datetime(2024, 1, 15, 0, 30)
        assert session.start_time.tzinfo is None
        assert session.end_time == datetime(2024, 1, 15, 6, 30)

        # Legacy behavior: UTC clock puts 00:30 before noon → Jan 14 by luck,
        # but the wall-clock hour itself is UTC, not local.
        assert DayManager.get_day_for_session(session.start_time) == date(2024, 1, 14)

    def test_event_times_use_declared_timezone(self, tmp_path):
        event_at_ms = WINTER_START_MS + 3600 * 1000  # 01:30 UTC == 20:30 EST Jan 14
        events = _make_events(WINTER_START_MS, WINTER_END_MS, event_at_ms)

        session = _parse_session(
            tmp_path, WINTER_START_MS, WINTER_END_MS, "America/New_York", events
        )

        assert len(session.events) == 1
        event = session.events[0]
        assert event.start_time == datetime(2024, 1, 14, 20, 30)
        assert event.start_time.tzinfo is None
        assert event.duration_seconds == 15.0

    def test_noon_cutoff_off_by_one_regression(self, tmp_path):
        """16:30 UTC == 11:30 EST: opposite sides of the noon cutoff.

        UTC clock assigns the session to Jan 15; the declared-timezone local
        clock correctly assigns it to the night of Jan 14.
        """
        start_ms = int(datetime(2024, 1, 15, 16, 30, tzinfo=UTC).timestamp() * 1000)
        end_ms = start_ms + 2 * 3600 * 1000

        utc_session = _parse_session(tmp_path, start_ms, end_ms, None)
        local_session = _parse_session(tmp_path, start_ms, end_ms, "America/New_York")

        assert DayManager.get_day_for_session(utc_session.start_time) == date(
            2024, 1, 15
        )
        assert DayManager.get_day_for_session(local_session.start_time) == date(
            2024, 1, 14
        )


class TestEpochToWallClock:
    def test_dst_summer_offset(self):
        # 2024-07-15 00:30 UTC == 20:30 EDT (UTC-4) July 14
        seconds = datetime(2024, 7, 15, 0, 30, tzinfo=UTC).timestamp()
        assert _epoch_to_wall_clock(seconds, "America/New_York") == datetime(
            2024, 7, 14, 20, 30
        )

    def test_none_is_utc_wall_clock(self):
        seconds = datetime(2024, 7, 15, 0, 30, tzinfo=UTC).timestamp()
        result = _epoch_to_wall_clock(seconds, None)
        assert result == datetime(2024, 7, 15, 0, 30)
        assert result.tzinfo is None

    def test_session_date_filter_uses_same_clock(self):
        # The date-filter sites derive session_date from epoch-seconds session
        # IDs with the same helper, so filtering agrees with day assignment.
        session_id = int(datetime(2024, 1, 15, 0, 30, tzinfo=UTC).timestamp())
        assert _epoch_to_wall_clock(session_id, "America/New_York").date() == date(
            2024, 1, 14
        )
        assert _epoch_to_wall_clock(session_id, None).date() == date(2024, 1, 15)


class TestInvalidTimezone:
    def test_invalid_zone_fails_loudly(self, tmp_path):
        parser = OscarDeviceParser()
        with pytest.raises(ZoneInfoNotFoundError):
            list(parser.parse_sessions(tmp_path, timezone_name="Not/A_Zone"))
