"""get_nightly_summary tool — StatsService / DayService adapter."""

from __future__ import annotations

from datetime import date
from typing import Any

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
    device_id: int | None = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
    compliance_threshold_hours: float = _DEFAULT_COMPLIANCE_THRESHOLD_HOURS,
) -> NightlySummaryResponse:
    """Return per-night therapy summary for a date range.

    Analysis-derived fields (RERA index, RDI) are read from the latest
    AnalysisResult for each session; when absent they are null with reason
    "analysis_not_run" (A2).

    Compliance fields (compliance_pct, days_compliant, days_total) are
    included in range mode using the supplied threshold (default 4 h).
    """
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

    # For each day get the earliest enabled session (representative for analysis/stats)
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

    # Latest AnalysisResult per session via row_number window
    analysis_by_session: dict[int, Any] = {}
    if session_ids:
        subq = (
            select(
                models.AnalysisResult.session_id,
                models.AnalysisResult.programmatic_result_json,
                func.row_number()
                .over(
                    partition_by=models.AnalysisResult.session_id,
                    order_by=models.AnalysisResult.created_at.desc(),
                )
                .label("rn"),
            )
            .where(models.AnalysisResult.session_id.in_(session_ids))
            .subquery()
        )
        analysis_rows = (
            await db_session.execute(
                select(subq.c.session_id, subq.c.programmatic_result_json).where(
                    subq.c.rn == 1
                )
            )
        ).all()
        for s_id, payload in analysis_rows:
            analysis_by_session[int(s_id)] = payload if payload else {}

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
    days_compliant = 0

    for day in day_rows:
        day_id = int(day.id)
        session_id = day_to_session.get(day_id)
        analysis = analysis_by_session.get(session_id, {}) if session_id else {}
        stats = stats_by_session.get(session_id) if session_id else None

        # Analysis-derived fields (A2)
        rera_index: float | None = None
        rera_index_reason: str | None = None
        rdi: float | None = None
        rdi_reason: str | None = None

        if analysis:
            mode_results = analysis.get("mode_results", {})
            duration_h = analysis.get("session_duration_hours", 0.0) or 0.0
            for _mode, mode_data in mode_results.items():
                if isinstance(mode_data, dict) and mode_data.get(
                    "rera_detection_enabled"
                ):
                    reras_list = mode_data.get("reras", [])
                    rdi_val = mode_data.get("rdi")
                    if duration_h > 0 and reras_list is not None:
                        rera_index = round(len(reras_list) / duration_h, 2)
                    if rdi_val is not None:
                        rdi = round(float(rdi_val), 2)
                    break
        else:
            rera_index_reason = "analysis_not_run"
            rdi_reason = "analysis_not_run"

        usage_h = day.total_therapy_hours
        if usage_h and usage_h >= compliance_threshold_hours:
            days_compliant += 1

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
        compliance = ComplianceFields(
            threshold_hours=compliance_threshold_hours,
            days_compliant=days_compliant,
            days_total=len(day_rows),
            compliance_pct=(
                round(days_compliant / len(day_rows) * 100, 1) if day_rows else 0.0
            ),
        )

    return NightlySummaryResponse(
        nights=nights,
        total_nights=total,
        page=page,
        page_size=page_size,
        compliance=compliance,
    )
