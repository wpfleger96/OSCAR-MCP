"""FastMCP in-memory roundtrip tests for SNORE MCP tools.

Each test exercises the tool → BreathService / service layer → mock DB path
by calling mcp.call_tool() with _scope_provider and _profile_id overridden
at the module level.  This verifies the full server wiring (error boundary,
size guard, JSON serialization) not just the tool impl functions.

Pattern:
  1. Build the server via make_server() (no lifespan starts).
  2. Patch snore.mcp.server._scope_provider to return a mock AsyncSession.
  3. Patch snore.mcp.server._profile_id to a test profile id.
  4. Patch BreathService / DeviceService / RxTracker methods to return
     predetermined data.
  5. Call await mcp.call_tool("tool_name", {...}).
  6. Assert is_error == False and result content is parseable JSON.
"""

from __future__ import annotations

import json

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_session() -> MagicMock:
    """Return a minimal AsyncSession mock that satisfies scalar queries."""
    session = MagicMock()
    # scalar_one / scalar_one_or_none / scalars().all()
    result_mock = MagicMock()
    result_mock.scalar_one.return_value = 0
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    result_mock.scalars.return_value.first.return_value = None
    result_mock.one.return_value = (0, None, None)
    result_mock.all.return_value = []
    session.execute = AsyncMock(return_value=result_mock)
    return session


@asynccontextmanager
async def _mock_scope(session: MagicMock) -> Any:
    yield session


def _make_server() -> Any:
    from snore.mcp.server import make_server  # noqa: PLC0415

    return make_server()


# ---------------------------------------------------------------------------
# get_data_overview
# ---------------------------------------------------------------------------


class TestGetDataOverviewRoundtrip:
    async def test_empty_db_returns_empty_overview(self) -> None:
        """get_data_overview on empty DB: no error, devices=[]."""
        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        # DeviceService.list_devices() returns [] → overview returns empty
        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            patch(
                "snore.services.device_service.DeviceService.list_devices",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await mcp.call_tool("get_data_overview", {})

        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["devices"] == []
        assert payload["total_sessions"] == 0
        assert payload["analysis_run"] is False

    async def test_single_device_appears_in_overview(self) -> None:
        """get_data_overview with one device returns device info."""
        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        mock_device = MagicMock()
        mock_device.id = 1
        mock_device.manufacturer = "ResMed"
        mock_device.model = "AirCurve 11"
        mock_device.serial_number = "SN123"

        # scalar_one for analysis count = 0, one() for per-device stats
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        stats_result = MagicMock()
        stats_result.one.return_value = (2, None, None)

        mode_result = MagicMock()
        mode_result.scalars.return_value.all.return_value = []

        waveform_result = MagicMock()
        waveform_result.scalars.return_value.all.return_value = []

        event_result = MagicMock()
        event_result.scalars.return_value.all.return_value = []

        session.execute = AsyncMock(
            side_effect=[
                count_result,  # analysis count
                stats_result,  # per-device stats
                mode_result,  # therapy modes
                waveform_result,  # waveform types
                event_result,  # event types
            ]
        )

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            patch(
                "snore.services.device_service.DeviceService.list_devices",
                new_callable=AsyncMock,
                return_value=[mock_device],
            ),
            patch(
                "snore.services.breath_service.BreathService.get_device_capabilities",
                new_callable=AsyncMock,
                side_effect=Exception("no breaths"),
            ),
        ):
            result = await mcp.call_tool("get_data_overview", {})

        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert len(payload["devices"]) == 1
        assert payload["devices"][0]["manufacturer"] == "ResMed"


# ---------------------------------------------------------------------------
# get_settings_timeline
# ---------------------------------------------------------------------------


class TestGetSettingsTimelineRoundtrip:
    async def test_empty_range_returns_no_epochs(self) -> None:
        """get_settings_timeline with no data: no error, epochs=[]."""
        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            patch(
                "snore.analysis.rx_tracker.RxTracker.get_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = await mcp.call_tool(
                "get_settings_timeline",
                {"start": "2024-01-01", "end": "2024-12-31"},
            )

        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["epochs"] == []
        assert payload["total_epochs"] == 0

    async def test_invalid_date_range_returns_error(self) -> None:
        """get_settings_timeline with end < start: ToolError raised."""
        from fastmcp.exceptions import ToolError

        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            pytest.raises(ToolError),
        ):
            await mcp.call_tool(
                "get_settings_timeline",
                {"start": "2024-12-31", "end": "2024-01-01"},
            )


# ---------------------------------------------------------------------------
# get_nightly_summary
# ---------------------------------------------------------------------------


class TestGetNightlySummaryRoundtrip:
    async def test_empty_range_returns_empty_nights(self) -> None:
        """get_nightly_summary with no Day rows: no error, nights=[]."""
        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            patch(
                "snore.services.breath_service.BreathService.get_nightly_range_summary",
                new_callable=AsyncMock,
                side_effect=ValueError("no sessions"),
            ),
        ):
            result = await mcp.call_tool(
                "get_nightly_summary",
                {"start": "2024-01-01", "end": "2024-01-31"},
            )

        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["nights"] == []
        assert payload["total_nights"] == 0

    async def test_invalid_page_returns_error(self) -> None:
        """get_nightly_summary with page=0: ToolError raised."""
        from fastmcp.exceptions import ToolError

        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            pytest.raises(ToolError),
        ):
            await mcp.call_tool(
                "get_nightly_summary",
                {"start": "2024-01-01", "end": "2024-01-31", "page": 0},
            )


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------


class TestGetEventsRoundtrip:
    async def test_missing_date_returns_error(self) -> None:
        """get_events for a date with no data: ToolError raised."""
        from fastmcp.exceptions import ToolError

        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            patch(
                "snore.services.breath_service.BreathService.get_contextual_events",
                new_callable=AsyncMock,
                side_effect=ValueError("no sessions for date"),
            ),
            pytest.raises(ToolError, match="No therapy data"),
        ):
            await mcp.call_tool("get_events", {"date": "2024-01-01"})

    async def test_events_returned_for_date(self) -> None:
        """get_events with contextual events: no error, events list populated."""
        from datetime import datetime

        import snore.mcp.server as srv

        from snore.services.breath_service import (
            ContextualEvent,
            TimezoneStatus,
        )

        mcp = _make_server()
        session = _make_mock_session()

        ev = ContextualEvent(
            session_id=42,
            session_start_wall_clock=datetime(2024, 1, 1, 22, 0, 0),
            event_type="CA",
            event_start_wall_clock=datetime(2024, 1, 1, 22, 30, 0),
            timezone_status=TimezoneStatus.UNKNOWN,
            offset_seconds=1800.0,
            duration_seconds=20.0,
            pressure_at_event_cmh2o=8.2,
            pressure_reason=None,
            leak_at_event_lpm=2.1,
            leak_reason=None,
            mv_prior_120s_lpm=5.8,
            mv_reason=None,
            minutes_since_session_start=30.0,
        )

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            patch(
                "snore.services.breath_service.BreathService.get_contextual_events",
                new_callable=AsyncMock,
                return_value=[ev],
            ),
        ):
            result = await mcp.call_tool(
                "get_events", {"date": "2024-01-01", "include_context": True}
            )

        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["total_events"] == 1
        assert payload["events"][0]["event_type"] == "CA"
        assert payload["events"][0]["offset_seconds"] == 1800.0
        ctx = payload["events"][0]["context"]
        assert ctx is not None
        assert ctx["pressure_at_event_cmh2o"] == pytest.approx(8.2)
        assert ctx["mv_prior_120s_lpm"] == pytest.approx(5.8)

    async def test_invalid_date_format_returns_error(self) -> None:
        """get_events with non-ISO date: ToolError raised."""
        from fastmcp.exceptions import ToolError

        import snore.mcp.server as srv

        mcp = _make_server()
        session = _make_mock_session()

        with (
            patch.object(srv, "_scope_provider", lambda: _mock_scope(session)),
            patch.object(srv, "_profile_id", 1),
            pytest.raises(ToolError),
        ):
            await mcp.call_tool("get_events", {"date": "not-a-date"})
