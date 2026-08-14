"""
Day aggregation and management logic (OSCAR-compatible).

Handles day splitting logic and aggregation of session statistics into daily records.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from snore.database.models import Day, Statistics
from snore.database.models import Session as SessionModel


class DayManager:
    """Manages day splitting and aggregation logic (OSCAR-compatible)."""

    DEFAULT_SPLIT_TIME = time(12, 0)

    @classmethod
    def get_day_for_session(cls, session_start: datetime) -> date:
        """
        Determine which calendar day a session belongs to based on split time.

        OSCAR logic: Sessions before the split time belong to the previous day.
        Split time is hardcoded to 12:00 noon.

        Args:
            session_start: Session start datetime

        Returns:
            Date the session belongs to
        """
        if session_start.time() < cls.DEFAULT_SPLIT_TIME:
            return session_start.date() - timedelta(days=1)
        return session_start.date()

    @classmethod
    async def get_or_create_day(
        cls,
        device_id: int,
        day_date: date,
        db_session: AsyncSession,
    ) -> Day:
        """
        Get or create day WITHOUT triggering aggregation.

        Use this when you want to defer aggregation (e.g., batch imports)
        or when aggregation will be handled separately.

        Args:
            device_id: Device ID
            day_date: Date for the day record
            db_session: SQLAlchemy async database session

        Returns:
            Day object (without aggregated statistics)
        """
        day = (
            (
                await db_session.execute(
                    select(Day).filter_by(device_id=device_id, date=day_date)
                )
            )
            .scalars()
            .first()
        )

        if not day:
            day = Day(device_id=device_id, date=day_date)
            db_session.add(day)
            await db_session.flush()

        return day

    @classmethod
    async def create_or_update_day(
        cls,
        device_id: int,
        day_date: date,
        db_session: AsyncSession,
    ) -> Day:
        """
        Create or update a day record with aggregated statistics from all sessions.

        Args:
            device_id: Device ID
            day_date: Date for the day record
            db_session: SQLAlchemy async database session

        Returns:
            Updated Day object
        """
        day = await cls.get_or_create_day(device_id, day_date, db_session)
        await cls._aggregate_day_statistics(day, db_session)
        return day

    @staticmethod
    def _effective_session_hours(
        stats: Statistics | None, session: SessionModel
    ) -> float:
        """Effective therapy hours for a session.

        Prefers statistics.usage_hours (actual mask-on time) over session span.
        usage_hours == 0.0 is treated as known-zero; no span fallback applies.
        """
        if stats is not None and stats.usage_hours is not None:
            return stats.usage_hours
        return (session.duration_seconds or 0) / 3600

    @classmethod
    def _weighted_average(
        cls,
        stat_pairs: list[tuple[Statistics, SessionModel]],
        attr: str,
    ) -> float | None:
        """Calculate usage-weighted average for a statistic across sessions.

        stat_pairs must be pre-aligned: each tuple is the Statistics row and its
        owning SessionModel. Weights prefer usage_hours over session span; entries
        with zero effective hours are excluded so they cannot distort the average.
        """
        values = [
            (getattr(s, attr), cls._effective_session_hours(s, sess))
            for s, sess in stat_pairs
            if getattr(s, attr) is not None
        ]
        # Zero-weight entries (known-zero usage) cannot contribute to a rate average.
        values = [(v, w) for v, w in values if w > 0]
        if not values:
            return None
        total_weight = sum(w for _, w in values)
        return float(sum(v * w for v, w in values) / total_weight)

    @classmethod
    async def _aggregate_day_statistics(
        cls, day: Day, db_session: AsyncSession
    ) -> None:
        """
        Aggregate statistics from all sessions belonging to a day.

        Args:
            day: Day object to update
            db_session: SQLAlchemy async database session
        """
        sessions = (
            (
                await db_session.execute(
                    select(SessionModel)
                    .filter_by(day_id=day.id, enabled=True)
                    .options(joinedload(SessionModel.statistics))
                )
            )
            .scalars()
            .all()
        )

        if not sessions:
            day.session_count = 0
            day.total_therapy_hours = 0.0
            day.obstructive_apneas = 0
            day.central_apneas = 0
            day.hypopneas = 0
            day.reras = 0
            day.ahi = None
            day.oai = None
            day.cai = None
            day.hi = None
            day.pressure_min = None
            day.pressure_max = None
            day.pressure_median = None
            day.pressure_mean = None
            day.pressure_95th = None
            day.leak_min = None
            day.leak_max = None
            day.leak_median = None
            day.leak_mean = None
            day.leak_95th = None
            day.spo2_min = None
            day.spo2_max = None
            day.spo2_mean = None
            return

        day.session_count = len(sessions)

        day.total_therapy_hours = sum(
            cls._effective_session_hours(s.statistics, s) for s in sessions
        )

        # Pre-aligned pairs: each Statistics row with its owning session.
        # Building this once avoids the zip-misalignment bug that occurs when
        # not every session has a Statistics row.
        stat_pairs: list[tuple[Statistics, SessionModel]] = [
            (s.statistics, s) for s in sessions if s.statistics
        ]
        stats_records = [s for s, _ in stat_pairs]

        if stat_pairs:
            day.obstructive_apneas = sum(
                s.obstructive_apneas for s in stats_records if s.obstructive_apneas
            )
            day.central_apneas = sum(
                s.central_apneas for s in stats_records if s.central_apneas
            )
            day.hypopneas = sum(s.hypopneas for s in stats_records if s.hypopneas)
            day.reras = sum(s.reras for s in stats_records if s.reras)

            total_hours = day.total_therapy_hours
            if total_hours > 0:
                day.ahi = cls._weighted_average(stat_pairs, "ahi")
                day.oai = cls._weighted_average(stat_pairs, "oai")
                day.cai = cls._weighted_average(stat_pairs, "cai")
                day.hi = cls._weighted_average(stat_pairs, "hi")

            pressure_mins = [s.pressure_min for s in stats_records if s.pressure_min]
            pressure_maxs = [s.pressure_max for s in stats_records if s.pressure_max]
            day.pressure_min = min(pressure_mins) if pressure_mins else None
            day.pressure_max = max(pressure_maxs) if pressure_maxs else None

            if total_hours > 0:
                day.pressure_median = cls._weighted_average(
                    stat_pairs, "pressure_median"
                )
                day.pressure_mean = cls._weighted_average(stat_pairs, "pressure_mean")
                day.pressure_95th = cls._weighted_average(stat_pairs, "pressure_95th")

            epap_mins = [s.epap_min for s in stats_records if s.epap_min]
            epap_maxs = [s.epap_max for s in stats_records if s.epap_max]
            day.epap_min = min(epap_mins) if epap_mins else None
            day.epap_max = max(epap_maxs) if epap_maxs else None

            if total_hours > 0:
                day.epap_median = cls._weighted_average(stat_pairs, "epap_median")
                day.epap_mean = cls._weighted_average(stat_pairs, "epap_mean")
                day.epap_95th = cls._weighted_average(stat_pairs, "epap_95th")

            leak_mins = [s.leak_min for s in stats_records if s.leak_min]
            leak_maxs = [s.leak_max for s in stats_records if s.leak_max]
            day.leak_min = min(leak_mins) if leak_mins else None
            day.leak_max = max(leak_maxs) if leak_maxs else None

            if total_hours > 0:
                day.leak_median = cls._weighted_average(stat_pairs, "leak_median")
                day.leak_mean = cls._weighted_average(stat_pairs, "leak_mean")
                day.leak_95th = cls._weighted_average(stat_pairs, "leak_95th")

            spo2_mins = [s.spo2_min for s in stats_records if s.spo2_min]
            spo2_maxs = [s.spo2_max for s in stats_records if s.spo2_max]
            day.spo2_min = min(spo2_mins) if spo2_mins else None
            day.spo2_max = max(spo2_maxs) if spo2_maxs else None

            if total_hours > 0:
                day.spo2_mean = cls._weighted_average(stat_pairs, "spo2_mean")

    @classmethod
    async def link_session_to_day(
        cls,
        session: SessionModel,
        device_id: int,
        db_session: AsyncSession,
    ) -> Day:
        """
        Link a session to its appropriate day record based on day-splitting logic.

        Creates or updates the day record with aggregated statistics.

        Args:
            session: Session to link
            device_id: Device ID the session belongs to
            db_session: SQLAlchemy async database session

        Returns:
            Day object the session was linked to
        """
        day_date = cls.get_day_for_session(session.start_time)

        day = await cls.get_or_create_day(device_id, day_date, db_session)

        session.day_id = day.id

        await cls._aggregate_day_statistics(day, db_session)

        return day

    @classmethod
    async def recalculate_day(cls, day: Day, db_session: AsyncSession) -> None:
        """
        Recalculate aggregated statistics for a day.

        Args:
            day: Day object to recalculate
            db_session: SQLAlchemy async database session
        """
        await cls._aggregate_day_statistics(day, db_session)
