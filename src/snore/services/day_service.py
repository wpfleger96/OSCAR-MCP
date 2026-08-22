"""Day aggregate service — queries Day table and returns Pydantic models."""

from __future__ import annotations

import logging

from datetime import date

from sqlalchemy import select

from snore.database import models
from snore.exceptions import NotFoundError
from snore.metrics import DAY_METRIC_STAT_COLUMNS
from snore.services._base import ProfileScopedService, paginate
from snore.services.schemas import DayDetail, DayListItem, HealthNightSummaryRead

logger = logging.getLogger(__name__)

__all__ = ["DayService"]


class DayService(ProfileScopedService):
    async def list_days(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
        device_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DayListItem], int]:
        """Return paginated list of days with optional filters."""
        query = (
            select(models.Day)
            .join(models.Device, models.Day.device_id == models.Device.id)
            .where(self._profile_filter())
        )

        if from_date is not None:
            query = query.where(models.Day.date >= from_date)
        if to_date is not None:
            query = query.where(models.Day.date <= to_date)
        if device_id is not None:
            query = query.where(models.Day.device_id == device_id)

        result, total = await paginate(
            self.db_session,
            query,
            order_by=models.Day.date.desc(),
            limit=limit,
            offset=offset,
        )
        items = [DayListItem.model_validate(d) for d in result.scalars().all()]
        return items, total

    async def list_dates(self) -> list[date]:
        query = (
            select(models.Day.date)
            .distinct()
            .join(models.Device, models.Day.device_id == models.Device.id)
            .where(self._profile_filter())
            .order_by(models.Day.date)
        )
        rows = (await self.db_session.execute(query)).scalars().all()
        return list(rows)

    async def get_day(self, day_date: date, device_id: int | None = None) -> DayDetail:
        """Return detailed day record with session IDs.

        When multiple devices have Day rows on the same date (e.g. a machine-switch
        date), returns the first row ordered by device_id with a warning rather than
        raising MultipleResultsFound.  Pass device_id to select a specific device.

        Raises NotFoundError if no day exists for this date in the actor's profile.
        """
        stmt = (
            select(models.Day)
            .join(models.Device, models.Day.device_id == models.Device.id)
            .where(self._profile_filter(), models.Day.date == day_date)
        )
        if device_id is not None:
            stmt = stmt.where(models.Day.device_id == device_id)

        stmt = stmt.order_by(models.Day.device_id)

        rows = (await self.db_session.execute(stmt)).scalars().all()

        if not rows:
            if device_id is not None:
                raise NotFoundError(
                    f"No data found for device_id={device_id} on date {day_date}"
                )
            raise NotFoundError(f"No data found for date {day_date}")

        if len(rows) > 1:
            device_ids = [r.device_id for r in rows]
            logger.warning(
                f"Multiple Day rows for date {day_date}: device_ids={device_ids}; "
                f"returning device_id={rows[0].device_id}"
            )

        day = rows[0]

        session_ids = [
            row[0]
            for row in (
                await self.db_session.execute(
                    select(models.Session.id)
                    .where(models.Session.day_id == day.id)
                    .order_by(models.Session.start_time)
                )
            ).all()
        ]

        health_summary_row = (
            await self.db_session.execute(
                select(models.HealthNightlySummary).where(
                    models.HealthNightlySummary.profile_id == self.profile_id,
                    models.HealthNightlySummary.night_date == day_date,
                )
            )
        ).scalar_one_or_none()

        health_sleep = (
            HealthNightSummaryRead.model_validate(health_summary_row)
            if health_summary_row is not None
            else None
        )

        # Identity copies: Day metric columns that DayDetail exposes under the
        # same name.  Columns DayDetail renames (pressure_median → avg_pressure,
        # leak_median → avg_leak, spo2_mean → avg_spo2) are not DayDetail
        # fields, so the membership test skips them; they are passed explicitly.
        stat_fields = {
            spec.name: getattr(day, spec.name)
            for spec in DAY_METRIC_STAT_COLUMNS
            if spec.name in DayDetail.model_fields
        }

        return DayDetail(
            **DayListItem.model_validate(day).model_dump(),
            **stat_fields,
            oai=day.oai,
            cai=day.cai,
            hi=day.hi,
            avg_pressure=day.pressure_median,
            avg_leak=day.leak_median,
            avg_spo2=day.spo2_mean,
            obstructive_apneas=day.obstructive_apneas or 0,
            central_apneas=day.central_apneas or 0,
            hypopneas=day.hypopneas or 0,
            reras=day.reras or 0,
            session_ids=session_ids,
            health_sleep=health_sleep,
        )
