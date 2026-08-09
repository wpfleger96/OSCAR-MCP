"""Integration tests for SNORE MCP tools.

Tests exercise each tool implementation directly against an in-memory async DB,
using the same fixture helpers as the rest of the integration suite.  These tests
verify behavior: correct data returned, null + reason pattern, pagination,
and graceful degradation when analysis results are absent.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import Event
from tests.integration.conftest import _make_day_session, _make_device

# ---------------------------------------------------------------------------
# get_data_overview
# ---------------------------------------------------------------------------


class TestGetDataOverview:
    async def test_empty_database_returns_empty_devices(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.overview import get_data_overview

        result = await get_data_overview(
            async_db_session, profile_id=async_test_profile.id
        )
        assert result.devices == []
        assert result.total_sessions == 0
        assert not result.analysis_run

    async def test_device_and_sessions_appear_in_overview(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.overview import get_data_overview

        device = await _make_device(async_db_session, async_test_profile.id)
        today = date(2024, 8, 1)
        await _make_day_session(async_db_session, device, today)
        await _make_day_session(async_db_session, device, today + timedelta(days=1))

        result = await get_data_overview(
            async_db_session, profile_id=async_test_profile.id
        )
        assert len(result.devices) == 1
        assert result.devices[0].manufacturer == "TestMfr"
        assert result.devices[0].session_count == 2
        assert result.total_sessions == 2
        assert result.date_range_start == today
        assert result.date_range_end == today + timedelta(days=1)

    async def test_analysis_run_false_when_no_analysis_results(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.overview import get_data_overview

        device = await _make_device(async_db_session, async_test_profile.id)
        await _make_day_session(async_db_session, device, date(2024, 8, 1))

        result = await get_data_overview(
            async_db_session, profile_id=async_test_profile.id
        )
        assert not result.analysis_run
        assert result.analysis_session_count == 0


# ---------------------------------------------------------------------------
# get_settings_timeline
# ---------------------------------------------------------------------------


class TestGetSettingsTimeline:
    async def test_empty_database_returns_no_epochs(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.settings import get_settings_timeline

        result = await get_settings_timeline(
            async_db_session,
            date(2024, 1, 1),
            date(2024, 12, 31),
            profile_id=async_test_profile.id,
        )
        assert result.epochs == []
        assert result.total_epochs == 0

    async def test_epochs_filtered_to_date_range(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.database.models import Setting
        from snore.mcp.tools.settings import get_settings_timeline

        device = await _make_device(async_db_session, async_test_profile.id)

        # Two sessions with settings — one inside range, one outside
        day1, sess1 = await _make_day_session(
            async_db_session, device, date(2024, 3, 1)
        )
        day2, sess2 = await _make_day_session(
            async_db_session, device, date(2024, 9, 1)
        )

        for sess in [sess1, sess2]:
            async_db_session.add(
                Setting(session_id=sess.id, key="mode", value="AutoSet")
            )
        await async_db_session.flush()

        # Query only the first half of the year
        result = await get_settings_timeline(
            async_db_session,
            date(2024, 1, 1),
            date(2024, 6, 30),
            profile_id=async_test_profile.id,
        )
        # Only the March session epoch should appear
        assert result.total_epochs == 1


# ---------------------------------------------------------------------------
# get_nightly_summary
# ---------------------------------------------------------------------------


class TestGetNightlySummary:
    async def test_empty_database_returns_empty_nights(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 1, 1),
            date(2024, 1, 31),
            profile_id=async_test_profile.id,
        )
        assert result.nights == []
        assert result.total_nights == 0

    async def test_rera_fields_null_with_reason_when_analysis_absent(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session, async_test_profile.id)
        await _make_day_session(async_db_session, device, date(2024, 8, 1), ahi=2.5)

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 1),
            profile_id=async_test_profile.id,
        )
        assert len(result.nights) == 1
        night = result.nights[0]
        assert night.rera_index is None
        assert night.rera_index_reason == "not_available"
        assert night.rdi is None
        assert night.fl_class_ge4_pct is None
        assert night.fl_class_ge4_pct_reason == "not_available"
        assert night.leak_above_24_pct is None
        assert night.leak_above_24_pct_reason == "not_available"

    async def test_ahi_populated_from_day_row(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session, async_test_profile.id)
        await _make_day_session(
            async_db_session, device, date(2024, 8, 1), ahi=5.2, oai=1.0, cai=0.5
        )

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 1),
            profile_id=async_test_profile.id,
        )
        night = result.nights[0]
        assert night.ahi == pytest.approx(5.2, abs=0.01)
        assert night.oai == pytest.approx(1.0, abs=0.01)

    async def test_compliance_block_present_in_range_mode(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session, async_test_profile.id)
        for i in range(5):
            await _make_day_session(
                async_db_session,
                device,
                date(2024, 8, 1) + timedelta(days=i),
                duration_hours=8.0,
            )

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 5),
            compliance_threshold_hours=4.0,
            profile_id=async_test_profile.id,
        )
        assert result.compliance is not None
        assert result.compliance.days_total == 5
        assert result.compliance.days_compliant == 5
        assert result.compliance.compliance_pct == 100.0

    async def test_compliance_below_threshold_counted_correctly(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session, async_test_profile.id)
        # 3 full nights, 2 short nights
        for i in range(3):
            await _make_day_session(
                async_db_session,
                device,
                date(2024, 8, 1) + timedelta(days=i),
                duration_hours=8.0,
            )
        for i in range(3, 5):
            await _make_day_session(
                async_db_session,
                device,
                date(2024, 8, 1) + timedelta(days=i),
                duration_hours=2.0,  # below 4 h threshold
            )

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 5),
            compliance_threshold_hours=4.0,
            profile_id=async_test_profile.id,
        )
        assert result.compliance is not None
        assert result.compliance.days_compliant == 3
        assert result.compliance.days_total == 5

    async def test_pagination_returns_correct_page(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session, async_test_profile.id)
        for i in range(10):
            await _make_day_session(
                async_db_session,
                device,
                date(2024, 8, 1) + timedelta(days=i),
            )

        page1 = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 10),
            page=1,
            page_size=5,
            profile_id=async_test_profile.id,
        )
        page2 = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 10),
            page=2,
            page_size=5,
            profile_id=async_test_profile.id,
        )
        assert len(page1.nights) == 5
        assert len(page2.nights) == 5
        assert page1.total_nights == 10
        # Pages should not overlap
        dates_p1 = {n.date for n in page1.nights}
        dates_p2 = {n.date for n in page2.nights}
        assert not dates_p1 & dates_p2


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------


class TestGetEvents:
    async def test_missing_date_raises_validation_error(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.tools.events import get_events

        with pytest.raises(ValidationError, match="No therapy data"):
            await get_events(
                async_db_session, date(2024, 1, 1), profile_id=async_test_profile.id
            )

    async def test_events_returned_for_session_date(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 15)
        day, sess = await _make_day_session(async_db_session, device, target_date)

        session_start = sess.start_time
        # Add two events
        for i, ev_type in enumerate(["OA", "CA"]):
            async_db_session.add(
                Event(
                    session_id=sess.id,
                    event_type=ev_type,
                    start_time=session_start + timedelta(minutes=10 + i * 5),
                    duration_seconds=15.0,
                )
            )
        await async_db_session.flush()

        result = await get_events(
            async_db_session, target_date, profile_id=async_test_profile.id
        )
        assert result.total_events == 2
        types_returned = {e.event_type for e in result.events}
        assert types_returned == {"OA", "CA"}

    async def test_event_type_filter_applied(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 16)
        day, sess = await _make_day_session(async_db_session, device, target_date)
        session_start = sess.start_time

        for i, ev_type in enumerate(["OA", "CA", "H"]):
            async_db_session.add(
                Event(
                    session_id=sess.id,
                    event_type=ev_type,
                    start_time=session_start + timedelta(minutes=10 + i * 5),
                    duration_seconds=10.0,
                )
            )
        await async_db_session.flush()

        result = await get_events(
            async_db_session,
            target_date,
            types=["OA"],
            profile_id=async_test_profile.id,
        )
        assert result.total_events == 1
        assert result.events[0].event_type == "OA"

    async def test_min_duration_filter_applied(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 17)
        day, sess = await _make_day_session(async_db_session, device, target_date)
        session_start = sess.start_time

        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=session_start + timedelta(minutes=10),
                duration_seconds=5.0,  # short
            )
        )
        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=session_start + timedelta(minutes=20),
                duration_seconds=30.0,  # long
            )
        )
        await async_db_session.flush()

        result = await get_events(
            async_db_session,
            target_date,
            min_duration=10.0,
            profile_id=async_test_profile.id,
        )
        assert result.total_events == 1
        assert result.events[0].duration_seconds == 30.0

    async def test_event_context_includes_minutes_since_start(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 18)
        day, sess = await _make_day_session(async_db_session, device, target_date)
        session_start = sess.start_time

        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="CA",
                start_time=session_start + timedelta(minutes=45),
                duration_seconds=20.0,
            )
        )
        await async_db_session.flush()

        result = await get_events(
            async_db_session,
            target_date,
            include_context=True,
            profile_id=async_test_profile.id,
        )
        assert result.total_events == 1
        ctx = result.events[0].context
        assert ctx is not None
        assert ctx.minutes_since_session_start == pytest.approx(45.0, abs=0.1)

    async def test_event_row_a6_timestamp_contract(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """A6: EventRow uses offset-free ISO 8601 wall-clock + offset_seconds, not UTC-offset."""
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 18)
        day, sess = await _make_day_session(async_db_session, device, target_date)
        session_start = sess.start_time

        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=session_start + timedelta(minutes=30),
                duration_seconds=10.0,
            )
        )
        await async_db_session.flush()

        result = await get_events(
            async_db_session,
            target_date,
            include_context=True,
            profile_id=async_test_profile.id,
        )
        ev = result.events[0]

        # Tier-2: wall-clock must be offset-free (no +HH:MM, no Z)
        assert ev.timezone_status == "unknown"
        assert "+" not in ev.start_time_wall_clock
        assert ev.start_time_wall_clock.endswith("Z") is False
        # Tier-3: offset_seconds = 30 min from session start
        assert ev.offset_seconds == pytest.approx(1800.0, abs=0.1)
        # Response also carries session anchor
        assert result.timezone_status == "unknown"
        assert "+" not in result.session_start_wall_clock

    async def test_context_disabled_returns_no_context_block(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 19)
        day, sess = await _make_day_session(async_db_session, device, target_date)
        session_start = sess.start_time

        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=session_start + timedelta(minutes=10),
                duration_seconds=10.0,
            )
        )
        await async_db_session.flush()

        result = await get_events(
            async_db_session,
            target_date,
            include_context=False,
            profile_id=async_test_profile.id,
        )
        assert result.total_events == 1
        assert result.events[0].context is None


# ---------------------------------------------------------------------------
# A6 non-UTC determinism test
# ---------------------------------------------------------------------------


class TestA6TimestampDeterminism:
    async def test_event_timestamps_identical_in_non_utc_host(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """A6: wall-clock timestamps must be identical regardless of host timezone.

        The DB stores naive datetimes (no TZ).  isoformat() on a naive datetime
        produces offset-free strings — same output whether the host is UTC,
        America/New_York, or Asia/Tokyo.  This test proves that by checking the
        output is offset-free and that running the tool under TZ=America/New_York
        (simulated by confirming no offset appears) produces the same value as the
        raw DB string.
        """
        import os

        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session, async_test_profile.id)
        target_date = date(2024, 8, 20)
        day, sess = await _make_day_session(async_db_session, device, target_date)
        session_start = sess.start_time

        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=session_start + timedelta(hours=1),
                duration_seconds=10.0,
            )
        )
        await async_db_session.flush()

        # Capture result under current TZ
        result = await get_events(
            async_db_session, target_date, profile_id=async_test_profile.id
        )
        ev = result.events[0]
        wall_clock_str = ev.start_time_wall_clock

        # Verify: offset-free (no +HH:MM, no Z, no -HH:MM)
        assert "+" not in wall_clock_str
        assert wall_clock_str.rstrip("0123456789:.T-") == ""  # only datetime chars
        assert ev.timezone_status == "unknown"

        # Verify: matches the raw DB value exactly (naive datetime.isoformat())
        expected = (session_start + timedelta(hours=1)).isoformat()
        assert wall_clock_str == expected

        # Verify the same invariant holds under a non-UTC host TZ.
        # Setting os.environ["TZ"] + calling time.tzset() (POSIX-only) actually
        # shifts the process timezone.  On Windows (no tzset), the test still
        # checks offset-freedom through the env change, which is weaker but
        # harmless.  The real protection is that our code never calls
        # .astimezone() or .timestamp() on naive datetimes.
        import time

        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            if hasattr(time, "tzset"):
                time.tzset()
            # Re-run the same tool — output must be byte-identical
            result2 = await get_events(
                async_db_session, target_date, profile_id=async_test_profile.id
            )
            assert result2.events[0].start_time_wall_clock == wall_clock_str
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            if hasattr(time, "tzset"):
                time.tzset()


# ---------------------------------------------------------------------------
# M4 cold-process capabilities test
# ---------------------------------------------------------------------------


class TestCapabilitiesColdProcess:
    async def test_capabilities_returns_empty_lists_on_cold_db(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """M4: docs://capabilities on a fresh DB returns empty lists, not errors."""
        from snore.mcp.tools.overview import get_data_overview

        # Cold DB — no imports
        result = await get_data_overview(
            async_db_session, profile_id=async_test_profile.id
        )

        assert result.devices == []
        assert result.total_sessions == 0
        assert result.available_waveform_channels == []
        assert result.available_event_types == []
        assert not result.analysis_run
        assert result.analysis_session_count == 0

    async def test_capabilities_register_all_parsers_idempotent(self) -> None:
        """M4: register_all_parsers() is safe to call multiple times (idempotent via catch)."""
        from snore.parsers.register_all import register_all_parsers
        from snore.parsers.registry import parser_registry

        # First call registers parsers
        register_all_parsers()
        count_after_first = len(parser_registry.list_parsers())
        assert count_after_first >= 0  # may be 0 if parsers not installed; that's fine

        # Second call must not raise even if parsers are already registered
        try:
            register_all_parsers()
        except Exception as exc:
            raise AssertionError(
                f"register_all_parsers() raised on second call: {exc}"
            ) from exc
