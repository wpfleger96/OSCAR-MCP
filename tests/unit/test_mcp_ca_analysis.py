"""Unit tests for the get_ca_analysis MCP tool fetch/compute pair.

Split into two groups:
  A) TestGetCaAnalysisAdapter — direct tests of the production composition
     (``fetch_ca_raw`` inside the DB scope, then pure ``ca_response_from_raw``)
     with a mock DB session and patched BreathService seams.
  B) TestGetCaAnalysisClient — client-level roundtrip tests via
     mcp_client_factory, exercising the registered get_ca_analysis server tool.
"""

from __future__ import annotations

import json

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import ToolError

# ---------------------------------------------------------------------------
# Module-level DTO builders (use real service types)
# ---------------------------------------------------------------------------


def _algo_versions() -> Any:
    from snore.analysis.shared.versioning import (  # noqa: PLC0415
        AlgorithmIdentity,
        AlgoVersions,
        AnalysisRunMetadata,
    )

    return AlgoVersions(
        identity=AlgorithmIdentity.current(),
        run=AnalysisRunMetadata(primary_mode="aasm", modes=["aasm"]),
    )


def _make_ca_detail(
    session_id: int = 1,
    offset_seconds: float = 120.0,
    duration_seconds: float | None = 10.0,
    preceding_mv_slope: float | None = 0.5,
    preceding_mv_reason: Any = None,
    ps_delivered_cmh2o: float | None = 12.0,
    ps_reason: Any = None,
    stability_index: float | None = 0.1,
    stability_reason: Any = None,
) -> Any:
    from snore.services.breath_service import CaDetail, TimezoneStatus  # noqa: PLC0415

    return CaDetail(
        session_id=session_id,
        session_start_wall_clock=datetime(2025, 1, 15, 22, 0, 0),
        timezone_status=TimezoneStatus.UNKNOWN,
        offset_seconds=offset_seconds,
        duration_seconds=duration_seconds,
        preceding_mv_slope=preceding_mv_slope,
        preceding_mv_reason=preceding_mv_reason,
        ps_delivered_cmh2o=ps_delivered_cmh2o,
        ps_reason=ps_reason,
        stability_index=stability_index,
        stability_reason=stability_reason,
    )


def _make_ca_analysis_result(**kwargs: Any) -> Any:
    from snore.analysis.shared.versioning import AlgorithmIdentity  # noqa: PLC0415
    from snore.services.breath_service import (  # noqa: PLC0415
        AnalysisStatus,
        CaAnalysisResult,
        DayAnalysisStatus,
        NullReason,
        SessionCoverage,
    )

    av = _algo_versions()
    defaults: dict[str, Any] = {
        "query_date": date(2025, 1, 15),
        "device_id": 3,
        "day_status": DayAnalysisStatus.OK,
        "session_coverage": [
            SessionCoverage(
                session_id=1, analysis_status=AnalysisStatus.OK, algo_versions=av
            )
        ],
        "algorithm_identity": AlgorithmIdentity.current(),
        "null_reason": None,
        "ca_events": [],
        "periodic_breathing_pct": None,
        "pb_reason": NullReason.NOT_AVAILABLE,
        "mv_rolling_variance": None,
        "mv_variance_reason": NullReason.NOT_AVAILABLE,
    }
    defaults.update(kwargs)
    return CaAnalysisResult(**defaults)


def _make_minimal_raw(device_id: int = 3) -> Any:
    """Minimal RawCaAnalysis (empty-day shape) for tests that mock compute_ca_analysis."""
    from snore.services.breath_service import (  # noqa: PLC0415
        DayAnalysisStatus,
        NullReason,
        RawCaAnalysis,
    )

    return RawCaAnalysis(
        therapy_date=date(2025, 1, 15),
        device_id=device_id,
        session_data=[],
        day_status=DayAnalysisStatus.NOT_RUN,
        algorithm_identity=None,
        null_reason=NullReason.ANALYSIS_NOT_RUN,
    )


# ---------------------------------------------------------------------------
# A) Adapter tests — call fetch_ca_raw + ca_response_from_raw directly
# ---------------------------------------------------------------------------


class TestGetCaAnalysisAdapter:
    async def test_happy_path_two_ca_events_renamed_fields(
        self, mock_db_session: Any
    ) -> None:
        """Two CA events: renamed fields present, wall-clock isoformat, timezone unknown,
        offsets pass through."""
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )

        ev1 = _make_ca_detail(session_id=1, offset_seconds=60.0, preceding_mv_slope=0.4)
        ev2 = _make_ca_detail(
            session_id=1, offset_seconds=300.0, preceding_mv_slope=0.8
        )
        mock_result = _make_ca_analysis_result(ca_events=[ev1, ev2])

        with (
            patch(
                "snore.services.breath_service.BreathService.fetch_ca_analysis",
                AsyncMock(return_value=_make_minimal_raw()),
            ),
            patch(
                "snore.services.breath_service.compute_ca_analysis",
                MagicMock(return_value=mock_result),
            ),
        ):
            raw, caps = await fetch_ca_raw(
                mock_db_session, date(2025, 1, 15), profile_id=1
            )
            result = ca_response_from_raw(raw, caps)

        assert result.query_date == "2025-01-15"
        assert result.device_id == 3
        assert result.day_status == "ok"
        assert len(result.ca_events) == 2

        e = result.ca_events[0]
        # Field renames
        assert hasattr(e, "preceding_mv_slope_lpm_per_min")
        assert hasattr(e, "preceding_mv_slope_reason")
        assert e.preceding_mv_slope_lpm_per_min == pytest.approx(0.4)
        assert e.preceding_mv_slope_reason is None
        # Wall-clock as isoformat string
        assert e.session_start_wall_clock == "2025-01-15T22:00:00"
        assert e.timezone_status == "unknown"
        # Offsets pass through
        assert e.offset_seconds == pytest.approx(60.0)
        assert result.ca_events[1].offset_seconds == pytest.approx(300.0)

    async def test_null_pb_reason_serialised_as_string(
        self, mock_db_session: Any
    ) -> None:
        """periodic_breathing_pct=None + pb_reason=ANALYSIS_NOT_RUN →
        pb_reason string 'analysis_not_run' in response."""
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )
        from snore.services.breath_service import NullReason  # noqa: PLC0415

        mock_result = _make_ca_analysis_result(
            periodic_breathing_pct=None,
            pb_reason=NullReason.ANALYSIS_NOT_RUN,
        )
        with (
            patch(
                "snore.services.breath_service.BreathService.fetch_ca_analysis",
                AsyncMock(return_value=_make_minimal_raw()),
            ),
            patch(
                "snore.services.breath_service.compute_ca_analysis",
                MagicMock(return_value=mock_result),
            ),
        ):
            raw, caps = await fetch_ca_raw(
                mock_db_session, date(2025, 1, 15), profile_id=1
            )
            result = ca_response_from_raw(raw, caps)

        assert result.periodic_breathing_pct is None
        assert result.pb_reason == "analysis_not_run"

    async def test_day_status_not_run_ca_events_still_returned(
        self, mock_db_session: Any
    ) -> None:
        """day_status=NOT_RUN with non-empty ca_events → events still present
        (event-anchored; import-time events are independent of analysis status)."""
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )
        from snore.services.breath_service import (  # noqa: PLC0415
            AnalysisStatus,
            DayAnalysisStatus,
            NullReason,
            SessionCoverage,
        )

        ev = _make_ca_detail(offset_seconds=180.0)
        mock_result = _make_ca_analysis_result(
            day_status=DayAnalysisStatus.NOT_RUN,
            session_coverage=[
                SessionCoverage(
                    session_id=1,
                    analysis_status=AnalysisStatus.NOT_RUN,
                    algo_versions=None,
                )
            ],
            algorithm_identity=None,
            null_reason=NullReason.ANALYSIS_NOT_RUN,
            ca_events=[ev],
        )
        with (
            patch(
                "snore.services.breath_service.BreathService.fetch_ca_analysis",
                AsyncMock(return_value=_make_minimal_raw()),
            ),
            patch(
                "snore.services.breath_service.compute_ca_analysis",
                MagicMock(return_value=mock_result),
            ),
        ):
            raw, caps = await fetch_ca_raw(
                mock_db_session, date(2025, 1, 15), profile_id=1
            )
            result = ca_response_from_raw(raw, caps)

        assert result.day_status == "not_run"
        assert result.null_reason == "analysis_not_run"
        assert len(result.ca_events) == 1
        assert result.ca_events[0].offset_seconds == pytest.approx(180.0)

    async def test_mixed_version_nulls_algorithm_identity_and_null_reason(
        self, mock_db_session: Any
    ) -> None:
        """MIXED_VERSION → algorithm_identity=None, null_reason='algo_version_mismatch';
        night-level fields null; ca_events still returned."""
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )
        from snore.services.breath_service import (  # noqa: PLC0415
            DayAnalysisStatus,
            NullReason,
        )

        ev = _make_ca_detail()
        mock_result = _make_ca_analysis_result(
            day_status=DayAnalysisStatus.MIXED_VERSION,
            algorithm_identity=None,
            null_reason=NullReason.ALGO_VERSION_MISMATCH,
            ca_events=[ev],
            periodic_breathing_pct=None,
            pb_reason=NullReason.ALGO_VERSION_MISMATCH,
            mv_rolling_variance=None,
            mv_variance_reason=NullReason.ALGO_VERSION_MISMATCH,
        )
        with (
            patch(
                "snore.services.breath_service.BreathService.fetch_ca_analysis",
                AsyncMock(return_value=_make_minimal_raw()),
            ),
            patch(
                "snore.services.breath_service.compute_ca_analysis",
                MagicMock(return_value=mock_result),
            ),
        ):
            raw, caps = await fetch_ca_raw(
                mock_db_session, date(2025, 1, 15), profile_id=1
            )
            result = ca_response_from_raw(raw, caps)

        assert result.day_status == "mixed_version"
        assert result.algorithm_identity is None
        assert result.null_reason == "algo_version_mismatch"
        assert result.periodic_breathing_pct is None
        assert result.pb_reason == "algo_version_mismatch"
        assert result.mv_rolling_variance is None
        assert result.mv_variance_reason == "algo_version_mismatch"
        # Events still returned (event-anchored)
        assert len(result.ca_events) == 1

    async def test_session_coverage_entries_mapped_correctly(
        self, mock_db_session: Any
    ) -> None:
        """session_coverage: session_id, analysis_status str, algo_versions dict."""
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )
        from snore.services.breath_service import (  # noqa: PLC0415
            AnalysisStatus,
            SessionCoverage,
        )

        av = _algo_versions()
        mock_result = _make_ca_analysis_result(
            session_coverage=[
                SessionCoverage(
                    session_id=7,
                    analysis_status=AnalysisStatus.OK,
                    algo_versions=av,
                ),
                SessionCoverage(
                    session_id=8,
                    analysis_status=AnalysisStatus.NOT_RUN,
                    algo_versions=None,
                ),
            ]
        )
        with (
            patch(
                "snore.services.breath_service.BreathService.fetch_ca_analysis",
                AsyncMock(return_value=_make_minimal_raw()),
            ),
            patch(
                "snore.services.breath_service.compute_ca_analysis",
                MagicMock(return_value=mock_result),
            ),
        ):
            raw, caps = await fetch_ca_raw(
                mock_db_session, date(2025, 1, 15), profile_id=1
            )
            result = ca_response_from_raw(raw, caps)

        assert len(result.session_coverage) == 2
        c0 = result.session_coverage[0]
        assert c0.session_id == 7
        assert c0.analysis_status == "ok"
        assert isinstance(c0.algo_versions, dict)
        assert "identity" in c0.algo_versions
        assert "run" in c0.algo_versions

        c1 = result.session_coverage[1]
        assert c1.session_id == 8
        assert c1.analysis_status == "not_run"
        assert c1.algo_versions is None

    async def test_device_capabilities_populated_on_happy_path(
        self, mock_db_session: Any
    ) -> None:
        """Happy path: device_capabilities is present (non-None) when build_device_capabilities
        returns a value."""
        from snore.mcp.schemas import DeviceCapabilities  # noqa: PLC0415
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )

        mock_result = _make_ca_analysis_result()
        mock_caps = DeviceCapabilities(
            manufacturer="TestMfr",
            model="TestModel",
            serial_number="SN001",
            has_flow_waveform=True,
            has_pressure_waveform=True,
            has_leak_waveform=True,
            has_spo2=False,
            has_events=True,
            has_analysis=True,
        )

        with (
            patch(
                "snore.services.breath_service.BreathService.fetch_ca_analysis",
                AsyncMock(return_value=_make_minimal_raw()),
            ),
            patch(
                "snore.mcp.tools.ca_analysis.build_device_capabilities",
                AsyncMock(return_value=mock_caps),
            ),
            patch(
                "snore.services.breath_service.compute_ca_analysis",
                MagicMock(return_value=mock_result),
            ),
        ):
            raw, caps = await fetch_ca_raw(
                mock_db_session, date(2025, 1, 15), profile_id=1
            )
            result = ca_response_from_raw(raw, caps)

        assert result.device_capabilities is not None
        assert result.device_capabilities.has_flow_waveform is True
        assert result.device_capabilities.manufacturer == "TestMfr"

    async def test_device_ambiguity_error_maps_to_validation_error_listing_devices(
        self, mock_db_session: Any
    ) -> None:
        """DeviceAmbiguityError → snore.mcp.errors.ValidationError with device IDs listed;
        profile_id NOT in message."""
        from snore.mcp.errors import ValidationError  # noqa: PLC0415
        from snore.mcp.tools.ca_analysis import fetch_ca_raw  # noqa: PLC0415
        from snore.services.breath_service import DeviceAmbiguityError  # noqa: PLC0415

        exc = DeviceAmbiguityError(
            therapy_date=date(2025, 1, 15),
            profile_id=99,
            owned_device_ids=[3, 5],
            device_serials={3: "SN001", 5: "SN002"},
        )
        with patch(
            "snore.services.breath_service.BreathService.fetch_ca_analysis",
            AsyncMock(side_effect=exc),
        ):
            with pytest.raises(ValidationError) as exc_info:
                await fetch_ca_raw(mock_db_session, date(2025, 1, 15), profile_id=99)

        msg = str(exc_info.value)
        assert "device_id=3" in msg
        assert "device_id=5" in msg
        # Profile ID must NOT appear in user-facing error messages
        assert "99" not in msg

    async def test_device_not_owned_error_message_contains_device_id_not_profile_id(
        self, mock_db_session: Any
    ) -> None:
        """DeviceNotOwnedError → ValidationError with device_id= in message,
        profile_id absent."""
        from snore.mcp.errors import ValidationError  # noqa: PLC0415
        from snore.mcp.tools.ca_analysis import fetch_ca_raw  # noqa: PLC0415
        from snore.services.breath_service import DeviceNotOwnedError  # noqa: PLC0415

        exc = DeviceNotOwnedError(device_id=42, profile_id=99)
        with patch(
            "snore.services.breath_service.BreathService.fetch_ca_analysis",
            AsyncMock(side_effect=exc),
        ):
            with pytest.raises(ValidationError) as exc_info:
                await fetch_ca_raw(
                    mock_db_session, date(2025, 1, 15), profile_id=99, device_id=42
                )

        msg = str(exc_info.value)
        assert "device_id=42" in msg
        assert "99" not in msg


# ---------------------------------------------------------------------------
# B) Client-level roundtrip tests via the registered get_ca_analysis tool
# ---------------------------------------------------------------------------


class TestGetCaAnalysisClient:
    async def test_roundtrip_wire_json_matches_adapter_result(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Happy path: mocked service DTO → correct JSON payload.

        Patches BreathService.fetch_ca_analysis (returning a minimal RawCaAnalysis)
        and compute_ca_analysis (returning mock_result) so the pure ca_response_from_raw
        mapping runs against the mocked CaAnalysisResult.
        """
        from snore.services.breath_service import NullReason  # noqa: PLC0415

        ev = _make_ca_detail(
            session_id=1,
            offset_seconds=120.0,
            duration_seconds=15.0,
            preceding_mv_slope=0.5,
            preceding_mv_reason=None,
        )
        mock_result = _make_ca_analysis_result(
            ca_events=[ev],
            periodic_breathing_pct=None,
            pb_reason=NullReason.NOT_AVAILABLE,
        )
        patch_fetch = patch(
            "snore.services.breath_service.BreathService.fetch_ca_analysis",
            AsyncMock(return_value=_make_minimal_raw()),
        )
        patch_compute = patch(
            "snore.services.breath_service.compute_ca_analysis",
            MagicMock(return_value=mock_result),
        )
        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_fetch, patch_compute]
        ) as client:
            result = await client.call_tool(
                "get_ca_analysis",
                {"date": "2025-01-15"},
            )

        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["query_date"] == "2025-01-15"
        assert payload["device_id"] == 3
        assert payload["day_status"] == "ok"
        assert len(payload["ca_events"]) == 1
        ev_out = payload["ca_events"][0]
        assert ev_out["preceding_mv_slope_lpm_per_min"] == pytest.approx(0.5)
        assert ev_out["preceding_mv_slope_reason"] is None
        assert ev_out["session_start_wall_clock"] == "2025-01-15T22:00:00"
        assert ev_out["timezone_status"] == "unknown"
        assert ev_out["offset_seconds"] == pytest.approx(120.0)
        assert payload["pb_reason"] == "not_available"

    async def test_invalid_date_string_raises_tool_error(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Invalid date string → ToolError."""
        async with mcp_client_factory(mock_db_session) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "get_ca_analysis",
                    {"date": "not-a-date"},
                )

    async def test_no_sessions_in_range_produces_polished_error(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """NoSessionsInRangeError from service → ToolError with polished date message.

        The error must say 'No therapy data found for date <date>' and include
        the get_data_overview hint.  It must NOT expose the raw internal message
        'No sessions found in range'.
        """
        from snore.services.breath_service import (
            NoSessionsInRangeError,  # noqa: PLC0415
        )

        exc = NoSessionsInRangeError(
            date(2026, 1, 15),
            date(2026, 1, 15),
        )
        with patch(
            "snore.services.breath_service.BreathService.fetch_ca_analysis",
            AsyncMock(side_effect=exc),
        ):
            async with mcp_client_factory(mock_db_session) as client:
                with pytest.raises(ToolError) as exc_info:
                    await client.call_tool(
                        "get_ca_analysis",
                        {"date": "2026-01-15"},
                    )

        msg = str(exc_info.value)
        assert "No therapy data found for date 2026-01-15" in msg
        assert "get_data_overview" in msg
        assert "No sessions found in range" not in msg

    async def test_size_guard_raises_tool_error(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """When JSON exceeds RESPONSE_SIZE_LIMIT → ToolError advises narrowing."""
        ev = _make_ca_detail()
        mock_result = _make_ca_analysis_result(ca_events=[ev])
        patch_fetch = patch(
            "snore.services.breath_service.BreathService.fetch_ca_analysis",
            AsyncMock(return_value=_make_minimal_raw()),
        )
        patch_compute = patch(
            "snore.services.breath_service.compute_ca_analysis",
            MagicMock(return_value=mock_result),
        )
        patch_size = patch("snore.mcp.server.RESPONSE_SIZE_LIMIT", new=1)

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_fetch, patch_compute, patch_size]
        ) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "get_ca_analysis",
                    {"date": "2025-01-15"},
                )
