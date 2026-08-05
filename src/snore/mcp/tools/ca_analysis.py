"""get_ca_analysis tool — BreathService CA-analysis adapter.

Architecture (plan §9 in-scope/out-of-scope split):
- DB fetch runs INSIDE the server's scope_provider context: ``fetch_ca_raw``
  holds the AsyncSession only during the query and device-capabilities fetch;
  the scope closes before CPU work begins.
- ``ca_response_from_raw`` is PURE — it calls ``compute_ca_analysis``
  (deserialize/compute/aggregate) outside the DB scope.  No session access
  occurs after ``fetch_ca_raw`` returns.

Timestamp contract (A6):
- Session wall-clock anchors (session_start_wall_clock) use tier-2
  (offset-free ISO 8601 + timezone_status="unknown") for absolute times.
- In-session positions are numeric offset_seconds (tier-3).

CA events are event-anchored (import-time Event rows with event_type='CA')
and are returned regardless of day_status (not_run, partial, mixed_version
are all SUCCESS responses with events present). Night-level fields
(periodic_breathing_pct, mv_rolling_variance) are null + *_reason companion
when coverage is insufficient. Refusals are SUCCESS responses, never errors.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.schemas import CaAnalysisResponse, CaDetailSchema
from snore.mcp.tools._capabilities import build_device_capabilities
from snore.mcp.tools._coverage import map_session_coverage
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
    from snore.services.breath_service import BreathService  # noqa: PLC0415

    bs = BreathService(db_session, profile_id)
    try:
        raw = await bs.fetch_ca_analysis(therapy_date=therapy_date, device_id=device_id)
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)

    # Service uses device_id=0 as a sentinel for "no device resolved" (never emit it).
    resolved_device_id = raw.device_id if raw.device_id > 0 else None
    caps = None
    if resolved_device_id is not None:
        # Pass analysis_run=True when the day has analysis coverage (avoids an
        # extra DB round-trip when day_status already confirms analysis ran).
        analysis_run = str(raw.day_status) != "not_run"
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

    Session sentinel scrub: ``device_id=0`` means no device was resolved; it
    is never emitted in the response (mapped to null).
    """
    from snore.services.breath_service import compute_ca_analysis  # noqa: PLC0415

    result = compute_ca_analysis(raw)

    coverage = map_session_coverage(result.session_coverage)

    ca_events = [
        CaDetailSchema(
            session_id=ev.session_id,
            session_start_wall_clock=ev.session_start_wall_clock.isoformat(),
            timezone_status=str(ev.timezone_status),
            offset_seconds=ev.offset_seconds,
            duration_seconds=ev.duration_seconds,
            preceding_mv_slope_lpm_per_min=ev.preceding_mv_slope,
            preceding_mv_slope_reason=str(ev.preceding_mv_reason)
            if ev.preceding_mv_reason is not None
            else None,
            ps_delivered_cmh2o=ev.ps_delivered_cmh2o,
            ps_reason=str(ev.ps_reason) if ev.ps_reason is not None else None,
            stability_index=ev.stability_index,
            stability_reason=str(ev.stability_reason)
            if ev.stability_reason is not None
            else None,
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
        null_reason=str(result.null_reason) if result.null_reason is not None else None,
        ca_events=ca_events,
        periodic_breathing_pct=result.periodic_breathing_pct,
        pb_reason=str(result.pb_reason) if result.pb_reason is not None else None,
        mv_rolling_variance=result.mv_rolling_variance,
        mv_variance_reason=str(result.mv_variance_reason)
        if result.mv_variance_reason is not None
        else None,
        device_capabilities=caps,
    )
