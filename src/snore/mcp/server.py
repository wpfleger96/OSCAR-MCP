"""SNORE MCP Server.

Third presentation layer over the async service layer — a peer of the CLI and
FastAPI, never a place where analysis logic lives.

Design doctrine:
  - Tiered data access: overview → summary → events → breath table → raw waveform
  - Compute server-side, return compact JSON; units on every field
  - Data-quality flags / null + reason everywhere (G2)
  - Stateless: no module-global state; DB via lifespan-provided session factory (G3)
  - Profile-parameterized: profiles shape instructions only, not data (G1)
  - Vendor dispatch stays in the parser/service layer (G4)
"""

from __future__ import annotations

import json
import logging

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from snore.database.session import (
    cleanup_database,
    init_database_from_url,
    session_scope,
)
from snore.database.target import DatabaseTarget
from snore.mcp.errors import ValidationError
from snore.mcp.profiles import ClinicalProfile, get_profile
from snore.mcp.schemas import SCHEMA_MODEL_MAP, model_to_schema
from snore.mcp.validation import parse_date, parse_date_range

logger = logging.getLogger(__name__)

# Module-level profile holder — set during lifespan, read-only from tools.
# This is the ONLY module-level state permitted in this package (G3).
_active_profile: ClinicalProfile | None = None

RESPONSE_SIZE_LIMIT = 500_000  # bytes; tools return narrow-your-query guidance


def _build_instructions(profile: ClinicalProfile) -> str:
    return f"""\
SNORE MCP Server v{version("snore")}
Sleep eNvironment Observation & Respiratory Evaluation

REQUIRED READING: Read `docs://tools` before using any tool. Failure to read
the tool documentation may result in incorrect or incomplete results.

Active clinical profile: {profile.display_name}
{profile.priority_hint}

Clinical context:
{profile.clinical_context}

WORKFLOW:
1. get_data_overview  → discover devices, date ranges, channels
2. get_nightly_summary → identify nights of interest (30 nights/page)
3. get_settings_timeline → understand settings epochs
4. get_events (date) → event-level detail for a night
5. (Phase 2) get_breath_table, find_windows, compare_epochs for morphology tuning
6. (Phase 3) render_window, get_waveform for visual inspection

DATA TIERS (progressive disclosure):
  Tier 1 (primary):  computed metrics — indices, percentiles, aggregates
  Tier 2 (secondary): PNG charts — render_window (Phase 3)
  Tier 3 (escape hatch): raw arrays — get_waveform ≤2 min / ≤1000 pts (Phase 3)

NULL FIELDS: When data is absent, fields are null + a companion *_reason field
explains why (e.g. rera_index_reason: "analysis_not_run"). Never infer from null.

See docs://capabilities for dataset-specific channel availability.
"""


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(
    app: Any, db_flag: str | None = None, profile_name: str = "neutral"
) -> AsyncGenerator[None]:
    """FastMCP lifespan: initialize DB and set active profile."""
    global _active_profile

    target = DatabaseTarget.from_env_and_flags(db_flag=db_flag, warn_ignored=True)
    async_url = target.resolve_async_url()

    await init_database_from_url(async_url)
    _active_profile = get_profile(profile_name)

    logger.info(
        "SNORE MCP server started — db=%r profile=%s", target.location, profile_name
    )

    try:
        yield
    finally:
        await cleanup_database()
        _active_profile = None
        logger.info("SNORE MCP server stopped")


def make_server(db_flag: str | None = None, profile_name: str = "neutral") -> FastMCP:
    """Construct and return a configured FastMCP instance."""
    profile = get_profile(profile_name)

    @asynccontextmanager
    async def _bound_lifespan(app: Any) -> AsyncGenerator[None]:
        async with _lifespan(app, db_flag=db_flag, profile_name=profile_name):
            yield

    mcp = FastMCP(
        name="snore",
        instructions=_build_instructions(profile),
        lifespan=_bound_lifespan,
    )

    _register_resources(mcp)
    _register_tools(mcp)

    return mcp


# ---------------------------------------------------------------------------
# Error boundary
# ---------------------------------------------------------------------------


def tool_error_boundary(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Convert common tool failures into ToolError so FastMCP sets isError=true."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ToolError:
            raise
        except (ValidationError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            response = getattr(exc, "response", None)
            message = response.text if response is not None else str(exc)
            raise ToolError(message) from exc

    return wrapper


def _check_response_size(result: Any, tool_name: str) -> None:
    """Raise ToolError if the serialized result exceeds RESPONSE_SIZE_LIMIT."""
    try:
        import sys

        size = sys.getsizeof(json.dumps(result, default=str))
    except Exception:
        return
    if size > RESPONSE_SIZE_LIMIT:
        raise ToolError(
            f"Response from {tool_name} exceeds the {RESPONSE_SIZE_LIMIT:,}-byte limit. "
            "Narrow your query: use a shorter date range, smaller page_size, or add "
            "device/type filters."
        )


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _register_resources(mcp: FastMCP) -> None:

    @mcp.resource("docs://tools")
    def get_tool_documentation() -> str:
        """Complete tool reference documentation."""
        docs_path = Path(__file__).resolve().parent / "docs" / "tools.md"
        return docs_path.read_text()

    @mcp.resource("docs://schemas/{schema_type}")
    def get_schema(schema_type: str) -> str:
        """JSON schema for a named response type.

        Available schema_types: device_capabilities, device_info, data_overview,
        settings_epoch, settings_timeline, nightly_row, compliance_fields,
        nightly_summary, event_context, event_row, events_response, capability_entry.
        """
        model = SCHEMA_MODEL_MAP.get(schema_type)
        if model is None:
            available = sorted(SCHEMA_MODEL_MAP.keys())
            raise ToolError(
                f"Unknown schema type {schema_type!r}. Available: {available}"
            )
        return json.dumps(model_to_schema(model), indent=2)

    @mcp.resource("docs://capabilities")
    async def get_capabilities() -> str:
        """Dataset capabilities — dynamically generated from imported data.

        Lists which waveform channels, event types, and analysis features are
        present in the imported dataset.  Use this to understand what is and is
        not available before calling tools.
        """
        from snore.mcp.tools.overview import get_data_overview

        async with session_scope() as db:
            overview = await get_data_overview(db)

        caps = {
            "description": (
                "Available data channels and features in the imported SNORE dataset. "
                "Channels listed as present=false are not available — tool fields "
                "for absent channels return null with a reason."
            ),
            "devices": [
                {
                    "id": d.id,
                    "manufacturer": d.manufacturer,
                    "model": d.model,
                    "date_range": {
                        "start": d.first_session_date.isoformat()
                        if d.first_session_date
                        else None,
                        "end": d.last_session_date.isoformat()
                        if d.last_session_date
                        else None,
                    },
                    "session_count": d.session_count,
                }
                for d in overview.devices
            ],
            "waveform_channels": [
                {"channel": ch, "present": True}
                for ch in overview.available_waveform_channels
            ],
            "event_types": overview.available_event_types,
            "analysis": {
                "run": overview.analysis_run,
                "session_count": overview.analysis_session_count,
                "note": (
                    "Run 'snore analysis run' or re-import with analysis enabled "
                    "to populate analysis-derived fields (RERA index, RDI, breath table)."
                    if not overview.analysis_run
                    else "Analysis results are available. RERA index and RDI fields are populated."
                ),
            },
        }

        return json.dumps(caps, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _register_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    @tool_error_boundary
    async def get_data_overview() -> dict[str, Any]:
        """Orient to the imported dataset: devices, date ranges, channels, analysis status.

        Call this first before any other tool. Returns everything needed to understand
        what data is available and which tools are applicable.

        Returns:
            DataOverviewResponse with devices, date ranges, waveform channels,
            event types, and analysis status.
        """
        from snore.mcp.tools.overview import get_data_overview as _impl

        async with session_scope() as db:
            result = await _impl(db)

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_data_overview")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def get_settings_timeline(
        start: str,
        end: str,
        device_id: int | None = None,
    ) -> dict[str, Any]:
        """Return therapy settings epochs for a date range.

        Each epoch represents a contiguous period of identical settings.
        Changed keys are flagged on the epoch where the change first appears.
        Uses generic RX_KEYS only (mode, epr_level, epr_mode, pressure_min,
        pressure_max, pressure_fixed, ipap, epap, ps).

        Args:
            start: Start date in YYYY-MM-DD format.
            end: End date in YYYY-MM-DD format.
            device_id: Optional device ID filter. Use get_data_overview to list devices.

        Returns:
            SettingsTimelineResponse with epochs list and total_epochs count.
        """
        from snore.mcp.tools.settings import get_settings_timeline as _impl

        start_d, end_d = parse_date_range(start, end)

        async with session_scope() as db:
            result = await _impl(db, start_d, end_d, device_id=device_id)

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_settings_timeline")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def get_nightly_summary(
        start: str,
        end: str,
        device_id: int | None = None,
        page: int = 1,
        page_size: int = 30,
        compliance_threshold_hours: float = 4.0,
    ) -> dict[str, Any]:
        """Return per-night therapy summary for a date range.

        Paginated at 30 nights/call (adjustable). Analysis-derived fields (RERA
        index, RDI) are null + reason "analysis_not_run" when analysis has not
        been run. Compliance fields are included in the response.

        Args:
            start: Start date in YYYY-MM-DD format.
            end: End date in YYYY-MM-DD format.
            device_id: Optional device ID filter.
            page: Page number (1-based). Default 1.
            page_size: Nights per page (max 90). Default 30.
            compliance_threshold_hours: Hours to count as compliant (default 4.0).

        Returns:
            NightlySummaryResponse with nights list, pagination, and compliance block.
        """
        from snore.mcp.tools.summary import get_nightly_summary as _impl

        start_d, end_d = parse_date_range(start, end)

        capped_page_size = min(page_size, 90)
        if page < 1:
            raise ValidationError("page must be >= 1")

        async with session_scope() as db:
            result = await _impl(
                db,
                start_d,
                end_d,
                device_id=device_id,
                page=page,
                page_size=capped_page_size,
                compliance_threshold_hours=compliance_threshold_hours,
            )

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_nightly_summary")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def get_events(
        date: str,
        types: list[str] | None = None,
        min_duration: float | None = None,
        include_context: bool = True,
    ) -> dict[str, Any]:
        """Return respiratory events for a single session date.

        Includes per-event context: minutes since session start.
        Pressure/leak at event and MV-prior-120s context require waveform
        lookups and will be added in Phase 3 (render_window).

        Args:
            date: Session date in YYYY-MM-DD format.
            types: Optional event type filter, e.g. ["CA", "OA", "H", "RERA"].
                   See docs://tools for common event_type values.
            min_duration: Minimum event duration in seconds (optional).
            include_context: Attach per-event context block (default true).

        Returns:
            EventsResponse with events list and total_events count.
        """
        from snore.mcp.tools.events import get_events as _impl

        event_date = parse_date(date, "date")

        async with session_scope() as db:
            result = await _impl(
                db,
                event_date,
                types=types,
                min_duration=min_duration,
                include_context=include_context,
            )

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_events")
        return payload
