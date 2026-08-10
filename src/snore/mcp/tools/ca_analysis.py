"""get_ca_analysis tool — BreathService CA-analysis adapter.

Architecture (DB-fetch / pure-compute split):
- DB fetch runs INSIDE the server's scope_provider context: ``fetch_ca_raw``
  holds the AsyncSession only during the query and device-capabilities fetch;
  the scope closes before CPU work begins.
- ``ca_response_from_raw`` is PURE — it calls ``compute_ca_analysis``
  (deserialize/compute/aggregate) outside the DB scope.  No session access
  occurs after ``fetch_ca_raw`` returns.

Timestamp contract (A6):
- Session wall-clock anchors (session_start_wall_clock) use tier-2
  (offset-free ISO 8601 + timezone_status "unknown" | "user_declared", with
  timezone_name carrying the profile's declared IANA zone) for absolute times.
- In-session positions are numeric offset_seconds (tier-3).

CA events are event-anchored (import-time Event rows with event_type='CA')
and are returned regardless of day_status (not_run, partial, mixed_version
are all SUCCESS responses with events present). Night-level fields
(periodic_breathing_pct, mv_rolling_variance) are null + *_reason companion
when coverage is insufficient. Refusals are SUCCESS responses, never errors.
"""

from __future__ import annotations

import asyncio

from datetime import date
from typing import TYPE_CHECKING, Any

from fastmcp import Context

if TYPE_CHECKING:
    from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.schemas import (
    CaAnalysisResponse,
    CaDetailSchema,
    localize_wall_clock,
    tz_fields,
)
from snore.mcp.tools._capabilities import build_device_capabilities
from snore.mcp.tools._coverage import map_session_coverage
from snore.mcp.tools._helpers import str_or_none
from snore.mcp.tools._scaffold import (
    _check_response_size,
    _runtime,
    tool_error_boundary,
)
from snore.mcp.tools._service_errors import (
    MAPPED_SERVICE_ERRORS,
    raise_mapped_service_error,
)

if TYPE_CHECKING:
    from snore.mcp.schemas import DeviceCapabilities
    from snore.services.breath_service import RawCaAnalysis


async def fetch_ca_raw(
    db_session: AsyncSession,
    therapy_date: date,
    profile_id: int,
    device_id: int | None = None,
) -> tuple[RawCaAnalysis, DeviceCapabilities | None]:
    """Fetch CA data and device capabilities within the caller's DB scope.

    Delegates to ``BreathService.fetch_ca_analysis`` for the per-session
    waveform blobs, CA events, and PB% JSON, then builds ``DeviceCapabilities``
    within the same scope.

    ``DeviceAmbiguityError``, ``DeviceNotOwnedError``, and
    ``MultiSessionAmbiguityError`` are mapped to ``ValidationError`` via
    ``raise_mapped_service_error``.

    Args:
        db_session: Async database session (used only inside this call).
        therapy_date: Therapy date to query.
        profile_id: Profile scope for all ownership checks.
        device_id: Filter to a specific device; required when multiple devices
            share the date.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        DayAnalysisStatus,
    )

    bs = BreathService(db_session, profile_id)
    try:
        raw = await bs.fetch_ca_analysis(therapy_date=therapy_date, device_id=device_id)
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)

    # Service uses device_id=0 as a sentinel for "no device resolved" (never emit it).
    resolved_device_id = raw.device_id if raw.device_id > 0 else None
    caps = None
    if resolved_device_id is not None:
        # Derive analysis_run from day_status — avoids the extra DB round-trip
        # inside build_device_capabilities either way.
        analysis_run = raw.day_status != DayAnalysisStatus.NOT_RUN
        caps = await build_device_capabilities(
            db_session,
            profile_id,
            resolved_device_id,
            date_start=therapy_date,
            date_end=therapy_date,
            analysis_run=analysis_run,
        )
    return raw, caps


def ca_response_from_raw(
    raw: RawCaAnalysis,
    caps: DeviceCapabilities | None,
) -> CaAnalysisResponse:
    """Build a ``CaAnalysisResponse`` from raw fetch results.

    Pure — no DB access.  Calls ``compute_ca_analysis`` (numpy/statistics
    aggregation) then maps the ``CaAnalysisResult`` DTO to the MCP response
    schema.
    """
    from snore.services.breath_service import compute_ca_analysis  # noqa: PLC0415

    result = compute_ca_analysis(raw)

    coverage = map_session_coverage(result.session_coverage)

    ca_events = [
        CaDetailSchema(
            session_id=ev.session_id,
            session_start_wall_clock=localize_wall_clock(
                ev.session_start_wall_clock, ev.timezone_status, ev.timezone_name
            ),
            **tz_fields(ev),
            offset_seconds=ev.offset_seconds,
            duration_seconds=ev.duration_seconds,
            preceding_mv_slope_lpm_per_min=ev.preceding_mv_slope,
            preceding_mv_slope_reason=str_or_none(ev.preceding_mv_reason),
            ps_delivered_cmh2o=ev.ps_delivered_cmh2o,
            ps_reason=str_or_none(ev.ps_reason),
            stability_index=ev.stability_index,
            stability_reason=str_or_none(ev.stability_reason),
            mv_source=str_or_none(ev.mv_source),
        )
        for ev in result.ca_events
    ]

    return CaAnalysisResponse(
        query_date=result.query_date.isoformat(),
        device_id=result.device_id,
        day_status=str(result.day_status),
        session_coverage=coverage,
        algorithm_identity=result.algorithm_identity.model_dump(mode="json")
        if result.algorithm_identity is not None
        else None,
        null_reason=str_or_none(result.null_reason),
        ca_events=ca_events,
        periodic_breathing_pct=result.periodic_breathing_pct,
        pb_reason=str_or_none(result.pb_reason),
        mv_rolling_variance=result.mv_rolling_variance,
        mv_variance_reason=str_or_none(result.mv_variance_reason),
        mv_source=str_or_none(result.mv_source),
        mv_fallback_version=result.mv_fallback_version,
        device_capabilities=caps,
    )


def register(mcp: FastMCP) -> None:
    from snore.mcp.validation import parse_date  # noqa: PLC0415

    @mcp.tool()
    @tool_error_boundary
    async def get_ca_analysis(
        ctx: Context,
        date: str,
        device_id: int | None = None,
    ) -> dict[str, Any]:
        """Central-apnea (clear-airway) context for a single therapy date.

        Returns per-CA-event metrics and night-level periodic-breathing statistics.
        CA events are sourced from device-reported event records (import-time Event
        rows) and are returned regardless of day analysis status — ``day_status``
        values of ``not_run``, ``partial``, and ``mixed_version`` all yield a
        successful response with events present.  Only the derived night-level fields
        (``periodic_breathing_pct``, ``mv_rolling_variance``) are null+reason when
        coverage is insufficient.

        Per-CA-event metrics (each nullable with a companion ``*_reason`` field):
            ``preceding_mv_slope_lpm_per_min`` — minute-ventilation slope (L/min per
                minute) computed over the 120 s preceding the event.
            ``ps_delivered_cmh2o`` — pressure support delivered during the event
                (cmH2O).
            ``stability_index`` — coefficient of variation of MV in the 60 s before
                the event.

        Night-level fields:
            ``periodic_breathing_pct`` — fraction of the night exhibiting periodic
                breathing, null+``pb_reason`` when not computable.
            ``mv_rolling_variance`` — rolling variance of minute ventilation across
                the night, null+``mv_variance_reason`` when not computable.

        ``algorithm_identity`` is null with ``null_reason: "algo_version_mismatch"``
        on mixed-version days (sessions analysed with incompatible algorithm versions).

        Args:
            date: Session date in YYYY-MM-DD format.
            device_id: Filter to a specific device.  Required when multiple owned
                       devices have data for the date; ambiguity error lists owned
                       device IDs.

        Returns:
            CaAnalysisResponse with ``query_date``, ``device_id``, ``day_status``,
            ``session_coverage``, ``algorithm_identity``, ``ca_events`` list,
            night-level fields, and ``device_capabilities`` (including
            ``has_flow_waveform``/``has_pressure_waveform``, so null waveform-derived
            metrics can be distinguished from never-recorded channels).

        Refusal semantics (successful responses):
            Night-level fields (``periodic_breathing_pct``, ``mv_rolling_variance``)
            are null+reason when ``day_status`` is ``not_run``, ``partial``, or
            ``mixed_version``.  CA events are still present in all these cases.
            ``algorithm_identity`` is null+``null_reason: "algo_version_mismatch"``
            on mixed-version days.
            When ``device_id`` is provided and the date has no sessions,
            the tool returns ``day_status: "not_run"``, ``ca_events: []``, and
            night-level nulls with reasons (SUCCESS) — not a tool error.

        Error conditions:
            - No ``device_id`` provided and no sessions found for date → tool error
              ("No sessions found in range <start> to <end>").
            - Multiple devices on date and no ``device_id`` → tool error listing
              owned device IDs.
        """
        from snore.mcp.tools.ca_analysis import (  # noqa: PLC0415
            ca_response_from_raw,
            fetch_ca_raw,
        )

        runtime = _runtime(ctx)
        therapy_date = parse_date(date, "date")
        async with runtime.scope_provider() as db:
            raw, caps = await fetch_ca_raw(
                db,
                therapy_date,
                profile_id=runtime.profile_id,
                device_id=device_id,
            )
        # CPU-bound: deserialize blobs, numpy statistics — off the event loop.
        result = await asyncio.to_thread(ca_response_from_raw, raw, caps)
        payload: dict[str, Any] = result.model_dump(mode="json")
        _check_response_size(payload, "get_ca_analysis")
        return payload
