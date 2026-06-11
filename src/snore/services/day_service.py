"""Day aggregate service — queries Day table and returns Pydantic models."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.schemas import DayDetail, DayListItem

__all__ = ["DayService"]


class DayService:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def list_days(
        self,
        from_date: date | None = None,
        to_date: date | None = None,
        device_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DayListItem], int]:
        """Return paginated list of days with optional filters."""
        query = select(models.Day)

        if from_date is not None:
            query = query.where(models.Day.date >= from_date)
        if to_date is not None:
            query = query.where(models.Day.date <= to_date)
        if device_id is not None:
            query = query.where(models.Day.device_id == device_id)

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db_session.execute(count_query).scalar_one()

        query = query.order_by(models.Day.date.desc())
        if limit > 0:
            query = query.limit(limit)
        query = query.offset(offset)

        rows = self.db_session.execute(query).scalars().all()
        items = [DayListItem.model_validate(d) for d in rows]
        return items, total

    def get_day(self, day_date: date) -> DayDetail:
        """Return detailed day record with session IDs.

        Raises:
            NotFoundError: If no Day record exists for the date.
        """
        stmt = select(models.Day).where(models.Day.date == day_date)
        day = self.db_session.execute(stmt).scalar_one_or_none()

        if day is None:
            raise NotFoundError(f"No data found for date {day_date}")

        session_ids = [
            row[0]
            for row in self.db_session.execute(
                select(models.Session.id).where(models.Session.day_id == day.id)
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
            session_ids=session_ids,
        )
