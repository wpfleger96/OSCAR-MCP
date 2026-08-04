"""get_nightly_summary tool — StatsService / DayService + BreathService adapter.

Timestamp contract (A6): date fields are Python ``date`` objects serialized as
``YYYY-MM-DD`` — no timezone issue since dates have no time component.

Compliance and breath-level FL/RERA fields are populated via
BreathService.get_nightly_range_summary() when available (ranges ≤ 90 nights and
no device ambiguity).  For ranges > 90 nights or when BreathService cannot
resolve the device, compliance falls back to inline arithmetic over
Day.total_therapy_hours and FL/RERA fields are null + reason.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.mcp.schemas import (
    ComplianceFields,
    NightlyRow,
    NightlySummaryResponse,
)

_DEFAULT_PAGE_SIZE = 30
_DEFAULT_COMPLIANCE_THRESHOLD_HOURS = 4.0


async def get_nightly_summary(
    db_session: AsyncSession,
    start: date,
    end: date,
    profile_id: int = 0,
    device_id: int | None = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
    compliance_threshold_hours: float = _DEFAULT_COMPLIANCE_THRESHOLD_HOURS,
) -> NightlySummaryResponse:
    """Return per-night therapy summary for a date range.

    Analysis-derived fields (RERA index, RDI, FL) are populated from
    BreathService.get_nightly_range_summary() when possible; absent entries are
    null with reason (A2).  Compliance fields use the same seam.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        DeviceAmbiguityError,
        NightlyAnalysisSummary,
        NightlyRangeSummary,
    )

    # Attempt BreathService range summary for compliance + FL/RERA.
    # Falls back gracefully on: range > 90 nights, device ambiguity, no profile.
    bs_range: NightlyRangeSummary | None = None
    if profile_id:
        n_calendar = (end - start).days + 1
        if n_calendar <= 90:
            try:
                bs_range = await BreathService(
                    db_session, profile_id
                ).get_nightly_range_summary(
                    start,
                    end,
                    device_id=device_id,
                    compliance_threshold_hours=compliance_threshold_hours,
                )
            except (ValueError, DeviceAmbiguityError):
                bs_range = None

    # Index per-night analysis summaries by therapy_date for O(1) lookup.
    bs_by_date: dict[date, NightlyAnalysisSummary] = {}
    if bs_range is not None:
        for night in bs_range.nights:
            bs_by_date[night.therapy_date] = night

    # Count total matching days for pagination
    count_q = select(func.count(models.Day.id)).where(
        models.Day.date >= start,
        models.Day.date <= end,
    )
    if device_id is not None:
        count_q = count_q.where(models.Day.device_id == device_id)
    total = (await db_session.execute(count_q)).scalar_one()

    # Fetch the page of Day rows directly for full field access
    day_q = (
        select(models.Day)
        .where(models.Day.date >= start, models.Day.date <= end)
        .order_by(models.Day.date.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    if device_id is not None:
        day_q = day_q.where(models.Day.device_id == device_id)
    day_rows = (await db_session.execute(day_q)).scalars().all()

    if not day_rows:
        return NightlySummaryResponse(
            nights=[],
            total_nights=total,
            page=page,
            page_size=page_size,
        )

    day_ids = [int(d.id) for d in day_rows]

    # For each day get the earliest enabled session (representative for stats)
    session_rows = (
        await db_session.execute(
            select(models.Session.id, models.Session.day_id)
            .where(
                models.Session.day_id.in_(day_ids),
                models.Session.enabled.is_(True),
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

    # Statistics rows for MV/RR/TV
    stats_by_session: dict[int, models.Statistics] = {}
    if session_ids:
        stat_rows = (
            (
                await db_session.execute(
                    select(models.Statistics).where(
                        models.Statistics.session_id.in_(session_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        for stat in stat_rows:
            stats_by_session[int(stat.session_id)] = stat

    nights: list[NightlyRow] = []
    days_compliant_inline = 0

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

        if bs_night is not None:
            if bs_night.rera_count is not None:
                duration_h = day.total_therapy_hours or 0.0
                rera_proxy_count = bs_night.rera_count
                if duration_h > 0:
                    rera_index = round(bs_night.rera_count / duration_h, 2)
            elif bs_night.rera_reason is not None:
                rera_index_reason = str(bs_night.rera_reason)
                rera_proxy_reason = str(bs_night.rera_reason)

            if bs_night.fl_median is not None:
                fl_median = round(bs_night.fl_median, 4)
            if bs_night.fl_reason is not None:
                fl_median_reason = str(bs_night.fl_reason)
                fl_p95_reason = str(bs_night.fl_reason)
                fl_max_reason = str(bs_night.fl_reason)
            if bs_night.fl_95th is not None:
                fl_p95 = round(bs_night.fl_95th, 4)
            if bs_night.fl_max is not None:
                fl_max = round(bs_night.fl_max, 4)
        else:
            rera_index_reason = "analysis_not_run"
            rdi_reason = "analysis_not_run"
            fl_median_reason = "analysis_not_run"
            fl_p95_reason = "analysis_not_run"
            fl_max_reason = "analysis_not_run"
            rera_proxy_reason = "analysis_not_run"

        usage_h = day.total_therapy_hours
        if usage_h and usage_h >= compliance_threshold_hours:
            days_compliant_inline += 1

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
                ti_median_s=None,
                ti_median_reason="not_available",
                ie_ratio=None,
                ie_ratio_reason="not_available",
                pressure_median_cmh2o=day.pressure_median,
                pressure_95th_cmh2o=day.pressure_95th,
                epap_median_cmh2o=day.epap_median,
                leak_median_lpm=day.leak_median,
                leak_95th_lpm=day.leak_95th,
                leak_above_24_pct=None,  # requires waveform time-above; Phase 4
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
                days_total=bs_range.n_nights,
                compliance_pct=round(bs_range.compliance_pct, 1),
            )
        else:
            # Inline fallback (range > 90 nights or device ambiguity)
            compliance = ComplianceFields(
                threshold_hours=compliance_threshold_hours,
                days_compliant=days_compliant_inline,
                days_total=len(day_rows),
                compliance_pct=(
                    round(days_compliant_inline / len(day_rows) * 100, 1)
                    if day_rows
                    else 0.0
                ),
            )

    return NightlySummaryResponse(
        nights=nights,
        total_nights=total,
        page=page,
        page_size=page_size,
        compliance=compliance,
    )
