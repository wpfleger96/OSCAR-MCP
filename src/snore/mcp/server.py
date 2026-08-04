"""SNORE MCP Server.

Third presentation layer over the async service layer — a peer of the CLI and
FastAPI, never a place where analysis logic lives.

Design doctrine:
  - Tiered data access: overview → summary → events → breath table → raw waveform
  - Compute server-side, return compact JSON; units on every field
  - Data-quality flags / null + reason everywhere (G2)
  - Stateless: no module-global state; DB access injected via SNORERuntime (G3)
  - Profile-parameterized: profiles shape instructions only, not data (G1)
  - Vendor dispatch stays in the parser/service layer (G4)

DB-access pattern (M2 / Thufir MINOR):
  ``_lifespan`` builds a ``SNORERuntime`` and yields it as the FastMCP lifespan
  context.  Every tool and resource receives a ``ctx: Context`` parameter;
  FastMCP injects it by type annotation and excludes it from the client-facing
  schema.  Tools call ``runtime = _runtime(ctx)`` to extract it and then
  ``runtime.scope_provider()`` instead of ``session_scope()`` directly.

  The swap site for PR-C (actor-scoped session factory) is the
  ``SNORERuntime(scope_provider=...)`` construction inside ``_lifespan`` — no
  tool code needs to change.  Do NOT call ``session_scope()`` directly from
  tools.
"""

from __future__ import annotations

import json
import logging

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.session import (
    cleanup_database,
    init_database_from_url,
    session_scope,
)
from snore.database.target import DatabaseTarget
from snore.mcp.errors import ValidationError
from snore.mcp.profiles import ClinicalProfile, get_profile
from snore.mcp.schemas import SCHEMA_MODEL_MAP, EpochSpec, model_to_schema
from snore.mcp.validation import (
    parse_date,
    parse_date_range,
    validate_compliance_threshold,
    validate_epoch_count,
    validate_max_events,
    validate_min_duration,
    validate_page_args,
    validate_window_count,
)

logger = logging.getLogger(__name__)

# DB-access seam (M2): tools call runtime.scope_provider(), never session_scope()
# directly.  SNORERuntime is built in _lifespan and injected via FastMCP's
# lifespan context; PR-C swaps in an actor-scoped factory at the construction
# site in _lifespan without touching any tool code.
# Type: a zero-arg callable returning an async context manager that yields AsyncSession.
_ScopeProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class SNORERuntime:
    """Lifespan-scoped runtime state injected into every tool and resource.

    FastMCP yields this from ``_lifespan`` and makes it available as
    ``ctx.lifespan_context``.  Frozen to prevent accidental mutation across
    concurrent requests sharing the same lifespan.
    """

    scope_provider: _ScopeProvider
    profile_id: int


def _runtime(ctx: Context) -> SNORERuntime:
    """Extract the SNORERuntime from the FastMCP context.

    fastmcp types ``ctx.lifespan_context`` as ``dict[str, Any]``; cast narrows
    it to the actual yielded type without changing runtime behaviour.
    """
    return cast("SNORERuntime", ctx.lifespan_context)


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
5. get_breath_table, find_windows, compare_epochs for breath morphology tuning
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
) -> AsyncGenerator[SNORERuntime]:
    """FastMCP lifespan: initialize DB, build SNORERuntime, resolve active profile.

    Yields a ``SNORERuntime`` as the lifespan context so every tool and resource
    can access the scope-provider seam and profile_id without module globals.
    Teardown calls ``cleanup_database()`` in a ``finally`` block so it runs even
    if tool errors occur during shutdown.

    PR-C's actor-scoped session factory swap happens at the ``SNORERuntime(...)``
    construction below — nothing else in this file changes for that swap.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415
    from snore.parsers.register_all import ensure_registered_parsers  # noqa: PLC0415

    target = DatabaseTarget.from_env_and_flags(db_flag=db_flag, warn_ignored=True)
    async_url = target.resolve_async_url()

    await init_database_from_url(async_url)

    try:
        # Register vendor parsers once at startup (idempotent; tools must not call
        # ensure_registered_parsers() themselves — this is the single call site).
        ensure_registered_parsers()

        # Resolve the active profile — required by BreathService, DeviceService,
        # and RxTracker (all scoped to a profile_id since multiuser Phase 1).
        async with session_scope() as _db:
            _profile_row = (
                (
                    await _db.execute(
                        select(models.Profile)
                        .where(models.Profile.deleting_at.is_(None))
                        .order_by(models.Profile.id)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if _profile_row is None:
                raise RuntimeError(
                    "No profile found in database — run 'snore db init' first "
                    "or import CPAP data to create a profile."
                )
            profile_id = int(_profile_row.id)

        # PR-C swap site: replace session_scope with an actor-scoped factory here.
        runtime = SNORERuntime(scope_provider=session_scope, profile_id=profile_id)

        logger.info(
            "SNORE MCP server started — db=%r profile=%s profile_id=%d",
            target.location,
            profile_name,
            runtime.profile_id,
        )
    except Exception:
        await cleanup_database()
        raise

    try:
        yield runtime
    finally:
        await cleanup_database()
        logger.info("SNORE MCP server stopped")


def make_server(db_flag: str | None = None, profile_name: str = "neutral") -> FastMCP:
    """Construct and return a configured FastMCP instance."""
    profile = get_profile(profile_name)

    @asynccontextmanager
    async def _bound_lifespan(app: Any) -> AsyncGenerator[SNORERuntime]:
        async with _lifespan(app, db_flag=db_flag, profile_name=profile_name) as rt:
            yield rt

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
            status = getattr(response, "status_code", None)
            message = (
                f"HTTP {status} from upstream service"
                if status is not None
                else str(exc)
            )
            raise ToolError(message) from exc

    return wrapper


def _check_response_size(result: Any, tool_name: str) -> None:
    """Raise ToolError if the serialized result exceeds RESPONSE_SIZE_LIMIT."""
    try:
        size = len(json.dumps(result, default=str).encode("utf-8"))
    except Exception:
        logger.warning(
            "_check_response_size: measurement failed for %s; skipping size gate",
            tool_name,
        )
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
        nightly_summary, event_context, event_row, events_response, capability_entry,
        breath_table_query, breath_table_row, breath_table_bin, breath_table_response,
        window_row, session_coverage_entry, find_windows_response, epoch_spec,
        epoch_distribution, epoch_stats, epoch_rx_violation, compare_epochs_response.
        """
        model = SCHEMA_MODEL_MAP.get(schema_type)
        if model is None:
            available = sorted(SCHEMA_MODEL_MAP.keys())
            raise ToolError(
                f"Unknown schema type {schema_type!r}. Available: {available}"
            )
        return json.dumps(model_to_schema(model), indent=2)

    @mcp.resource("docs://capabilities")
    async def get_capabilities(ctx: Context) -> str:
        """Dataset capabilities — dynamically generated from imported data.

        Lists which waveform channels, event types, and analysis features are
        present in the imported dataset.  Use this to understand what is and is
        not available before calling tools.

        Channels/settings are derived from DB rows (G2 — capability-honest).
        Parser registry is consulted for supported-vendor context only.
        """
        from snore.mcp.tools.overview import get_data_overview
        from snore.parsers.registry import parser_registry

        runtime = _runtime(ctx)

        async with runtime.scope_provider() as db:
            overview = await get_data_overview(db, profile_id=runtime.profile_id)

        supported_parsers = [
            f"{p.manufacturer} ({p.parser_id})" for p in parser_registry.list_parsers()
        ]

        caps = {
            "description": (
                "Available data channels and features in the imported SNORE dataset. "
                "Channels listed as present=false are not available — tool fields "
                "for absent channels return null with a reason. "
                "supported_parsers lists registered device parsers (supplementary context only)."
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
            "supported_parsers": supported_parsers,
        }

        return json.dumps(caps, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _register_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    @tool_error_boundary
    async def get_data_overview(ctx: Context) -> dict[str, Any]:
        """Orient to the imported dataset: devices, date ranges, channels, analysis status.

        Call this first before any other tool. Returns everything needed to understand
        what data is available and which tools are applicable.

        Returns:
            DataOverviewResponse with devices, date ranges, waveform channels,
            event types, and analysis status.
        """
        from snore.mcp.tools.overview import get_data_overview as _impl

        runtime = _runtime(ctx)

        async with runtime.scope_provider() as db:
            result = await _impl(db, profile_id=runtime.profile_id)

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_data_overview")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def get_settings_timeline(
        ctx: Context,
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

        runtime = _runtime(ctx)
        start_d, end_d = parse_date_range(start, end)

        async with runtime.scope_provider() as db:
            result = await _impl(
                db, start_d, end_d, profile_id=runtime.profile_id, device_id=device_id
            )

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_settings_timeline")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def get_nightly_summary(
        ctx: Context,
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

        runtime = _runtime(ctx)
        start_d, end_d = parse_date_range(start, end)

        n_calendar = (end_d - start_d).days + 1
        if n_calendar > 90:
            raise ValidationError(
                f"Date range spans {n_calendar} nights; maximum per call is 90. "
                "Use multiple calls to page over longer ranges."
            )

        capped_page_size = validate_page_args(page, page_size)
        validate_compliance_threshold(compliance_threshold_hours)

        async with runtime.scope_provider() as db:
            result = await _impl(
                db,
                start_d,
                end_d,
                profile_id=runtime.profile_id,
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
        ctx: Context,
        date: str,
        device_id: int | None = None,
        types: list[str] | None = None,
        min_duration: float | None = None,
        include_context: bool = True,
        max_events: int = 500,
    ) -> dict[str, Any]:
        """Return respiratory events for a single session date with inline waveform context.

        Each event includes pressure/leak at the event time and MV in the prior
        120 s (when waveform data is available), plus minutes since session start.

        Args:
            date: Session date in YYYY-MM-DD format.
            device_id: Optional device ID filter. Required when multiple devices
                       have data for the same date.
            types: Optional event type filter, e.g. ["CA", "OA", "H", "RERA"].
                   See docs://tools for common event_type values.
            min_duration: Minimum event duration in seconds (optional).
            include_context: Attach per-event waveform context block (default true).
            max_events: Maximum number of events to return after filtering (default 500,
                        minimum 1). When the result is truncated, ``total_events`` still
                        reports the full unfiltered count and ``truncated`` is set to true
                        in the response.

        Returns:
            EventsResponse with events list, total_events count, and truncated flag.
        """
        from snore.mcp.tools.events import get_events as _impl

        runtime = _runtime(ctx)
        event_date = parse_date(date, "date")

        validate_min_duration(min_duration)
        validate_max_events(max_events)

        async with runtime.scope_provider() as db:
            result = await _impl(
                db,
                event_date,
                profile_id=runtime.profile_id,
                device_id=device_id,
                types=types,
                min_duration=min_duration,
                include_context=include_context,
                max_events=max_events,
            )

        payload = result.model_dump(mode="json")
        _check_response_size(payload, "get_events")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def get_breath_table(
        ctx: Context,
        date: str,
        offset_start: float,
        offset_end: float,
        device_id: int | None = None,
        session_id: int | None = None,
        page: int = 1,
        page_size: int = 500,
        bin_minutes: float | None = None,
    ) -> dict[str, Any]:
        """Paginated breath-level table for a single therapy night.

        Use this tool to inspect individual breath waveform features (flow class,
        flattening index, timing, tidal volume) within a time window of a therapy session.
        Call ``get_data_overview`` first to confirm analysis has been run; this tool
        requires breath-level analysis results.

        Raw windows are capped at 15 minutes (offset_end - offset_start ≤ 900 s).
        For longer windows set ``bin_minutes`` to aggregate into time bins — the response
        then populates ``bins`` instead of ``rows``.  ``page_size`` is capped at 2000.

        Args:
            date: Session date in YYYY-MM-DD format.
            offset_start: Window start in seconds from session start (≥ 0).
            offset_end: Window end in seconds from session start (> offset_start).
                        Raw window must be ≤ 900 s unless ``bin_minutes`` is set.
            device_id: Filter to a specific device.  Required when multiple devices
                       have data for the same date.
            session_id: Filter to a specific session.  Required when the device had
                        multiple sessions on the date.  Pass ``device_id`` too when
                        both are given to validate consistency.
            page: Page number for raw rows (1-based, default 1).
            page_size: Rows per page for raw fetch (default 500, max 2000).
            bin_minutes: When set (≥ 1.0), aggregate breaths into bins of this width
                         instead of returning raw rows.  Required for windows > 15 min.

        Returns:
            BreathTableResponse.  ``is_binned`` indicates raw vs binned mode.
            ``analysis_status`` / ``null_reason`` describe coverage.
            ``device_capabilities`` describes what the device records.

        Refusal semantics:
            When ``analysis_status`` is ``"not_run"`` or ``"stale"``, ``null_reason``
            explains why (``"analysis_not_run"``, ``"analysis_stale"``).
            These are successful responses with ``total_breaths=0``, not tool errors.

        Error conditions:
            - No sessions found for date → tool error; use ``get_data_overview``.
            - Multiple devices on date and no ``device_id`` → tool error listing device IDs.
            - Multiple sessions on date and no ``session_id`` → tool error listing session IDs.
            - Raw window > 15 min without ``bin_minutes`` → tool error; set ``bin_minutes``.
            - Breath-level tables missing → tool error; run ``snore analysis run``.
        """
        from snore.mcp.tools.breath_table import (
            get_breath_table as _impl,  # noqa: PLC0415
        )

        runtime = _runtime(ctx)
        therapy_date = parse_date(date, "date")

        async with runtime.scope_provider() as db:
            result = await _impl(
                db,
                therapy_date,
                profile_id=runtime.profile_id,
                device_id=device_id,
                session_id=session_id,
                offset_start=offset_start,
                offset_end=offset_end,
                page=page,
                page_size=page_size,
                bin_minutes=bin_minutes,
            )

        payload: dict[str, Any] = result.model_dump(mode="json")
        _check_response_size(payload, "get_breath_table")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def find_windows(
        ctx: Context,
        date: str,
        criterion: str,
        n: int = 5,
        device_id: int | None = None,
        include_unknown_leak: bool = False,
        flattening_threshold: float | None = None,
        min_window_breaths: int = 3,
        context_breaths_before: int = 3,
        context_breaths_after: int = 3,
        context_seconds: float = 120.0,
        min_fl_run_length: int = 2,
        fl_class_threshold: int = 4,
    ) -> dict[str, Any]:
        """Find the N worst breath windows matching a flow-limitation criterion for a night.

        Use this tool to locate specific regions in a therapy session worth reviewing in
        detail (e.g. in ``get_breath_table`` or Phase-3 ``render_window``).  Each window
        is a contiguous breath sequence ranked by severity; windows with >50% overlap
        (relative to the shorter) are deduped, keeping the worst.  Results are ordered
        worst-first.

        Requires breath-level analysis results (``get_data_overview`` → ``analysis_run``
        must be true).

        Args:
            date: Session date in YYYY-MM-DD format.
            criterion: Window selection criterion.  One of:
                ``"worst_flattening_leak_valid"`` — worst mean mid-inspiratory flattening
                    among leak-valid breaths; use to find FL hotspots.
                ``"ca_centered"`` — context window around each CA event; works even when
                    the day mixes algorithm versions.
                ``"fl_run_ending_in_recovery"`` — FL runs immediately followed by a
                    recovery breath; requires uniform primary_mode across sessions.
            n: Number of windows to return (1–50, default 5).
            device_id: Filter to a specific device.  Required when multiple devices
                       have data for the same date.
            include_unknown_leak: Include breaths where leak validity is unknown
                (default false).  Only relevant for ``worst_flattening_leak_valid``.
            flattening_threshold: Minimum mid-inspiratory flattening score for a breath
                to anchor a window.  Service default when omitted.
            min_window_breaths: Minimum breaths per window (default 3).
            context_breaths_before: Context breaths before the anchor (default 3).
            context_breaths_after: Context breaths after the anchor (default 3).
            context_seconds: Context window duration in seconds (default 120.0).
                Only relevant for ``ca_centered``.
            min_fl_run_length: Minimum FL-class run length (default 2).
                Only relevant for ``fl_run_ending_in_recovery``.
            fl_class_threshold: Minimum flow class to count as FL (default 4).
                Only relevant for ``fl_run_ending_in_recovery``.

        Returns:
            FindWindowsResponse.  ``windows`` is ordered worst-first.
            ``session_coverage`` lists per-session analysis status.
            ``device_capabilities`` describes what the device records.

        Refusal semantics (successful responses with empty ``windows`` list):
            ``null_reason: "algo_version_mismatch"`` — the day has sessions analysed
                with different algorithm versions; FL-ranked criteria
                (``worst_flattening_leak_valid``, ``fl_run_ending_in_recovery``)
                refuse comparison.  ``ca_centered`` is unaffected — it still works.
            ``null_reason: "primary_mode_mismatch"`` — sessions differ in primary mode;
                only ``fl_run_ending_in_recovery`` refuses; other criteria are unaffected.
            ``null_reason: "analysis_not_run"`` — no analysis results for this date.

        Error conditions:
            - Unknown ``criterion`` value → tool error listing valid criteria.
            - ``n`` outside 1–50 → tool error.
            - Multiple devices on date and no ``device_id`` → tool error listing device IDs.
            - Options irrelevant to the chosen criterion passed with non-default values
              → tool error; omit those options or use their defaults.
        """
        from snore.mcp.tools.windows import find_windows as _impl  # noqa: PLC0415

        runtime = _runtime(ctx)
        therapy_date = parse_date(date, "date")
        validate_window_count(n)

        async with runtime.scope_provider() as db:
            result = await _impl(
                db,
                therapy_date,
                profile_id=runtime.profile_id,
                criterion=criterion,
                n=n,
                device_id=device_id,
                include_unknown_leak=include_unknown_leak,
                flattening_threshold=flattening_threshold,
                min_window_breaths=min_window_breaths,
                context_breaths_before=context_breaths_before,
                context_breaths_after=context_breaths_after,
                context_seconds=context_seconds,
                min_fl_run_length=min_fl_run_length,
                fl_class_threshold=fl_class_threshold,
            )

        payload: dict[str, Any] = result.model_dump(mode="json")
        _check_response_size(payload, "find_windows")
        return payload

    @mcp.tool()
    @tool_error_boundary
    async def compare_epochs(
        ctx: Context,
        epochs: list[EpochSpec],
        metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compare breath-feature distributions across up to 6 therapy settings epochs.

        Use this tool to detect whether a settings change improved or worsened
        flow-limitation metrics.  Each epoch is a labelled date range; the tool returns
        descriptive statistics (median, IQR, P95) of leak-valid breaths for each epoch.
        Only nights with OK analysis results contribute to an epoch's distributions.

        Call ``get_settings_timeline`` first to identify meaningful epoch boundaries,
        then ``compare_epochs`` to quantify the difference.

        Requires breath-level analysis results (``get_data_overview`` → ``analysis_run``
        must be true).

        Args:
            epochs: List of 1–6 epoch specs.  Each spec has:
                ``label`` — human-readable epoch name (appears in response).
                ``date_start`` — epoch start in YYYY-MM-DD format (inclusive).
                ``date_end`` — epoch end in YYYY-MM-DD format (inclusive).
                ``device_id`` — optional; all epochs must target the same device.
            metrics: Optional subset of distribution metrics to compute.  Defaults
                to all four: ``"mid_insp_flattening"``, ``"flatness_index"``,
                ``"tidal_volume_ml"``, ``"ie_ratio"``.

        Returns:
            CompareEpochsResponse.  Each entry in ``epochs`` contains distributions
            and coverage metadata (``nights_with_data``, ``nights_missing_analysis``).
            ``rx_violations`` lists any therapy-settings changes detected within an
            epoch's date range; callers should split affected epochs at those dates.

        Refusal semantics (ALL epoch distributions set to null):
            ``null_reason: "algo_version_mismatch"`` — epochs span sessions analysed
                with incompatible algorithm versions (cross-version refusal keys differ);
                re-run analysis with a uniform version before comparing.
            ``null_reason: "rx_changed_within_epoch"`` — therapy settings changed within
                at least one epoch; ``rx_violations`` lists the epoch label, changed keys,
                and change dates so the caller can split the range.
            Partial degradation: ``null_reason: "primary_mode_mismatch"`` on individual
                epochs nulls only RERA-related fields (``rera_proxy_count``,
                ``rera_reason``); the FL distributions remain populated.

        Error conditions:
            - Epochs list empty or >6 entries → tool error.
            - Multiple device IDs across epoch specs → tool error.
            - Device not owned → tool error.
            - Multiple devices on the date range and no ``device_id`` → tool error
              listing device IDs so the caller can re-issue with ``device_id``.
        """
        from snore.mcp.tools.epochs import compare_epochs as _impl  # noqa: PLC0415

        runtime = _runtime(ctx)
        validate_epoch_count(len(epochs))

        async with runtime.scope_provider() as db:
            result = await _impl(
                db,
                profile_id=runtime.profile_id,
                epochs=epochs,
                metrics=metrics,
            )

        payload: dict[str, Any] = result.model_dump(mode="json")
        _check_response_size(payload, "compare_epochs")
        return payload
