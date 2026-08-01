"""RX (prescription) change tracking and period analysis."""

import itertools
import logging

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from snore.database.models import Day, Device
from snore.database.models import Session as SessionModel
from snore.services.schemas import (
    RxAllResponse,
    RxChangesResponse,
    RxComparisonResponse,
    RxPeriodResponse,
    RxSettingChange,
)

logger = logging.getLogger(__name__)

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

_TAIL_WALK_BATCH_SIZE = 90


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

    async def get_history(self, db_session: AsyncSession) -> list[RxPeriodResponse]:
        """Return all RX periods with stats."""
        periods = await self._compute_periods(db_session)
        stats = self._compute_period_stats(periods)
        return [self._to_response(p) for p in stats]

    async def get_current(self, db_session: AsyncSession) -> RxPeriodResponse | None:
        """Return the current (most recent) RX period, or None if no data.

        Invariant: for any DB state, result equals get_history()[-1] when
        non-empty — the period with max (start_date, device_id) across all
        devices.  Empty DB / no periods → None.
        """
        candidates: list[RxPeriod] = []
        for device_id, device_name in await self._get_devices(db_session):
            period = await self._find_last_period_for_device(
                db_session, device_id, device_name
            )
            if period is not None:
                candidates.append(period)

        if not candidates:
            return None

        best = max(candidates, key=lambda p: (p.start_date, p.device_id))
        stats = self._compute_period_stats([best])[0]
        return self._to_response(stats)

    async def get_comparison(
        self, db_session: AsyncSession, min_days: int = 7
    ) -> RxComparisonResponse:
        """Return all periods with best/worst indices."""
        periods = await self._compute_periods(db_session)
        stats = self._compute_period_stats(periods)
        best_index, worst_index = self._best_worst_indices(stats, min_days)
        return RxComparisonResponse(
            periods=[self._to_response(p) for p in stats],
            best_index=best_index,
            worst_index=worst_index,
        )

    async def get_changes(self, db_session: AsyncSession) -> RxChangesResponse:
        """Return a log of every per-key settings change across all devices.

        Intentionally diffs ALL persisted settings keys — including comfort
        settings such as mask_type and humidity_level — not just the
        prescription-defining RX_KEYS.  This gives a complete audit trail of
        every setting the clinician or patient touched.  Do not narrow this to
        _get_day_rx_settings; use _get_day_settings (no key filter).
        """
        return self._compute_changes(await self._days_by_device(db_session))

    async def get_all(self, db_session: AsyncSession, min_days: int = 7) -> RxAllResponse:
        """Return all RX data from a single database query."""
        device_groups = await self._days_by_device(db_session)

        periods = self._compute_periods_from_groups(device_groups)
        stats = self._compute_period_stats(periods)
        history = [self._to_response(p) for p in stats]

        best_index, worst_index = self._best_worst_indices(stats, min_days)

        return RxAllResponse(
            history=history,
            current=history[-1] if history else None,
            best_index=best_index,
            worst_index=worst_index,
            changes=self._compute_changes(device_groups),
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
            device_id=period.device_id,
            device_name=period.device_name,
        )

    async def _compute_periods(self, db_session: AsyncSession) -> list[RxPeriod]:
        """Query all days and group into RX periods."""
        return self._compute_periods_from_groups(await self._days_by_device(db_session))

    def _compute_periods_from_groups(
        self, device_groups: list[tuple[int, str, list[Day]]]
    ) -> list[RxPeriod]:
        """Group consecutive days per device by therapy settings into RX periods."""
        all_periods: list[RxPeriod] = []
        for device_id, device_name, device_days in device_groups:
            all_periods.extend(
                self._compute_device_periods(device_days, device_id, device_name)
            )
        all_periods.sort(key=lambda p: (p.start_date, p.device_id))
        return all_periods

    def _compute_changes(
        self, device_groups: list[tuple[int, str, list[Day]]]
    ) -> RxChangesResponse:
        """Diff ALL settings keys day-over-day per device."""
        all_changes: list[RxSettingChange] = []
        for device_id, device_name, device_days in device_groups:
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

    async def _days_by_device(self, db_session: AsyncSession) -> list[tuple[int, str, list[Day]]]:
        """Query all days grouped by device, ordered by (device_id, date).

        Returns a list of (device_id, device_name, days) tuples, one per
        device, preserving the query order.  Devices whose Day rows have no
        matching Device row (device is None) are skipped with a warning.
        """
        days = (
            (await db_session.execute(
                select(Day)
                .order_by(Day.device_id, Day.date)
                .options(
                    joinedload(Day.sessions).joinedload(SessionModel.settings),
                    joinedload(Day.device),
                )
            ))
            .unique()
            .scalars()
            .all()
        )

        result: list[tuple[int, str, list[Day]]] = []
        for device_id, device_days_iter in itertools.groupby(
            days, key=lambda d: d.device_id
        ):
            device_days = list(device_days_iter)
            device = device_days[0].device
            if device is None:
                logger.warning(
                    "Skipping device_id=%s: no matching Device row", device_id
                )
                continue
            device_name = f"{device.manufacturer} {device.model}"
            result.append((device_id, device_name, device_days))

        return result

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
                ahi_values = sorted(d.ahi for d in valid_ahi_days if d.ahi is not None)
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

    def _best_worst_indices(
        self, periods: list[RxPeriodStats], min_days: int = 7
    ) -> tuple[int | None, int | None]:
        """Return (best_index, worst_index) by average AHI among eligible periods."""
        eligible = [
            (i, p)
            for i, p in enumerate(periods)
            if len(p.days) >= min_days and p.avg_ahi is not None
        ]
        if not eligible:
            return (None, None)

        best_idx = min(eligible, key=lambda t: t[1].avg_ahi or float("inf"))[0]
        worst_idx = max(eligible, key=lambda t: t[1].avg_ahi or float("-inf"))[0]
        return (best_idx, worst_idx)

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

    async def _get_devices(self, db_session: AsyncSession) -> list[tuple[int, str]]:
        """Return (device_id, device_name) for all devices that have Day rows.

        Uses an inner join so Day rows with no matching Device are naturally
        excluded — same outcome as the missing-device guard in _days_by_device.
        """
        rows = (await db_session.execute(
            select(Day.device_id, Device.manufacturer, Device.model)
            .join(Device, Day.device_id == Device.id)
            .distinct()
        )).all()
        return [
            (
                device_id,
                f"{manufacturer} {model}",
            )  # name format must match _days_by_device
            for device_id, manufacturer, model in rows
        ]

    async def _query_device_days_desc(
        self,
        db_session: AsyncSession,
        device_id: int,
        *,
        before: date | None,
        limit: int,
    ) -> list[Day]:
        """Return up to `limit` days for `device_id` ordered newest-first.

        If `before` is given, only days strictly older than that date are
        included (keyset pagination cursor).
        """
        q = select(Day).where(Day.device_id == device_id).order_by(Day.date.desc())
        if before is not None:
            q = q.where(Day.date < before)
        return list(
            (await db_session.execute(
                q.options(
                    selectinload(Day.sessions).selectinload(SessionModel.settings),
                ).limit(limit)
            ))
            .unique()
            .scalars()
            .all()
        )

    async def _find_last_period_for_device(
        self,
        db_session: AsyncSession,
        device_id: int,
        device_name: str,
    ) -> RxPeriod | None:
        """Walk batches newest→oldest to find the last RX period for a device.

        Returns the most recent contiguous run of days sharing the same RX
        fingerprint, skipping days with no valid settings.  Stops as soon as
        a day with a different fingerprint (a period boundary) is encountered.
        If all batches exhaust without finding a boundary, the entire device
        history is one period.
        """
        current_fingerprint: str | None = None
        current_settings: dict[str, str] | None = None
        collected_days: list[Day] = []  # accumulated newest-first
        cursor: date | None = None
        done = False

        while not done:
            batch = await self._query_device_days_desc(
                db_session, device_id, before=cursor, limit=_TAIL_WALK_BATCH_SIZE
            )
            if not batch:
                break

            for day in batch:
                day_settings = self._get_day_rx_settings(day)
                if day_settings is None:
                    continue

                fingerprint = self._build_fingerprint(day_settings)

                if current_fingerprint is None:
                    current_fingerprint = fingerprint
                    current_settings = day_settings

                if fingerprint != current_fingerprint:
                    done = True
                    break

                collected_days.append(day)

            if not done:
                cursor = batch[-1].date

        if not collected_days or current_settings is None:
            return None

        collected_days.reverse()
        return RxPeriod(
            settings=current_settings,
            start_date=collected_days[0].date,
            end_date=collected_days[-1].date,
            days=collected_days,
            device_id=device_id,
            device_name=device_name,
        )
