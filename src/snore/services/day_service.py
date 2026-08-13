"""Day aggregate service — queries Day table and returns Pydantic models."""

from __future__ import annotations

import logging

from datetime import date

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.schemas import DayDetail, DayListItem

logger = logging.getLogger(__name__)

__all__ = ["DayService"]


class DayService:
    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self.db_session = db_session
        self.profile_id = profile_id

    def _profile_filter(self) -> ColumnElement[bool]:
        """WHERE predicate: join session's device to this profile."""
        return models.Device.profile_id == self.profile_id

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

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db_session.execute(count_query)).scalar_one()

        query = query.order_by(models.Day.date.desc())
        if limit > 0:
            query = query.limit(limit)
        query = query.offset(offset)

        rows = (await self.db_session.execute(query)).scalars().all()
        items = [DayListItem.model_validate(d) for d in rows]
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

        return DayDetail(
            **DayListItem.model_validate(day).model_dump(),
            oai=day.oai,
            cai=day.cai,
            hi=day.hi,
            avg_pressure=day.pressure_mean,
            avg_leak=day.leak_median,
            avg_spo2=day.spo2_mean,
            pressure_min=day.pressure_min,
            pressure_max=day.pressure_max,
            pressure_median=day.pressure_median,
            pressure_95th=day.pressure_95th,
            epap_min=day.epap_min,
            epap_max=day.epap_max,
            epap_median=day.epap_median,
            epap_mean=day.epap_mean,
            epap_95th=day.epap_95th,
            leak_min=day.leak_min,
            leak_max=day.leak_max,
            leak_mean=day.leak_mean,
            leak_95th=day.leak_95th,
            spo2_min=day.spo2_min,
            spo2_max=day.spo2_max,
            obstructive_apneas=day.obstructive_apneas or 0,
            central_apneas=day.central_apneas or 0,
            hypopneas=day.hypopneas or 0,
            reras=day.reras or 0,
            session_ids=session_ids,
        )
