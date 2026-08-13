"""Health service — reads Apple Health tables for the authenticated profile."""

from __future__ import annotations

from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.schemas import (
    HealthNightDetailRead,
    HealthNightSummaryRead,
    HealthSampleRead,
)

__all__ = ["HealthService"]

_SLEEP_RECORD_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
_SPO2_RECORD_TYPE = "HKQuantityTypeIdentifierOxygenSaturation"
_RR_RECORD_TYPE = "HKQuantityTypeIdentifierRespiratoryRate"


class HealthService:
    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self.db_session = db_session
        self.profile_id = profile_id

    async def list_nights(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[HealthNightSummaryRead], int]:
        """Return paginated nightly summaries for the profile, most-recent first."""
        query = select(models.HealthNightlySummary).where(
            models.HealthNightlySummary.profile_id == self.profile_id
        )
        if from_date is not None:
            query = query.where(models.HealthNightlySummary.night_date >= from_date)
        if to_date is not None:
            query = query.where(models.HealthNightlySummary.night_date <= to_date)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db_session.execute(count_query)).scalar_one()

        query = query.order_by(models.HealthNightlySummary.night_date.desc())
        if limit > 0:
            query = query.limit(limit)
        query = query.offset(offset)

        rows = (await self.db_session.execute(query)).scalars().all()
        items = [HealthNightSummaryRead.model_validate(r) for r in rows]
        return items, total

    async def list_night_dates(self) -> list[date]:
        """Return all night dates for the profile that have sleep summaries, ascending."""
        query = (
            select(models.HealthNightlySummary.night_date)
            .where(models.HealthNightlySummary.profile_id == self.profile_id)
            .order_by(models.HealthNightlySummary.night_date)
        )
        rows = (await self.db_session.execute(query)).scalars().all()
        return list(rows)

    async def get_night_detail(self, night_date: date) -> HealthNightDetailRead:
        """Return nightly sleep summary with aggregated SpO2 and respiratory rate.

        SpO2 values stored as fractions (0–1) are normalized to percent on read.

        Raises NotFoundError when no summary exists for this night.
        """
        summary = (
            await self.db_session.execute(
                select(models.HealthNightlySummary).where(
                    models.HealthNightlySummary.profile_id == self.profile_id,
                    models.HealthNightlySummary.night_date == night_date,
                )
            )
        ).scalar_one_or_none()

        if summary is None:
            raise NotFoundError(f"No health data found for night {night_date}")

        # Single-pass conditional aggregation for SpO2 avg/min and RR avg.
        agg = (
            await self.db_session.execute(
                select(
                    func.avg(
                        case(
                            (
                                models.HealthSample.record_type == _SPO2_RECORD_TYPE,
                                models.HealthSample.value_num,
                            )
                        )
                    ).label("avg_spo2"),
                    func.min(
                        case(
                            (
                                models.HealthSample.record_type == _SPO2_RECORD_TYPE,
                                models.HealthSample.value_num,
                            )
                        )
                    ).label("min_spo2"),
                    func.avg(
                        case(
                            (
                                models.HealthSample.record_type == _RR_RECORD_TYPE,
                                models.HealthSample.value_num,
                            )
                        )
                    ).label("avg_rr"),
                ).where(
                    models.HealthSample.profile_id == self.profile_id,
                    models.HealthSample.night_date == night_date,
                    models.HealthSample.record_type.in_(
                        [_SPO2_RECORD_TYPE, _RR_RECORD_TYPE]
                    ),
                )
            )
        ).one()

        avg_spo2: float | None = agg.avg_spo2
        min_spo2: float | None = agg.min_spo2
        avg_rr: float | None = agg.avg_rr

        # Normalize fraction-encoded SpO2 to percent.
        if avg_spo2 is not None and avg_spo2 <= 1.5:
            avg_spo2 = round(avg_spo2 * 100, 1)
            if min_spo2 is not None:
                min_spo2 = round(min_spo2 * 100, 1)
        elif avg_spo2 is not None:
            avg_spo2 = round(avg_spo2, 1)
            if min_spo2 is not None:
                min_spo2 = round(min_spo2, 1)

        return HealthNightDetailRead(
            **HealthNightSummaryRead.model_validate(summary).model_dump(),
            avg_spo2_pct=avg_spo2,
            min_spo2_pct=min_spo2,
            avg_rr=round(avg_rr, 2) if avg_rr is not None else None,
        )

    async def get_night_samples(
        self, night_date: date, source_name: str | None = None
    ) -> list[HealthSampleRead]:
        """Return sleep-stage samples for the night, ordered by start time.

        Source filter: explicit source_name overrides; when omitted the night's
        preferred_source is used (no filter when preferred_source is also None).

        Raises NotFoundError when no summary row exists for this night.
        """
        row = (
            await self.db_session.execute(
                select(
                    models.HealthNightlySummary.id,
                    models.HealthNightlySummary.preferred_source,
                ).where(
                    models.HealthNightlySummary.profile_id == self.profile_id,
                    models.HealthNightlySummary.night_date == night_date,
                )
            )
        ).one_or_none()

        if row is None:
            raise NotFoundError(f"No health data found for night {night_date}")

        effective_source = (
            source_name if source_name is not None else row.preferred_source
        )

        query = (
            select(models.HealthSample)
            .where(
                models.HealthSample.profile_id == self.profile_id,
                models.HealthSample.night_date == night_date,
                models.HealthSample.record_type == _SLEEP_RECORD_TYPE,
            )
            .order_by(models.HealthSample.start_time)
        )
        if effective_source is not None:
            query = query.where(models.HealthSample.source_name == effective_source)

        rows = (await self.db_session.execute(query)).scalars().all()
        return [HealthSampleRead.model_validate(r) for r in rows]
