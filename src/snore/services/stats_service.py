"""Statistics service for therapy data aggregation and analysis."""

from bisect import bisect_right
from datetime import date, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from snore.analysis.calculations import (
    PeriodType,
    assess_therapy_effectiveness,
    calculate_ahi_trend_direction,
    calculate_average_ahi,
    calculate_period_statistics,
    calculate_records,
    calculate_trends_extended,
)
from snore.database import models
from snore.services.schemas import EventTypeCount, PeriodStatistics, TherapySummary

__all__ = ["StatsService"]


class StatsService:
    """Service for therapy statistics computation and analysis."""

    def __init__(self, db_session: Session):
        """
        Initialize statistics service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    def _query_days(
        self,
        days_limit: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[models.Day]:
        """
        Query Day records, optionally filtered by date range or rolling window.

        Args:
            days_limit: Only include days within the last N days from today
            from_date: Only include days on or after this date
            to_date: Only include days on or before this date

        Returns:
            List of Day records
        """
        query = select(models.Day)
        if days_limit:
            cutoff_date = date.today() - timedelta(days=days_limit)
            query = query.where(models.Day.date >= cutoff_date)
        if from_date is not None:
            query = query.where(models.Day.date >= from_date)
        if to_date is not None:
            query = query.where(models.Day.date <= to_date)
        return list(self.db_session.execute(query).scalars().all())

    def get_summary(
        self,
        days_limit: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> TherapySummary | None:
        """
        Compute aggregated therapy summary statistics.

        Args:
            days_limit: Only include days within the last N days from today
            from_date: Only include days on or after this date
            to_date: Only include days on or before this date

        Returns:
            TherapySummary with all computed metrics, or None if no data
        """
        day_records = self._query_days(days_limit, from_date=from_date, to_date=to_date)

        if not day_records:
            return None

        dates = [d.date for d in day_records]
        first_date = min(dates)
        last_date = max(dates)
        days_since_last = (date.today() - last_date).days

        day_ids = [d.id for d in day_records]
        days_with_data = len(day_records)

        total_duration = self.db_session.execute(
            select(func.sum(models.Session.duration_seconds))
            .join(models.Day)
            .where(models.Day.id.in_(day_ids))
        ).scalar()
        total_hours = (total_duration or 0) / 3600
        avg_hours = total_hours / days_with_data if days_with_data > 0 else 0

        avg_ahi = calculate_average_ahi(day_records)
        effectiveness = assess_therapy_effectiveness(avg_ahi) if avg_ahi else "unknown"

        # Compute AHI trend over weekly period aggregates (matches CLI behavior)
        weekly_periods = calculate_period_statistics(day_records, "week")
        weekly_ahi_values = [
            ps.avg_ahi for ps in weekly_periods if ps.avg_ahi is not None
        ]
        ahi_trend_direction = calculate_ahi_trend_direction(weekly_ahi_values)

        pressure_values = [
            d.pressure_median for d in day_records if d.pressure_median is not None
        ]
        avg_pressure = (
            sum(pressure_values) / len(pressure_values) if pressure_values else None
        )
        min_pressure = min(pressure_values) if pressure_values else None
        max_pressure = max(pressure_values) if pressure_values else None

        leak_values = [d.leak_median for d in day_records if d.leak_median is not None]
        avg_leak = sum(leak_values) / len(leak_values) if leak_values else None

        spo2_values = [d.spo2_mean for d in day_records if d.spo2_mean is not None]
        avg_spo2 = sum(spo2_values) / len(spo2_values) if spo2_values else None
        spo2_mins = [d.spo2_min for d in day_records if d.spo2_min is not None]
        min_spo2 = min(spo2_mins) if spo2_mins else None

        event_counts = self.db_session.execute(
            select(
                models.Event.event_type,
                func.count(models.Event.id).label("count"),
            )
            .join(models.Session)
            .join(models.Day)
            .where(models.Day.id.in_(day_ids))
            .group_by(models.Event.event_type)
            .order_by(text("count DESC"))
        ).all()

        total_events = sum(count for _, count in event_counts)
        event_type_counts = [
            EventTypeCount(
                event_type=event_type,
                count=count,
                percentage=(count / total_events * 100) if total_events > 0 else 0,
            )
            for event_type, count in event_counts
        ]

        stats_records = (
            self.db_session.execute(
                select(models.Statistics)
                .join(models.Session)
                .join(models.Day)
                .where(models.Day.id.in_(day_ids))
            )
            .scalars()
            .all()
        )

        weighted_sums: dict[str, float] = {
            "rr": 0.0,
            "tv": 0.0,
            "mv": 0.0,
            "pulse": 0.0,
            "rei": 0.0,
            "epap": 0.0,
        }
        usage_hours_for: dict[str, float] = {
            "rr": 0.0,
            "tv": 0.0,
            "mv": 0.0,
            "pulse": 0.0,
            "rei": 0.0,
            "epap": 0.0,
        }
        total_spo2_time_below_90 = 0

        for stat in stats_records:
            if not stat.usage_hours or stat.usage_hours <= 0:
                continue
            hours = stat.usage_hours
            for field, key in [
                ("respiratory_rate_mean", "rr"),
                ("tidal_volume_mean", "tv"),
                ("minute_ventilation_mean", "mv"),
                ("pulse_mean", "pulse"),
                ("rei", "rei"),
                ("epap_mean", "epap"),
            ]:
                val = getattr(stat, field)
                if val is not None:
                    weighted_sums[key] += val * hours
                    usage_hours_for[key] += hours
            if stat.spo2_time_below_90 is not None:
                total_spo2_time_below_90 += stat.spo2_time_below_90

        def _avg(key: str) -> float | None:
            return (
                weighted_sums[key] / usage_hours_for[key]
                if usage_hours_for[key] > 0
                else None
            )

        return TherapySummary(
            first_date=first_date,
            last_date=last_date,
            days_since_last=days_since_last,
            total_hours=total_hours,
            avg_hours=avg_hours,
            days_with_data=days_with_data,
            avg_ahi=avg_ahi,
            effectiveness=effectiveness,
            avg_rei=_avg("rei"),
            avg_pressure=avg_pressure,
            min_pressure=min_pressure,
            max_pressure=max_pressure,
            avg_epap=_avg("epap"),
            avg_leak=avg_leak,
            avg_spo2=avg_spo2,
            min_spo2=min_spo2,
            total_spo2_time_below_90=total_spo2_time_below_90,
            avg_pulse=_avg("pulse"),
            avg_respiratory_rate=_avg("rr"),
            avg_tidal_volume=_avg("tv"),
            avg_minute_ventilation=_avg("mv"),
            ahi_trend_direction=ahi_trend_direction,
            event_counts=event_type_counts,
        )

    def get_period_statistics(
        self,
        period_type: PeriodType,
        days_limit: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[PeriodStatistics]:
        """
        Calculate statistics grouped by time periods.

        Args:
            period_type: One of 'day', 'week', 'month', '6month', 'year'
            days_limit: Only include days within the last N days from today
            from_date: Only include days on or after this date
            to_date: Only include days on or before this date

        Returns:
            List of PeriodStatistics for each period
        """
        day_records = self._query_days(days_limit, from_date=from_date, to_date=to_date)
        return calculate_period_statistics(day_records, period_type)

    def _aggregate_session_stats_per_period(
        self,
        day_records: list[models.Day],
        period_stats: list[PeriodStatistics],
    ) -> dict[date, dict[str, float | None]]:
        """
        Compute usage-weighted session-level means per period.

        Issues one Statistics query joined through Session→Day, then buckets each
        row into its period using the passed period_stats boundaries.  The returned
        dict is keyed by period_start and contains sub-keys epap, rr, pulse, mv.

        Args:
            day_records: Day records already queried for this request
            period_stats: Period statistics whose period_start values become dict keys

        Returns:
            Dict mapping period_start → {epap, rr, pulse, mv} (None when no data)
        """
        if not day_records or not period_stats:
            return {
                ps.period_start: {"epap": None, "rr": None, "pulse": None, "mv": None}
                for ps in period_stats
            }

        # Sort period boundaries for O(log P) lookup per Statistics row.
        sorted_starts = sorted(ps.period_start for ps in period_stats)
        period_end_by_start: dict[date, date] = {
            ps.period_start: ps.period_end for ps in period_stats
        }

        day_ids = [d.id for d in day_records]
        rows = self.db_session.execute(
            select(models.Statistics, models.Day.date)
            .join(models.Session, models.Statistics.session_id == models.Session.id)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(models.Day.id.in_(day_ids))
        ).all()

        _KEYS = ["epap", "rr", "pulse", "mv"]
        _FIELD_MAP = {
            "epap": "epap_mean",
            "rr": "respiratory_rate_mean",
            "pulse": "pulse_mean",
            "mv": "minute_ventilation_mean",
        }

        weighted_sums: dict[date, dict[str, float]] = {}
        total_hours: dict[date, dict[str, float]] = {}

        for stat, day_date in rows:
            if not stat.usage_hours or stat.usage_hours <= 0:
                continue

            idx = bisect_right(sorted_starts, day_date) - 1
            if idx < 0:
                continue
            period_start = sorted_starts[idx]
            if day_date > period_end_by_start[period_start]:
                continue

            if period_start not in weighted_sums:
                weighted_sums[period_start] = {k: 0.0 for k in _KEYS}
                total_hours[period_start] = {k: 0.0 for k in _KEYS}

            hours = stat.usage_hours
            for key in _KEYS:
                val: float | None = getattr(stat, _FIELD_MAP[key])
                if val is not None:
                    weighted_sums[period_start][key] += val * hours
                    total_hours[period_start][key] += hours

        result: dict[date, dict[str, float | None]] = {}
        for ps in period_stats:
            pstart = ps.period_start
            if pstart not in weighted_sums:
                result[pstart] = {k: None for k in _KEYS}
            else:
                result[pstart] = {
                    k: (
                        weighted_sums[pstart][k] / total_hours[pstart][k]
                        if total_hours[pstart][k] > 0
                        else None
                    )
                    for k in _KEYS
                }

        return result

    def get_trends(
        self,
        period_type: PeriodType,
        days_limit: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, list[tuple[date, float | None]]]:
        """
        Compute extended trend data for the requested period granularity.

        Args:
            period_type: One of 'day', 'week', 'month', '6month', 'year'
            days_limit: Only include days within the last N days from today
            from_date: Only include days on or after this date
            to_date: Only include days on or before this date

        Returns:
            Dictionary with 13 metric keys, each a list of (date, value) tuples:
            ahi, usage, spo2, leak, pressure, oai, cai, hi, rera, epap, rr, pulse, mv
        """
        day_records = self._query_days(days_limit, from_date=from_date, to_date=to_date)
        period_stats = calculate_period_statistics(day_records, period_type)
        session_extras = self._aggregate_session_stats_per_period(
            day_records, period_stats
        )
        return calculate_trends_extended(period_stats, session_extras)

    def get_records(
        self, days_limit: int | None = None, top_n: int = 5
    ) -> dict[str, dict[str, list[tuple[date, float]]]]:
        """
        Calculate top best/worst days for key metrics.

        Args:
            days_limit: Only include days within the last N days from today
            top_n: Number of records to return

        Returns:
            Dictionary mapping metric names to best/worst records
        """
        day_records = self._query_days(days_limit)
        return calculate_records(day_records, top_n)
