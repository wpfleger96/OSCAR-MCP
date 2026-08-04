"""FastMCP in-memory roundtrip tests for compare_epochs tool.

Each test calls through a connected fastmcp.Client backed by a mock DB session and a
patched BreathService.  The suite verifies: server wiring (validate_epoch_count fires
before the service is called), adapter validation (date parsing, metric validation),
and response mapping (isoformat dates, string flow_class keys, enum → str).

Patching: ``snore.services.breath_service.BreathService.compare_epochs`` is patched
via ``extra_patches`` passed to ``mcp_client_factory`` from conftest.
"""

from __future__ import annotations

import json

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from fastmcp.exceptions import ToolError

# ---------------------------------------------------------------------------
# Shared mock result builders
# ---------------------------------------------------------------------------


def _make_null_dist() -> Any:
    from snore.services.breath_service import DistributionStats  # noqa: PLC0415

    return DistributionStats(median=None, iqr=None, p95=None, n_breaths=0, n_nights=0)


def _make_populated_dist(median: float = 0.5) -> Any:
    from snore.services.breath_service import DistributionStats  # noqa: PLC0415

    return DistributionStats(median=median, iqr=0.1, p95=0.8, n_breaths=100, n_nights=2)


def _make_epoch_stats(
    label: str = "Before",
    date_start: date = date(2025, 1, 1),
    date_end: date = date(2025, 1, 31),
    null_reason: Any = None,
    algorithm_identity: Any = None,
    flow_class_distribution: dict[int, int] | None = None,
    rera_reason: Any = None,
    rera_proxy_count: int | None = 0,
) -> Any:
    from snore.analysis.shared.versioning import (  # noqa: PLC0415
        AlgorithmIdentity,
    )
    from snore.services.breath_service import (  # noqa: PLC0415
        EpochBreathStats,
    )

    if algorithm_identity is None and null_reason is None:
        algorithm_identity = AlgorithmIdentity.current()

    null_dist = _make_null_dist()
    pop_dist = _make_populated_dist()
    has_data = null_reason is None

    return EpochBreathStats(
        label=label,
        date_start=date_start,
        date_end=date_end,
        nights_with_data=2 if has_data else 0,
        nights_missing_analysis=0,
        algorithm_identity=algorithm_identity,
        null_reason=null_reason,
        primary_mode="aasm" if has_data else None,
        mid_insp_flattening=pop_dist if has_data else null_dist,
        flatness_index=pop_dist if has_data else null_dist,
        flow_class_distribution=flow_class_distribution
        if flow_class_distribution is not None
        else ({3: 10, 4: 5} if has_data else {}),
        tidal_volume_ml=pop_dist if has_data else null_dist,
        ie_ratio=pop_dist if has_data else null_dist,
        rera_proxy_count=rera_proxy_count if has_data else None,
        rera_reason=rera_reason,
        rx_settings={"pressure_min": "4.0"} if has_data else {},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCompareEpochsRoundtrip:
    async def test_happy_path_two_epochs_returns_populated_distributions(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Two epochs with data: wire JSON has isoformat dates, string flow_class keys,
        algorithm_identity dict, and null null_reason."""
        from snore.services.breath_service import CompareEpochsResult  # noqa: PLC0415

        mock_result = CompareEpochsResult(
            epochs=[
                _make_epoch_stats("Before", date(2025, 1, 1), date(2025, 1, 31)),
                _make_epoch_stats("After", date(2025, 2, 1), date(2025, 2, 28)),
            ],
            null_reason=None,
            rx_violations=[],
        )

        service_mock = AsyncMock(return_value=mock_result)
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            result = await client.call_tool(
                "compare_epochs",
                {
                    "epochs": [
                        {
                            "label": "Before",
                            "date_start": "2025-01-01",
                            "date_end": "2025-01-31",
                        },
                        {
                            "label": "After",
                            "date_start": "2025-02-01",
                            "date_end": "2025-02-28",
                        },
                    ]
                },
            )

        assert not result.is_error
        payload = json.loads(result.content[0].text)

        assert payload["null_reason"] is None
        assert len(payload["epochs"]) == 2

        ep = payload["epochs"][0]
        # Dates must be isoformat strings
        assert ep["date_start"] == "2025-01-01"
        assert ep["date_end"] == "2025-01-31"
        # flow_class keys must be strings, not ints
        assert all(isinstance(k, str) for k in ep["flow_class_distribution"])
        assert ep["flow_class_distribution"]["3"] == 10
        assert ep["flow_class_distribution"]["4"] == 5
        # algorithm_identity must be a dict (model_dump result)
        assert isinstance(ep["algorithm_identity"], dict)
        assert "segmenter" in ep["algorithm_identity"]
        # null_reason must be null when data is present
        assert ep["null_reason"] is None
        # distributions populated
        assert ep["mid_insp_flattening"]["median"] is not None
        assert ep["rx_settings"]["pressure_min"] == "4.0"

    async def test_rx_violation_refusal_pass_through_as_success(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Service returning RX_CHANGED_WITHIN_EPOCH produces a SUCCESS response
        (not a ToolError) with null_reason and rx_violations populated, and
        change_dates in isoformat."""
        from snore.services.breath_service import (  # noqa: PLC0415
            CompareEpochsResult,
            EpochRxViolation,
            NullReason,
        )

        violation = EpochRxViolation(
            epoch_label="Before",
            changed_keys=["pressure_min"],
            change_dates=[date(2025, 1, 15)],
        )
        null_stats = _make_epoch_stats(
            "Before",
            null_reason=NullReason.RX_CHANGED_WITHIN_EPOCH,
            algorithm_identity=None,
        )

        mock_result = CompareEpochsResult(
            epochs=[null_stats],
            null_reason=NullReason.RX_CHANGED_WITHIN_EPOCH,
            rx_violations=[violation],
        )

        service_mock = AsyncMock(return_value=mock_result)
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            result = await client.call_tool(
                "compare_epochs",
                {
                    "epochs": [
                        {
                            "label": "Before",
                            "date_start": "2025-01-01",
                            "date_end": "2025-01-31",
                        }
                    ]
                },
            )

        assert not result.is_error
        payload = json.loads(result.content[0].text)

        # Top-level refusal reason
        assert payload["null_reason"] == "rx_changed_within_epoch"
        # Per-epoch reason
        assert payload["epochs"][0]["null_reason"] == "rx_changed_within_epoch"
        # Violation row present
        assert len(payload["rx_violations"]) == 1
        v = payload["rx_violations"][0]
        assert v["epoch_label"] == "Before"
        assert "pressure_min" in v["changed_keys"]
        # change_dates must be isoformat strings
        assert v["change_dates"] == ["2025-01-15"]

    async def test_seven_epochs_raises_tool_error_before_service_called(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """epochs list with 7 entries raises ToolError from validate_epoch_count;
        BreathService.compare_epochs must not be called."""
        service_mock = AsyncMock()
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            with pytest.raises(ToolError, match="7"):
                await client.call_tool(
                    "compare_epochs",
                    {
                        "epochs": [
                            {
                                "label": f"E{i}",
                                "date_start": "2025-01-01",
                                "date_end": "2025-01-31",
                            }
                            for i in range(7)
                        ]
                    },
                )

        service_mock.assert_not_called()

    async def test_date_start_after_date_end_raises_tool_error_with_param_name(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Epoch with date_start > date_end raises ToolError naming the epoch's
        date_start parameter by index."""
        service_mock = AsyncMock()
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(
                    "compare_epochs",
                    {
                        "epochs": [
                            {
                                "label": "E0",
                                "date_start": "2025-01-31",
                                "date_end": "2025-01-01",
                            }
                        ]
                    },
                )

        error_text = str(exc_info.value)
        assert "epochs[0].date_start" in error_text

        service_mock.assert_not_called()

    async def test_malformed_date_string_raises_tool_error_with_format_hint(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Non-ISO date string raises ToolError mentioning the expected YYYY-MM-DD format."""
        service_mock = AsyncMock()
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(
                    "compare_epochs",
                    {
                        "epochs": [
                            {
                                "label": "E0",
                                "date_start": "Jan 1 2025",
                                "date_end": "2025-01-31",
                            }
                        ]
                    },
                )

        assert "YYYY-MM-DD" in str(exc_info.value)
        service_mock.assert_not_called()

    async def test_unknown_metric_raises_tool_error_listing_valid_metrics(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """Unknown metric string raises ToolError naming the invalid metric and
        listing all four valid metric names; service must not be called."""
        service_mock = AsyncMock()
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(
                    "compare_epochs",
                    {
                        "epochs": [
                            {
                                "label": "E0",
                                "date_start": "2025-01-01",
                                "date_end": "2025-01-31",
                            }
                        ],
                        "metrics": ["bad_metric"],
                    },
                )

        error_text = str(exc_info.value)
        assert "bad_metric" in error_text
        assert "mid_insp_flattening" in error_text
        assert "flatness_index" in error_text
        assert "tidal_volume_ml" in error_text
        assert "ie_ratio" in error_text

        service_mock.assert_not_called()

    async def test_ie_ratio_metric_passed_to_service_as_enum(
        self, mock_db_session: Any, mcp_client_factory: Any
    ) -> None:
        """metrics=['ie_ratio'] in the tool call is converted to
        [DistributionMetric.IE_RATIO] and passed to BreathService.compare_epochs."""
        from snore.services.breath_service import (  # noqa: PLC0415
            CompareEpochsResult,
            DistributionMetric,
            NullReason,
        )

        mock_result = CompareEpochsResult(
            epochs=[
                _make_epoch_stats(
                    "Ep1",
                    null_reason=NullReason.NO_DATA_IN_RANGE,
                    algorithm_identity=None,
                )
            ],
            null_reason=NullReason.NO_DATA_IN_RANGE,
            rx_violations=[],
        )

        service_mock = AsyncMock(return_value=mock_result)
        patch_cm = patch(
            "snore.services.breath_service.BreathService.compare_epochs", service_mock
        )

        async with mcp_client_factory(
            mock_db_session, extra_patches=[patch_cm]
        ) as client:
            result = await client.call_tool(
                "compare_epochs",
                {
                    "epochs": [
                        {
                            "label": "Ep1",
                            "date_start": "2025-01-01",
                            "date_end": "2025-01-31",
                        }
                    ],
                    "metrics": ["ie_ratio"],
                },
            )

        assert not result.is_error
        assert service_mock.called
        # metrics is passed as a keyword argument regardless of how self is bound
        assert service_mock.call_args.kwargs.get("metrics") == [
            DistributionMetric.IE_RATIO
        ]
