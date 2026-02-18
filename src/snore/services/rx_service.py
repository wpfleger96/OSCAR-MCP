"""RX therapy period service — bridges RxTracker dataclasses to Pydantic models."""

from sqlalchemy.orm import Session

from snore.analysis.rx_tracker import RxPeriodStats, RxTracker
from snore.services.schemas import RxComparisonResponse, RxPeriodResponse

__all__ = ["RxService"]


class RxService:
    def __init__(self, db_session: Session):
        self.db_session = db_session
        self._tracker = RxTracker()

    def get_history(self) -> list[RxPeriodResponse]:
        """Return all RX periods with stats."""
        periods = self._tracker.compute_periods(self.db_session)
        stats = self._tracker.compute_period_stats(periods)
        return [self._to_response(p) for p in stats]

    def get_current(self) -> RxPeriodResponse | None:
        """Return the current (most recent) RX period, or None if no data."""
        periods = self._tracker.compute_periods(self.db_session)
        if not periods:
            return None
        stats = self._tracker.compute_period_stats(periods)
        return self._to_response(stats[-1]) if stats else None

    def get_comparison(self, min_days: int = 7) -> RxComparisonResponse:
        """Return all periods with best/worst indices."""
        periods = self._tracker.compute_periods(self.db_session)
        stats = self._tracker.compute_period_stats(periods)
        best, worst = self._tracker.best_worst(stats, min_days)
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
