"""FL/RERA experimental-disclaimer labeling on MCP surfaces.

The FL/RERA proxy metrics are read-time trend instruments, not device-scored
events. These tests pin the shared disclaimer constant onto the two surfaces MCP
clients see: registered tool descriptions (get_nightly_summary, compare_epochs)
and docs://schemas JSON-schema field descriptions (NightlyRow, EpochStats).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import fastmcp

from snore.constants import FL_RERA_EXPERIMENTAL_DISCLAIMER
from snore.mcp.schemas import EpochStats, NightlyRow
from snore.mcp.server import StaticRuntime, make_server


@asynccontextmanager
async def _fake_lifespan(*args: Any, **kwargs: Any) -> AsyncIterator[StaticRuntime]:  # noqa: RUF029
    yield StaticRuntime(base_scope_provider=MagicMock(), profile_id=1)


async def _tool_descriptions() -> dict[str, str]:
    mcp = make_server()
    with patch("snore.mcp.server._lifespan", _fake_lifespan):
        async with fastmcp.Client(mcp) as client:
            tools = await client.list_tools()
    return {t.name: (t.description or "") for t in tools}


def _field_description(model: type, field_name: str) -> str:
    return model.model_json_schema()["properties"][field_name].get("description", "")


class TestToolDescriptionsCarryDisclaimer:
    async def test_get_nightly_summary_description_contains_disclaimer(self) -> None:
        descriptions = await _tool_descriptions()
        description = descriptions["get_nightly_summary"]
        normalized_description = " ".join(description.split())

        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in description
        assert (
            "RDI here adds the experimental RERA-proxy index" in normalized_description
        )
        assert "rule-matched classified breaths" in normalized_description
        assert "confidence gate excludes fallback guesses" in normalized_description

    async def test_compare_epochs_description_contains_disclaimer(self) -> None:
        descriptions = await _tool_descriptions()

        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in descriptions["compare_epochs"]

    async def test_unrelated_tool_description_omits_disclaimer(self) -> None:
        descriptions = await _tool_descriptions()

        assert FL_RERA_EXPERIMENTAL_DISCLAIMER not in descriptions["get_data_overview"]


class TestNightlyRowSchemaLabels:
    def test_rera_index_description_contains_disclaimer(self) -> None:
        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in _field_description(
            NightlyRow, "rera_index"
        )

    def test_fl_class_ge4_pct_description_contains_disclaimer(self) -> None:
        description = _field_description(NightlyRow, "fl_class_ge4_pct")

        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in description
        assert "rule-matched" in description
        assert "excludes fallback guesses" in description

    def test_proxy_derived_rdi_description_contains_disclaimer(self) -> None:
        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in _field_description(NightlyRow, "rdi")

    def test_rera_proxy_count_description_contains_disclaimer(self) -> None:
        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in _field_description(
            NightlyRow, "rera_proxy_count"
        )


class TestEpochStatsSchemaLabels:
    def test_rera_proxy_count_description_contains_disclaimer(self) -> None:
        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in _field_description(
            EpochStats, "rera_proxy_count"
        )

    def test_flow_class_distribution_description_contains_disclaimer(self) -> None:
        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in _field_description(
            EpochStats, "flow_class_distribution"
        )

    def test_flow_class_distribution_fallback_description_contains_disclaimer(
        self,
    ) -> None:
        assert FL_RERA_EXPERIMENTAL_DISCLAIMER in _field_description(
            EpochStats, "flow_class_distribution_fallback"
        )
