"""
Day aggregation and management logic (OSCAR-compatible).

Handles day splitting logic and aggregation of session statistics into daily records.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

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
    def get_or_create_day(
        cls,
        device_id: int,
        day_date: date,
        db_session: Session,
    ) -> Day:
        """
        Get or create day WITHOUT triggering aggregation.

        Use this when you want to defer aggregation (e.g., batch imports)
        or when aggregation will be handled separately.

        Args:
            device_id: Device ID
            day_date: Date for the day record
            db_session: SQLAlchemy database session

        Returns:
            Day object (without aggregated statistics)
        """
        day = (
            db_session.query(Day).filter_by(device_id=device_id, date=day_date).first()
        )

        if not day:
            day = Day(device_id=device_id, date=day_date)
            db_session.add(day)
            db_session.flush()

        return day

    @classmethod
    def create_or_update_day(
        cls,
        device_id: int,
        day_date: date,
        db_session: Session,
    ) -> Day:
        """
        Create or update a day record with aggregated statistics from all sessions.

        Args:
            device_id: Device ID
            day_date: Date for the day record
            db_session: SQLAlchemy database session

        Returns:
            Updated Day object
        """
        day = cls.get_or_create_day(device_id, day_date, db_session)
        cls._aggregate_day_statistics(day, db_session)
        return day

    @classmethod
    def _weighted_average(
        cls, stats_records: list[Statistics], sessions: list[SessionModel], attr: str
    ) -> float | None:
        """Calculate time-weighted average for a statistic across sessions."""
        values = [
            (getattr(s, attr), sess.duration_seconds / 3600)
            for s, sess in zip(stats_records, sessions, strict=False)
            if getattr(s, attr) is not None and sess.duration_seconds
        ]
        if not values:
            return None
        total_weighted = sum(v * w for v, w in values)
        total_weight = sum(w for _, w in values)
        return float(total_weighted / total_weight)

    @classmethod
    def _aggregate_day_statistics(cls, day: Day, db_session: Session) -> None:
        """
        Aggregate statistics from all sessions belonging to a day.

        Args:
            day: Day object to update
            db_session: SQLAlchemy database session
        """
        sessions = db_session.query(SessionModel).filter_by(day_id=day.id).all()

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

        total_seconds = sum(s.duration_seconds for s in sessions if s.duration_seconds)
        day.total_therapy_hours = total_seconds / 3600.0 if total_seconds else 0.0

        stats_records = [s.statistics for s in sessions if s.statistics]

        if stats_records:
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
                day.ahi = cls._weighted_average(stats_records, sessions, "ahi")
                day.oai = cls._weighted_average(stats_records, sessions, "oai")
                day.cai = cls._weighted_average(stats_records, sessions, "cai")
                day.hi = cls._weighted_average(stats_records, sessions, "hi")

            pressure_mins = [s.pressure_min for s in stats_records if s.pressure_min]
            pressure_maxs = [s.pressure_max for s in stats_records if s.pressure_max]
            day.pressure_min = min(pressure_mins) if pressure_mins else None
            day.pressure_max = max(pressure_maxs) if pressure_maxs else None

            if total_hours > 0:
                day.pressure_median = cls._weighted_average(
                    stats_records, sessions, "pressure_median"
                )
                day.pressure_mean = cls._weighted_average(
                    stats_records, sessions, "pressure_mean"
                )
                day.pressure_95th = cls._weighted_average(
                    stats_records, sessions, "pressure_95th"
                )

            epap_mins = [s.epap_min for s in stats_records if s.epap_min]
            epap_maxs = [s.epap_max for s in stats_records if s.epap_max]
            day.epap_min = min(epap_mins) if epap_mins else None
            day.epap_max = max(epap_maxs) if epap_maxs else None

            if total_hours > 0:
                day.epap_median = cls._weighted_average(
                    stats_records, sessions, "epap_median"
                )
                day.epap_mean = cls._weighted_average(
                    stats_records, sessions, "epap_mean"
                )
                day.epap_95th = cls._weighted_average(
                    stats_records, sessions, "epap_95th"
                )

            leak_mins = [s.leak_min for s in stats_records if s.leak_min]
            leak_maxs = [s.leak_max for s in stats_records if s.leak_max]
            day.leak_min = min(leak_mins) if leak_mins else None
            day.leak_max = max(leak_maxs) if leak_maxs else None

            if total_hours > 0:
                day.leak_median = cls._weighted_average(
                    stats_records, sessions, "leak_median"
                )
                day.leak_mean = cls._weighted_average(
                    stats_records, sessions, "leak_mean"
                )
                day.leak_95th = cls._weighted_average(
                    stats_records, sessions, "leak_95th"
                )

            spo2_mins = [s.spo2_min for s in stats_records if s.spo2_min]
            spo2_maxs = [s.spo2_max for s in stats_records if s.spo2_max]
            day.spo2_min = min(spo2_mins) if spo2_mins else None
            day.spo2_max = max(spo2_maxs) if spo2_maxs else None

            if total_hours > 0:
                day.spo2_mean = cls._weighted_average(
                    stats_records, sessions, "spo2_mean"
                )

    @classmethod
    def link_session_to_day(
        cls,
        session: SessionModel,
        device_id: int,
        db_session: Session,
    ) -> Day:
        """
        Link a session to its appropriate day record based on day-splitting logic.

        Creates or updates the day record with aggregated statistics.

        Args:
            session: Session to link
            device_id: Device ID the session belongs to
            db_session: SQLAlchemy database session

        Returns:
            Day object the session was linked to
        """
        day_date = cls.get_day_for_session(session.start_time)

        day = cls.get_or_create_day(device_id, day_date, db_session)

        session.day_id = day.id

        cls._aggregate_day_statistics(day, db_session)

        return day

    @classmethod
    def recalculate_all_days_for_device(
        cls, device_id: int, db_session: Session
    ) -> int:
        """
        Recalculate all day records for a device.

        Useful after bulk session imports or data corrections.

        Args:
            device_id: Device ID to recalculate
            db_session: SQLAlchemy database session

        Returns:
            Number of day records updated
        """
        days = db_session.query(Day).filter_by(device_id=device_id).all()

        for day in days:
            cls._aggregate_day_statistics(day, db_session)

        return len(days)
