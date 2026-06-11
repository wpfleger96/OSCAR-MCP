"""RX (prescription) change tracking and period analysis."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session, joinedload

from snore.database.models import Day
from snore.database.models import Session as SessionModel
from snore.services.schemas import RxComparisonResponse, RxPeriodResponse

RX_KEYS = (
    "mode",
    "epr_level",
    "epr_mode",
    "pressure_min",
    "pressure_max",
    "pressure_fixed",
)


@dataclass
class RxPeriod:
    """Therapy prescription period with consistent settings."""

    settings: dict[str, str]
    start_date: date
    end_date: date
    days: list[Day]


@dataclass
class RxPeriodStats(RxPeriod):
    """RX period with computed statistics."""

    avg_ahi: float | None = None
    median_ahi: float | None = None
    avg_hours: float | None = None
    total_hours: float = 0.0
    avg_leak: float | None = None


class RxTracker:
    """Track and analyze therapy prescription changes."""

    def get_history(self, db_session: Session) -> list[RxPeriodResponse]:
        """Return all RX periods with stats."""
        periods = self._compute_periods(db_session)
        stats = self._compute_period_stats(periods)
        return [self._to_response(p) for p in stats]

    def get_current(self, db_session: Session) -> RxPeriodResponse | None:
        """Return the current (most recent) RX period, or None if no data."""
        history = self.get_history(db_session)
        return history[-1] if history else None

    def get_comparison(
        self, db_session: Session, min_days: int = 7
    ) -> RxComparisonResponse:
        """Return all periods with best/worst indices."""
        periods = self._compute_periods(db_session)
        stats = self._compute_period_stats(periods)
        best, worst = self._best_worst(stats, min_days)
        responses = [self._to_response(p) for p in stats]
        best_index = stats.index(best) if best is not None else None
        worst_index = stats.index(worst) if worst is not None else None
        return RxComparisonResponse(
            periods=responses, best_index=best_index, worst_index=worst_index
        )

    def _to_response(self, period: RxPeriodStats) -> RxPeriodResponse:
        """Convert RxPeriodStats dataclass to RxPeriodResponse Pydantic model."""
        return RxPeriodResponse(
            settings=period.settings,
            start_date=period.start_date,
            end_date=period.end_date,
            days_count=len(period.days),
            avg_ahi=period.avg_ahi,
            median_ahi=period.median_ahi,
            avg_hours=period.avg_hours,
            total_hours=period.total_hours,
            avg_leak=period.avg_leak,
        )

    def _compute_periods(self, db_session: Session) -> list[RxPeriod]:
        """
        Group consecutive days by therapy settings into RX periods.

        Algorithm:
        1. Query sessions ordered by start_time, join with settings
        2. For each day, extract RX-defining keys from the longest session's settings
        3. Build fingerprint from sorted key-value pairs
        4. Group consecutive days with same fingerprint into RxPeriod

        Returns:
            List of RxPeriod objects in chronological order.
        """
        days_query = (
            db_session.query(Day)
            .order_by(Day.date)
            .options(joinedload(Day.sessions).joinedload(SessionModel.settings))
        )

        days = days_query.all()

        if not days:
            return []

        periods: list[RxPeriod] = []
        current_settings: dict[str, str] | None = None
        current_fingerprint: str | None = None
        current_period_days: list[Day] = []
        current_start_date: date | None = None

        for day in days:
            day_settings = self._get_day_rx_settings(day)

            if day_settings is None:
                continue

            fingerprint = self._build_fingerprint(day_settings)

            if fingerprint != current_fingerprint:
                if current_period_days and current_start_date and current_settings:
                    periods.append(
                        RxPeriod(
                            settings=current_settings,
                            start_date=current_start_date,
                            end_date=current_period_days[-1].date,
                            days=current_period_days,
                        )
                    )

                current_settings = day_settings
                current_fingerprint = fingerprint
                current_period_days = [day]
                current_start_date = day.date
            else:
                current_period_days.append(day)

        if current_period_days and current_start_date and current_settings:
            periods.append(
                RxPeriod(
                    settings=current_settings,
                    start_date=current_start_date,
                    end_date=current_period_days[-1].date,
                    days=current_period_days,
                )
            )

        return periods

    def _compute_period_stats(self, periods: list[RxPeriod]) -> list[RxPeriodStats]:
        """
        Compute statistics for each RX period.

        Returns:
            List of RxPeriodStats with computed metrics.
        """
        stats_periods: list[RxPeriodStats] = []

        for period in periods:
            valid_ahi_days = [d for d in period.days if d.ahi is not None]
            valid_leak_days = [d for d in period.days if d.leak_median is not None]

            avg_ahi = None
            median_ahi = None
            if valid_ahi_days:
                ahi_values = sorted(
                    [d.ahi for d in valid_ahi_days if d.ahi is not None]
                )
                avg_ahi = sum(ahi_values) / len(ahi_values)
                mid = len(ahi_values) // 2
                if len(ahi_values) % 2 == 0:
                    median_ahi = (ahi_values[mid - 1] + ahi_values[mid]) / 2
                else:
                    median_ahi = ahi_values[mid]

            avg_leak = None
            if valid_leak_days:
                leak_values = [
                    d.leak_median for d in valid_leak_days if d.leak_median is not None
                ]
                avg_leak = sum(leak_values) / len(leak_values)

            total_hours = sum(d.total_therapy_hours or 0 for d in period.days)
            avg_hours = total_hours / len(period.days) if period.days else None

            stats_periods.append(
                RxPeriodStats(
                    settings=period.settings,
                    start_date=period.start_date,
                    end_date=period.end_date,
                    days=period.days,
                    avg_ahi=avg_ahi,
                    median_ahi=median_ahi,
                    avg_hours=avg_hours,
                    total_hours=total_hours,
                    avg_leak=avg_leak,
                )
            )

        return stats_periods

    def _best_worst(
        self, periods: list[RxPeriodStats], min_days: int = 7
    ) -> tuple[RxPeriodStats | None, RxPeriodStats | None]:
        """
        Identify best and worst RX periods by average AHI.

        Args:
            periods: List of RX periods with statistics
            min_days: Minimum number of days for a period to be considered

        Returns:
            Tuple of (best_period, worst_period), either may be None
        """
        eligible_periods = [
            p for p in periods if len(p.days) >= min_days and p.avg_ahi is not None
        ]

        if not eligible_periods:
            return (None, None)

        best = min(
            eligible_periods,
            key=lambda p: p.avg_ahi if p.avg_ahi is not None else float("inf"),
        )
        worst = max(
            eligible_periods,
            key=lambda p: p.avg_ahi if p.avg_ahi is not None else float("-inf"),
        )

        return (best, worst)

    def _get_day_rx_settings(self, day: Day) -> dict[str, str] | None:
        """Extract RX-defining settings from the longest session of a day."""
        if not day.sessions:
            return None

        longest_session = max(
            day.sessions,
            key=lambda s: s.duration_seconds if s.duration_seconds else 0.0,
        )

        if not longest_session.settings:
            return None

        settings_dict: dict[str, str] = {}
        for setting in longest_session.settings:
            if setting.key in RX_KEYS and setting.value is not None:
                settings_dict[setting.key] = setting.value

        return settings_dict if settings_dict else None

    def _build_fingerprint(self, settings: dict[str, str]) -> str:
        """Build a fingerprint string from RX settings for comparison."""
        sorted_items = sorted(settings.items())
        return "|".join(f"{k}={v}" for k, v in sorted_items)
