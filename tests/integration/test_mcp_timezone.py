"""Integration tests for the user-declared profile timezone (A6).

A profile may declare an IANA timezone name.  When set, MCP responses carry
``timezone_status: "user_declared"`` plus ``timezone_name`` beside every tier-2
wall-clock anchor.  Timestamps stay naive — declaring a timezone must never
introduce a UTC offset or ``Z`` suffix into any wall-clock string.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import Event
from tests.integration.conftest import (
    _make_analysis_result,
    _make_day_session,
    _make_device,
)
from tests.integration.test_mcp_breath_table import _make_breath
from tests.integration.test_mcp_ca_analysis import _make_ca_event
from tests.integration.test_mcp_waveform import _make_waveform

TZ = "America/New_York"


def _assert_naive(wall_clock: str) -> None:
    """Tier-2 wall-clock strings stay offset-free even when a TZ is declared."""
    assert "+" not in wall_clock
    assert not wall_clock.endswith("Z")


@pytest.fixture
async def tz_profile(async_db_session: AsyncSession, async_test_profile: Any) -> Any:
    """The standard test profile with a declared IANA timezone."""
    async_test_profile.timezone = TZ
    await async_db_session.flush()
    return async_test_profile


class TestUserDeclaredTimezone:
    async def test_get_events_carries_user_declared_timezone(
        self, async_db_session: AsyncSession, tz_profile: Any
    ) -> None:
        from snore.mcp.tools.events import get_events  # noqa: PLC0415

        target_date = date(2024, 8, 18)
        device = await _make_device(async_db_session, tz_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=sess.start_time,
                duration_seconds=10.0,
            )
        )
        await async_db_session.flush()

        result = await get_events(
            async_db_session, target_date, profile_id=tz_profile.id
        )

        assert result.timezone_status == "user_declared"
        assert result.timezone_name == TZ
        ev = result.events[0]
        assert ev.timezone_status == "user_declared"
        assert ev.timezone_name == TZ
        _assert_naive(ev.start_time_wall_clock)
        _assert_naive(ev.session_start_wall_clock)

    async def test_get_breath_table_carries_user_declared_timezone(
        self, async_db_session: AsyncSession, tz_profile: Any
    ) -> None:
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 1)
        device = await _make_device(async_db_session, tz_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        ar = await _make_analysis_result(async_db_session, sess)
        await _make_breath(async_db_session, ar, sess, breath_number=1)
        await async_db_session.flush()

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=tz_profile.id,
            offset_start=0.0,
            offset_end=900.0,
        )

        assert result.timezone_status == "user_declared"
        assert result.timezone_name == TZ
        row = result.rows[0]
        assert row.timezone_status == "user_declared"
        assert row.timezone_name == TZ
        _assert_naive(row.session_start_wall_clock)

    async def test_find_windows_carries_user_declared_timezone(
        self, async_db_session: AsyncSession, tz_profile: Any
    ) -> None:
        from snore.mcp.tools.windows import find_windows  # noqa: PLC0415

        target_date = date(2024, 2, 1)
        device = await _make_device(async_db_session, tz_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        await _make_ca_event(async_db_session, sess, offset_seconds=300.0)

        result = await find_windows(
            async_db_session,
            target_date,
            profile_id=tz_profile.id,
            criterion="ca_centered",
        )

        assert len(result.windows) == 1
        win = result.windows[0]
        assert win.timezone_status == "user_declared"
        assert win.timezone_name == TZ
        _assert_naive(win.session_start_wall_clock)

    async def test_get_waveform_carries_user_declared_timezone(
        self, async_db_session: AsyncSession, tz_profile: Any
    ) -> None:
        from snore.mcp.tools.waveform import (  # noqa: PLC0415
            fetch_waveform_raw,
            waveform_response_from_raw,
        )

        target_date = date(2024, 3, 1)
        device = await _make_device(async_db_session, tz_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        await _make_waveform(async_db_session, sess, "flow", 25.0, "L/min", 750)

        raw = await fetch_waveform_raw(
            async_db_session,
            target_date,
            profile_id=tz_profile.id,
            offset_start=0.0,
            offset_end=10.0,
            window_cap_seconds=120.0,
        )
        response = waveform_response_from_raw(raw)

        assert response.timezone_status == "user_declared"
        assert response.timezone_name == TZ
        assert response.session_start_wall_clock is not None
        _assert_naive(response.session_start_wall_clock)

    async def test_get_ca_analysis_carries_user_declared_timezone(
        self, async_db_session: AsyncSession, tz_profile: Any
    ) -> None:
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )

        day_date = date(2025, 1, 15)
        device = await _make_device(async_db_session, tz_profile.id)
        _, sess = await _make_day_session(async_db_session, device, day_date)
        await _make_analysis_result(async_db_session, sess)
        await _make_ca_event(async_db_session, sess, offset_seconds=120.0)

        raw, caps = await fetch_ca_raw(async_db_session, day_date, tz_profile.id)
        result = ca_response_from_raw(raw, caps)

        assert len(result.ca_events) == 1
        ev = result.ca_events[0]
        assert ev.timezone_status == "user_declared"
        assert ev.timezone_name == TZ
        _assert_naive(ev.session_start_wall_clock)

    async def test_undeclared_profile_stays_unknown_with_null_name(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Control: no declared timezone → status "unknown", timezone_name null."""
        from snore.mcp.tools.events import get_events  # noqa: PLC0415

        target_date = date(2024, 8, 18)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        async_db_session.add(
            Event(
                session_id=sess.id,
                event_type="OA",
                start_time=sess.start_time,
                duration_seconds=10.0,
            )
        )
        await async_db_session.flush()

        result = await get_events(
            async_db_session, target_date, profile_id=async_test_profile.id
        )

        assert result.timezone_status == "unknown"
        assert result.timezone_name is None
        assert result.events[0].timezone_status == "unknown"
        assert result.events[0].timezone_name is None
