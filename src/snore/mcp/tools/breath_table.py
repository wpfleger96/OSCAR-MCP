"""get_breath_table tool — BreathService.get_breath_table() adapter.

Timestamp contract (A6):
- Session wall-clock anchor (session_start_wall_clock) uses tier-2
  (offset-free ISO 8601 + timezone_status="unknown") for absolute times.
- In-session positions are numeric offsets in seconds (tier-3).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.errors import ValidationError
from snore.mcp.schemas import (
    BreathTableBin,
    BreathTableQuery,
    BreathTableResponse,
    BreathTableRow,
)
from snore.mcp.tools._capabilities import build_device_capabilities
from snore.mcp.tools._service_errors import (
    MAPPED_SERVICE_ERRORS,
    raise_mapped_service_error,
)


async def get_breath_table(
    db_session: AsyncSession,
    therapy_date: date,
    profile_id: int,
    device_id: int | None = None,
    session_id: int | None = None,
    offset_start: float = 0.0,
    offset_end: float = 900.0,
    page: int = 1,
    page_size: int = 500,
    bin_minutes: float | None = None,
) -> BreathTableResponse:
    """Return a paginated breath-level table for a single therapy night.

    Builds a BreathQueryRange (Pydantic validates offset bounds, page_size cap,
    and the 15-min raw window limit), calls BreathService.get_breath_table, and
    maps the typed DTO to BreathTableResponse. No domain computation is done here.

    Args:
        db_session: Async database session.
        therapy_date: Therapy date to query.
        profile_id: Profile scope for ownership checks (required).
        device_id: Filter to a specific device; required when multiple devices
            share the date.
        session_id: Filter to a specific session; required when the device had
            multiple sessions on the date.
        offset_start: Window start in seconds from session start (>= 0).
        offset_end: Window end in seconds from session start (> offset_start).
            Raw window must be <= 900 s unless bin_minutes is set.
        page: Page number for raw rows (1-based, default 1).
        page_size: Rows per page for raw fetch (default 500, max 2000).
        bin_minutes: When set (>= 1.0), aggregate into time bins instead of
            returning raw rows. Required for windows > 15 min.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathQueryRange,
        BreathService,
    )

    # Pydantic validation errors (offset ordering, page_size cap, 15-min raw cap)
    # propagate to the server error boundary, which maps them to ToolError.
    query = BreathQueryRange(
        therapy_date=therapy_date,
        device_id=device_id,
        session_id=session_id,
        offset_start=offset_start,
        offset_end=offset_end,
        page=page,
        page_size=page_size,
        bin_minutes=bin_minutes,
    )

    bs = BreathService(db_session, profile_id)
    try:
        dto = await bs.get_breath_table(query)
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)
    except ValueError as exc:
        msg = str(exc)
        if "No sessions found" in msg:
            raise ValidationError(
                f"{msg} Use get_data_overview to check which dates have imported data."
            ) from exc
        raise ValidationError(msg) from exc

    # Echo the resolved query — therapy_date is a date object, convert to string.
    mapped_query = BreathTableQuery(
        therapy_date=dto.query.therapy_date.isoformat(),
        device_id=dto.query.device_id,
        session_id=dto.query.session_id,
        offset_start=dto.query.offset_start,
        offset_end=dto.query.offset_end,
        page=dto.query.page,
        page_size=dto.query.page_size,
        bin_minutes=dto.query.bin_minutes,
    )

    # Top-level session anchor: first row if rows present, else first bin, else None.
    top_session_id: int | None = (
        dto.rows[0].session_id if dto.rows else dto.query.session_id
    )
    top_session_start: str | None
    if dto.rows:
        top_session_start = dto.rows[0].session_start_wall_clock.isoformat()
    elif dto.bins:
        top_session_start = dto.bins[0].session_start_wall_clock.isoformat()
    else:
        top_session_start = None

    # Map raw rows — field renames per contract §4a:
    #   ti → ti_s, te → te_s, ttot → ttot_s
    #   peak_insp_flow → peak_insp_flow_lpm, peak_exp_flow → peak_exp_flow_lpm
    #   tidal_volume → tidal_volume_ml
    # Enum values serialised to str; None stays None.
    rows = [
        BreathTableRow(
            analysis_result_id=r.analysis_result_id,
            session_id=r.session_id,
            breath_number=r.breath_number,
            session_start_wall_clock=r.session_start_wall_clock.isoformat(),
            timezone_status="unknown",
            start_offset_seconds=r.start_offset_seconds,
            end_offset_seconds=r.end_offset_seconds,
            ti_s=r.ti,
            te_s=r.te,
            ttot_s=r.ttot,
            ie_ratio=r.ie_ratio,
            duty_cycle=r.duty_cycle,
            peak_insp_flow_lpm=r.peak_insp_flow,
            peak_exp_flow_lpm=r.peak_exp_flow,
            tidal_volume_ml=r.tidal_volume,
            flatness_index=r.flatness_index,
            mid_insp_flattening=r.mid_insp_flattening,
            flow_class=r.flow_class,
            flow_class_confidence=r.flow_class_confidence,
            is_recovery_breath=r.is_recovery_breath,
            trigger_type=str(r.trigger_type) if r.trigger_type is not None else None,
            cycle_type=str(r.cycle_type) if r.cycle_type is not None else None,
            trigger_cycle_confidence=r.trigger_cycle_confidence,
            trigger_cycle_experimental=True,
            trigger_cycle_applicability=(
                str(r.trigger_cycle_applicability)
                if r.trigger_cycle_applicability is not None
                else None
            ),
            trigger_cycle_reason=(
                str(r.trigger_cycle_reason)
                if r.trigger_cycle_reason is not None
                else None
            ),
            leak_valid=r.leak_valid,
            leak_valid_reason=(
                str(r.leak_valid_reason) if r.leak_valid_reason is not None else None
            ),
            ramp_active=r.ramp_active,
            ramp_active_reason=(
                str(r.ramp_active_reason) if r.ramp_active_reason is not None else None
            ),
            mask_off=r.mask_off,
            mask_off_reason=(
                str(r.mask_off_reason) if r.mask_off_reason is not None else None
            ),
        )
        for r in dto.rows
    ]

    # Map aggregated bins — tidal_volume_median → tidal_volume_median_ml.
    bins = [
        BreathTableBin(
            session_start_wall_clock=b.session_start_wall_clock.isoformat(),
            timezone_status="unknown",
            bin_start_offset=b.bin_start_offset,
            bin_end_offset=b.bin_end_offset,
            breath_count=b.breath_count,
            flatness_index_median=b.flatness_index_median,
            mid_insp_flattening_median=b.mid_insp_flattening_median,
            flow_class_mode=b.flow_class_mode,
            tidal_volume_median_ml=b.tidal_volume_median,
            ie_ratio_median=b.ie_ratio_median,
            leak_valid_fraction=b.leak_valid_fraction,
            analysis_status=str(b.analysis_status),
        )
        for b in dto.bins
    ]

    # Resolve device_id for the capabilities block.
    # Priority: explicit arg → first row's session → explicit session_id arg → None.
    resolved_device_id: int | None = device_id
    if resolved_device_id is None:
        source_session_id = dto.rows[0].session_id if dto.rows else session_id
        if source_session_id is not None:
            from sqlalchemy import select  # noqa: PLC0415

            from snore.database import models  # noqa: PLC0415

            result = await db_session.execute(
                select(models.Session.device_id)
                .join(models.Device, models.Device.id == models.Session.device_id)
                .where(
                    models.Session.id == source_session_id,
                    models.Device.profile_id == profile_id,
                )
            )
            resolved_device_id = result.scalar_one_or_none()

    caps = (
        await build_device_capabilities(
            db_session,
            profile_id,
            resolved_device_id,
            date_start=therapy_date,
            date_end=therapy_date,
        )
        if resolved_device_id is not None
        else None
    )

    return BreathTableResponse(
        query=mapped_query,
        session_id=top_session_id,
        session_start_wall_clock=top_session_start,
        timezone_status="unknown",
        analysis_status=str(dto.analysis_status),
        algo_versions=(
            dto.algo_versions.model_dump(mode="json")
            if dto.algo_versions is not None
            else None
        ),
        null_reason=str(dto.null_reason) if dto.null_reason is not None else None,
        is_binned=dto.is_binned,
        total_breaths=dto.total_breaths,
        page=dto.page,
        page_size=dto.page_size,
        rows=rows,
        bins=bins,
        device_capabilities=caps,
    )
