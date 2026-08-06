"""get_nightly_summary tool — StatsService / DayService + BreathService adapter.

Timestamp contract (A6): date fields are Python ``date`` objects serialized as
``YYYY-MM-DD`` — no timezone issue since dates have no time component.

Compliance and breath-level FL/RERA/Ti/IE fields are populated via
BreathService.get_nightly_range_summary().  Compliance denominator is
``n_calendar_nights`` (the full calendar span, not just nights with data).
All Day and Session queries are profile-scoped through Device.profile_id to
prevent cross-profile data leaks.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models

if TYPE_CHECKING:
    from fastmcp import FastMCP

from snore.mcp.errors import ValidationError
from snore.mcp.schemas import (
    ComplianceFields,
    NightlyRow,
    NightlySummaryResponse,
)
from snore.mcp.tools._capabilities import _has_analysis, build_device_capabilities
from snore.mcp.tools._helpers import str_or_none
from snore.mcp.tools._scaffold import _scope_and_run, tool_error_boundary
from snore.mcp.tools._service_errors import (
    MAPPED_SERVICE_ERRORS,
    raise_mapped_service_error,
)

_DEFAULT_PAGE_SIZE = 30
_DEFAULT_COMPLIANCE_THRESHOLD_HOURS = 4.0
MAX_NIGHTLY_RANGE = 90


async def get_nightly_summary(
    db_session: AsyncSession,
    start: date,
    end: date,
    profile_id: int,
    device_id: int | None = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
    compliance_threshold_hours: float = _DEFAULT_COMPLIANCE_THRESHOLD_HOURS,
) -> NightlySummaryResponse:
    """Return per-night therapy summary for a date range.

    Analysis-derived fields (RERA index, RDI, Ti, I:E, FL) are populated from
    BreathService.get_nightly_range_summary(); absent entries are null with
    reason (A2).  Compliance uses n_calendar_nights as denominator.

    Raises ValidationError when BreathService reports device ownership problems
    (DeviceAmbiguityError, DeviceNotOwnedError).  The server boundary converts
    ValidationError to a ToolError before it reaches the client.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        NightlyAnalysisSummary,
        NightlyRangeSummary,
    )

    # Fetch BreathService range summary — always called when profile_id is set.
    # ValueError (end < start or > 90 nights) is pre-validated/rejected upstream.
    bs_range: NightlyRangeSummary | None = None
    try:
        bs_range = await BreathService(
            db_session, profile_id
        ).get_nightly_range_summary(
            start,
            end,
            device_id=device_id,
            compliance_threshold_hours=compliance_threshold_hours,
        )
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)

    # Index per-night analysis summaries by therapy_date for O(1) lookup.
    bs_by_date: dict[date, NightlyAnalysisSummary] = {}
    if bs_range is not None:
        for night in bs_range.nights:
            bs_by_date[night.therapy_date] = night

    # Count total matching days for pagination — scoped via Device.profile_id
    count_q = (
        select(func.count(models.Day.id))
        .join(models.Device, models.Day.device_id == models.Device.id)
        .where(
            models.Day.date >= start,
            models.Day.date <= end,
            models.Device.profile_id == profile_id,
        )
    )
    if device_id is not None:
        count_q = count_q.where(models.Day.device_id == device_id)
    total = (await db_session.execute(count_q)).scalar_one()

    # Fetch the page of Day rows — scoped via Device.profile_id
    day_q = (
        select(models.Day)
        .join(models.Device, models.Day.device_id == models.Device.id)
        .where(
            models.Day.date >= start,
            models.Day.date <= end,
            models.Device.profile_id == profile_id,
        )
        .order_by(models.Day.date.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    if device_id is not None:
        day_q = day_q.where(models.Day.device_id == device_id)
    day_rows = (await db_session.execute(day_q)).scalars().all()

    if not day_rows:
        early_compliance: ComplianceFields | None = None
        if start != end and bs_range is not None:
            early_compliance = ComplianceFields(
                threshold_hours=compliance_threshold_hours,
                days_compliant=bs_range.days_compliant,
                days_total=bs_range.n_calendar_nights,
                compliance_pct=round(bs_range.compliance_pct, 1),
            )
        return NightlySummaryResponse(
            nights=[],
            total_nights=total,
            page=page,
            page_size=page_size,
            compliance=early_compliance,
        )

    day_ids = [int(d.id) for d in day_rows]

    # For each day get the earliest enabled session (representative for stats).
    # Defense-in-depth: join through Device.profile_id even though day_ids are
    # already profile-scoped by the Day query above.
    session_rows = (
        await db_session.execute(
            select(models.Session.id, models.Session.day_id)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(
                models.Session.day_id.in_(day_ids),
                models.Session.enabled.is_(True),
                models.Device.profile_id == profile_id,
            )
            .order_by(models.Session.day_id, models.Session.start_time)
        )
    ).all()

    day_to_session: dict[int, int] = {}
    for session_id, s_day_id in session_rows:
        s_day_id_int = int(s_day_id)
        if s_day_id_int not in day_to_session:
            day_to_session[s_day_id_int] = int(session_id)

    session_ids = list(day_to_session.values())

    # Statistics rows for MV/RR/TV — defense-in-depth join through Device.profile_id.
    stats_by_session: dict[int, models.Statistics] = {}
    if session_ids:
        stat_rows = (
            (
                await db_session.execute(
                    select(models.Statistics)
                    .join(
                        models.Session,
                        models.Statistics.session_id == models.Session.id,
                    )
                    .join(
                        models.Device,
                        models.Session.device_id == models.Device.id,
                    )
                    .where(
                        models.Statistics.session_id.in_(session_ids),
                        models.Device.profile_id == profile_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for stat in stat_rows:
            stats_by_session[int(stat.session_id)] = stat

    nights: list[NightlyRow] = []

    for day in day_rows:
        day_id = int(day.id)
        session_id = day_to_session.get(day_id)
        stats = stats_by_session.get(session_id) if session_id else None
        bs_night = bs_by_date.get(day.date)

        # Analysis-derived fields from BreathService (A2)
        rera_index: float | None = None
        rera_index_reason: str | None = None
        rdi: float | None = None
        rdi_reason: str | None = None
        fl_median: float | None = None
        fl_median_reason: str | None = None
        fl_p95: float | None = None
        fl_p95_reason: str | None = None
        fl_max: float | None = None
        fl_max_reason: str | None = None
        rera_proxy_count: int | None = None
        rera_proxy_reason: str | None = None
        ti_median_s: float | None = None
        ti_median_reason: str | None = None
        ie_ratio: float | None = None
        ie_ratio_reason: str | None = None

        if bs_night is not None:
            # rera_proxy: raw count from service (not divided by hours)
            if bs_night.rera_count is not None:
                rera_proxy_count = bs_night.rera_count
            elif bs_night.rera_reason is not None:
                rera_proxy_reason = str(bs_night.rera_reason)

            # rera_index and rdi mapped straight through from service DTO
            rera_index = bs_night.rera_index
            rera_index_reason = str_or_none(bs_night.rera_index_reason)
            rdi = bs_night.rdi
            rdi_reason = str_or_none(bs_night.rdi_reason)

            if bs_night.fl_median is not None:
                fl_median = round(bs_night.fl_median, 4)
            if bs_night.fl_reason is not None:
                reason_str = str_or_none(bs_night.fl_reason)
                fl_median_reason = reason_str
                fl_p95_reason = reason_str
                fl_max_reason = reason_str
            if bs_night.fl_95th is not None:
                fl_p95 = round(bs_night.fl_95th, 4)
            if bs_night.fl_max is not None:
                fl_max = round(bs_night.fl_max, 4)

            # Ti and I:E from BreathService
            if bs_night.ti_median_s is not None:
                ti_median_s = round(bs_night.ti_median_s, 3)
            ti_median_reason = str_or_none(bs_night.ti_median_reason)
            if bs_night.ie_ratio_median is not None:
                ie_ratio = round(bs_night.ie_ratio_median, 3)
            ie_ratio_reason = str_or_none(bs_night.ie_ratio_reason)
        else:
            rera_index_reason = "analysis_not_run"
            rdi_reason = "analysis_not_run"
            fl_median_reason = "analysis_not_run"
            fl_p95_reason = "analysis_not_run"
            fl_max_reason = "analysis_not_run"
            rera_proxy_reason = "analysis_not_run"
            ti_median_reason = "analysis_not_run"
            ie_ratio_reason = "analysis_not_run"

        usage_h = day.total_therapy_hours

        nights.append(
            NightlyRow(
                date=day.date,
                usage_hours=round(usage_h, 2) if usage_h is not None else None,
                session_count=day.session_count or 0,
                ahi=round(day.ahi, 2) if day.ahi is not None else None,
                oai=round(day.oai, 2) if day.oai is not None else None,
                cai=round(day.cai, 2) if day.cai is not None else None,
                hi=round(day.hi, 2) if day.hi is not None else None,
                rera_index=rera_index,
                rera_index_reason=rera_index_reason,
                rdi=rdi,
                rdi_reason=rdi_reason,
                fl_median=fl_median,
                fl_median_reason=fl_median_reason,
                fl_p95=fl_p95,
                fl_p95_reason=fl_p95_reason,
                fl_max=fl_max,
                fl_max_reason=fl_max_reason,
                rera_proxy_count=rera_proxy_count,
                rera_proxy_reason=rera_proxy_reason,
                ti_median_s=ti_median_s,
                ti_median_reason=ti_median_reason,
                ie_ratio=ie_ratio,
                ie_ratio_reason=ie_ratio_reason,
                pressure_median_cmh2o=day.pressure_median,
                pressure_95th_cmh2o=day.pressure_95th,
                epap_median_cmh2o=day.epap_median,
                leak_median_lpm=day.leak_median,
                leak_95th_lpm=day.leak_95th,
                leak_above_24_pct=None,
                leak_above_24_pct_reason="not_available",
                rr_mean_bpm=stats.respiratory_rate_mean if stats else None,
                tv_mean_ml=(
                    round(stats.tidal_volume_mean * 1000, 1)
                    if stats and stats.tidal_volume_mean is not None
                    else None
                ),
                mv_mean_lpm=stats.minute_ventilation_mean if stats else None,
                spo2_mean_pct=day.spo2_mean,
                device_id=day.device_id,
            )
        )

    compliance: ComplianceFields | None = None
    if len(day_rows) > 1 or (start != end):
        if bs_range is not None:
            compliance = ComplianceFields(
                threshold_hours=compliance_threshold_hours,
                days_compliant=bs_range.days_compliant,
                days_total=bs_range.n_calendar_nights,
                compliance_pct=round(bs_range.compliance_pct, 1),
            )

    # Device capabilities block — populated when BreathService resolved a device.
    # analysis_run is computed once here to avoid re-running the three-join query
    # inside build_device_capabilities.
    analysis_run = await _has_analysis(db_session, profile_id)
    dev_caps = None
    if bs_range is not None and bs_range.device_id and bs_range.device_id > 0:
        dev_caps = await build_device_capabilities(
            db_session,
            profile_id,
            bs_range.device_id,
            date_start=start,
            date_end=end,
            analysis_run=analysis_run,
        )

    return NightlySummaryResponse(
        nights=nights,
        total_nights=total,
        page=page,
        page_size=page_size,
        compliance=compliance,
        device_capabilities=dev_caps,
    )


def register(mcp: FastMCP) -> None:
    from snore.mcp.validation import (  # noqa: PLC0415
        parse_date_range,
        validate_compliance_threshold,
        validate_page_args,
    )

    @mcp.tool()
    @tool_error_boundary
    async def get_nightly_summary(
        ctx: Context,
        start: str,
        end: str,
        device_id: int | None = None,
        page: int = 1,
        page_size: int = 30,
        compliance_threshold_hours: float = _DEFAULT_COMPLIANCE_THRESHOLD_HOURS,
    ) -> dict[str, Any]:
        """Return per-night therapy summary for a date range.

        Paginated at 30 nights/call (adjustable). Analysis-derived fields (RERA
        index, RDI) are null + reason "analysis_not_run" when analysis has not
        been run. Compliance fields are included in the response.

        The ``compliance`` block is present whenever ``start != end`` (range
        mode), even when the range contains no night data rows; it is ``null``
        only for single-date requests. ``days_total`` counts CALENDAR nights in
        the requested range — nights without data count as non-compliant.

        ``rera_index_reason`` may be ``"duration_zero"`` when a RERA count
        exists but therapy hours for the night is zero, making the per-hour
        rate undefined.

        Args:
            start: Start date in YYYY-MM-DD format.
            end: End date in YYYY-MM-DD format.
            device_id: Optional device ID filter.
            page: Page number (1-based). Default 1.
            page_size: Nights per page (must be between 1 and 90). Default 30.
            compliance_threshold_hours: Hours to count as compliant (default 4.0).

        Returns:
            NightlySummaryResponse with nights list, pagination, and compliance block.
        """
        from snore.mcp.tools.summary import (
            get_nightly_summary as _impl,  # noqa: PLC0415
        )

        start_d, end_d = parse_date_range(start, end)
        n_calendar = (end_d - start_d).days + 1
        if n_calendar > MAX_NIGHTLY_RANGE:
            raise ValidationError(
                f"Date range spans {n_calendar} nights; maximum per call is {MAX_NIGHTLY_RANGE}. "
                "Use multiple calls to page over longer ranges."
            )
        capped_page_size = validate_page_args(page, page_size)
        validate_compliance_threshold(compliance_threshold_hours)
        return await _scope_and_run(
            ctx,
            _impl,
            tool_name="get_nightly_summary",
            start=start_d,
            end=end_d,
            device_id=device_id,
            page=page,
            page_size=capped_page_size,
            compliance_threshold_hours=compliance_threshold_hours,
        )
