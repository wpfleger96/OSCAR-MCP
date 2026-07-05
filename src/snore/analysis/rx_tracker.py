"""RX (prescription) change tracking and period analysis."""

import itertools

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session, joinedload

from snore.database.models import Day
from snore.database.models import Session as SessionModel
from snore.services.schemas import (
    RxChangesResponse,
    RxComparisonResponse,
    RxPeriodResponse,
    RxSettingChange,
)

RX_KEYS = (
    "mode",
    "epr_level",
    "epr_mode",
    "pressure_min",
    "pressure_max",
    "pressure_fixed",
    "ipap",
    "epap",
    "ps",
)


def _diff_settings(
    prev: dict[str, str], curr: dict[str, str]
) -> list[tuple[str, str | None, str | None]]:
    """Return (key, old_value, new_value) for every key whose value changed."""
    all_keys = sorted(set(prev) | set(curr))
    return [
        (k, prev.get(k), curr.get(k)) for k in all_keys if prev.get(k) != curr.get(k)
    ]


@dataclass
class RxPeriod:
    """Therapy prescription period with consistent settings."""

    settings: dict[str, str]
    start_date: date
    end_date: date
    days: list[Day]
    device_id: int
    device_name: str


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

    def get_changes(self, db_session: Session) -> RxChangesResponse:
        """Return a log of every per-key settings change across all devices."""
        days = (
            db_session.query(Day)
            .order_by(Day.device_id, Day.date)
            .options(
                joinedload(Day.sessions).joinedload(SessionModel.settings),
                joinedload(Day.device),
            )
            .all()
        )

        all_changes: list[RxSettingChange] = []

        for device_id, device_days_iter in itertools.groupby(
            days, key=lambda d: d.device_id
        ):
            device_days = list(device_days_iter)
            device = device_days[0].device
            device_name = f"{device.manufacturer} {device.model}"

            prev_settings: dict[str, str] | None = None
            for day in device_days:
                curr_settings = self._get_day_settings(day)
                if curr_settings is None:
                    continue
                if prev_settings is not None:
                    for key, old_val, new_val in _diff_settings(
                        prev_settings, curr_settings
                    ):
                        all_changes.append(
                            RxSettingChange(
                                date=day.date,
                                device_id=device_id,
                                device_name=device_name,
                                key=key,
                                old_value=old_val,
                                new_value=new_val,
                            )
                        )
                prev_settings = curr_settings

        all_changes.sort(key=lambda c: (c.date, c.device_id, c.key))
        return RxChangesResponse(changes=all_changes)

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
            device_id=period.device_id,
            device_name=period.device_name,
        )

    def _compute_periods(self, db_session: Session) -> list[RxPeriod]:
        """
        Group consecutive days per device by therapy settings into RX periods.

        Algorithm:
        1. Query days ordered by (device_id, date), join sessions→settings and device
        2. Group by device_id with itertools.groupby
        3. For each device, run the fingerprint loop via _compute_device_periods
        4. Sort combined list by (start_date, device_id) for stable ordering
        """
        days = (
            db_session.query(Day)
            .order_by(Day.device_id, Day.date)
            .options(
                joinedload(Day.sessions).joinedload(SessionModel.settings),
                joinedload(Day.device),
            )
            .all()
        )

        if not days:
            return []

        all_periods: list[RxPeriod] = []
        for _device_id, device_days_iter in itertools.groupby(
            days, key=lambda d: d.device_id
        ):
            device_days = list(device_days_iter)
            device = device_days[0].device
            device_name = f"{device.manufacturer} {device.model}"
            all_periods.extend(
                self._compute_device_periods(
                    device_days, device_days[0].device_id, device_name
                )
            )

        all_periods.sort(key=lambda p: (p.start_date, p.device_id))
        return all_periods

    def _compute_device_periods(
        self, days: list[Day], device_id: int, device_name: str
    ) -> list[RxPeriod]:
        """Run the fingerprint-grouping loop for a single device's days in date order."""
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
                            device_id=device_id,
                            device_name=device_name,
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
                    device_id=device_id,
                    device_name=device_name,
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
                    device_id=period.device_id,
                    device_name=period.device_name,
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

    def _get_day_settings(
        self, day: Day, key_filter: tuple[str, ...] | None = None
    ) -> dict[str, str] | None:
        """Extract settings from the longest enabled session of a day.

        Args:
            day: The Day ORM object with sessions pre-loaded.
            key_filter: If given, only these keys are included in the result.
                        If None, all keys with non-null values are returned.
        """
        enabled_sessions = [s for s in day.sessions if s.enabled]
        if not enabled_sessions:
            return None

        longest_session = max(
            enabled_sessions,
            key=lambda s: s.duration_seconds if s.duration_seconds else 0.0,
        )

        if not longest_session.settings:
            return None

        settings_dict: dict[str, str] = {}
        for setting in longest_session.settings:
            if setting.value is not None:
                if key_filter is None or setting.key in key_filter:
                    settings_dict[setting.key] = setting.value

        return settings_dict if settings_dict else None

    def _get_day_rx_settings(self, day: Day) -> dict[str, str] | None:
        """Extract RX-defining settings from the longest enabled session of a day."""
        return self._get_day_settings(day, key_filter=RX_KEYS)

    def _build_fingerprint(self, settings: dict[str, str]) -> str:
        """Build a fingerprint string from RX settings for comparison."""
        sorted_items = sorted(settings.items())
        return "|".join(f"{k}={v}" for k, v in sorted_items)
