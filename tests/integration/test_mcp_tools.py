"""Integration tests for SNORE MCP tools.

Tests exercise each tool implementation directly against an in-memory async DB,
using the same fixture helpers as the rest of the integration suite.  These tests
verify behavior: correct data returned, null + reason pattern, pagination,
and graceful degradation when analysis results are absent.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import Day, Device, Event, Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_device(db: AsyncSession, manufacturer: str = "TestMfr") -> Device:
    import uuid

    device = Device(
        manufacturer=manufacturer,
        model="TestModel",
        serial_number=f"SN_{uuid.uuid4().hex[:8]}",
    )
    db.add(device)
    await db.flush()
    return device


async def _make_day_session(
    db: AsyncSession,
    device: Device,
    day_date: date,
    duration_hours: float = 8.0,
    **day_kwargs: Any,
) -> tuple[Day, Session]:
    """Create a Day + enabled Session pair."""
    day = Day(
        device_id=device.id,
        date=day_date,
        total_therapy_hours=duration_hours,
        **day_kwargs,
    )
    db.add(day)
    await db.flush()

    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"test_{day_date.isoformat()}",
        start_time=datetime.combine(day_date, datetime.min.time()).replace(hour=22),
        end_time=datetime.combine(day_date, datetime.min.time()).replace(hour=22)
        + timedelta(hours=duration_hours),
        duration_seconds=duration_hours * 3600,
        enabled=True,
    )
    db.add(sess)
    await db.flush()
    return day, sess


# ---------------------------------------------------------------------------
# get_data_overview
# ---------------------------------------------------------------------------


class TestGetDataOverview:
    async def test_empty_database_returns_empty_devices(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.overview import get_data_overview

        result = await get_data_overview(async_db_session)
        assert result.devices == []
        assert result.total_sessions == 0
        assert not result.analysis_run

    async def test_device_and_sessions_appear_in_overview(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.overview import get_data_overview

        device = await _make_device(async_db_session)
        today = date(2024, 8, 1)
        await _make_day_session(async_db_session, device, today)
        await _make_day_session(async_db_session, device, today + timedelta(days=1))

        result = await get_data_overview(async_db_session)
        assert len(result.devices) == 1
        assert result.devices[0].manufacturer == "TestMfr"
        assert result.devices[0].session_count == 2
        assert result.total_sessions == 2
        assert result.date_range_start == today
        assert result.date_range_end == today + timedelta(days=1)

    async def test_analysis_run_false_when_no_analysis_results(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.overview import get_data_overview

        device = await _make_device(async_db_session)
        await _make_day_session(async_db_session, device, date(2024, 8, 1))

        result = await get_data_overview(async_db_session)
        assert not result.analysis_run
        assert result.analysis_session_count == 0


# ---------------------------------------------------------------------------
# get_settings_timeline
# ---------------------------------------------------------------------------


class TestGetSettingsTimeline:
    async def test_empty_database_returns_no_epochs(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.settings import get_settings_timeline

        result = await get_settings_timeline(
            async_db_session,
            date(2024, 1, 1),
            date(2024, 12, 31),
        )
        assert result.epochs == []
        assert result.total_epochs == 0

    async def test_epochs_filtered_to_date_range(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.database.models import Setting
        from snore.mcp.tools.settings import get_settings_timeline

        device = await _make_device(async_db_session)

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
        )
        # Only the March session epoch should appear
        assert result.total_epochs == 1


# ---------------------------------------------------------------------------
# get_nightly_summary
# ---------------------------------------------------------------------------


class TestGetNightlySummary:
    async def test_empty_database_returns_empty_nights(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 1, 1),
            date(2024, 1, 31),
        )
        assert result.nights == []
        assert result.total_nights == 0

    async def test_rera_fields_null_with_reason_when_analysis_absent(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session)
        await _make_day_session(async_db_session, device, date(2024, 8, 1), ahi=2.5)

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 1),
        )
        assert len(result.nights) == 1
        night = result.nights[0]
        assert night.rera_index is None
        assert night.rera_index_reason == "analysis_not_run"
        assert night.rdi is None
        assert night.rdi_reason == "analysis_not_run"

    async def test_ahi_populated_from_day_row(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session)
        await _make_day_session(
            async_db_session, device, date(2024, 8, 1), ahi=5.2, oai=1.0, cai=0.5
        )

        result = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 1),
        )
        night = result.nights[0]
        assert night.ahi == pytest.approx(5.2, abs=0.01)
        assert night.oai == pytest.approx(1.0, abs=0.01)

    async def test_compliance_block_present_in_range_mode(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session)
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
        )
        assert result.compliance is not None
        assert result.compliance.days_total == 5
        assert result.compliance.days_compliant == 5
        assert result.compliance.compliance_pct == 100.0

    async def test_compliance_below_threshold_counted_correctly(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session)
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
        )
        assert result.compliance is not None
        assert result.compliance.days_compliant == 3
        assert result.compliance.days_total == 5

    async def test_pagination_returns_correct_page(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.summary import get_nightly_summary

        device = await _make_device(async_db_session)
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
        )
        page2 = await get_nightly_summary(
            async_db_session,
            date(2024, 8, 1),
            date(2024, 8, 10),
            page=2,
            page_size=5,
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
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.tools.events import get_events

        with pytest.raises(ValidationError, match="No therapy data"):
            await get_events(async_db_session, date(2024, 1, 1))

    async def test_events_returned_for_session_date(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session)
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

        result = await get_events(async_db_session, target_date)
        assert result.total_events == 2
        types_returned = {e.event_type for e in result.events}
        assert types_returned == {"OA", "CA"}

    async def test_event_type_filter_applied(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session)
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

        result = await get_events(async_db_session, target_date, types=["OA"])
        assert result.total_events == 1
        assert result.events[0].event_type == "OA"

    async def test_min_duration_filter_applied(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session)
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

        result = await get_events(async_db_session, target_date, min_duration=10.0)
        assert result.total_events == 1
        assert result.events[0].duration_seconds == 30.0

    async def test_event_context_includes_minutes_since_start(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session)
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

        result = await get_events(async_db_session, target_date, include_context=True)
        assert result.total_events == 1
        ctx = result.events[0].context
        assert ctx is not None
        assert ctx.minutes_since_session_start == pytest.approx(45.0, abs=0.1)

    async def test_context_disabled_returns_no_context_block(
        self, async_db_session: AsyncSession
    ) -> None:
        from snore.mcp.tools.events import get_events

        device = await _make_device(async_db_session)
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

        result = await get_events(async_db_session, target_date, include_context=False)
        assert result.total_events == 1
        assert result.events[0].context is None
