"""get_settings_timeline tool — RxTracker adapter."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.rx_tracker import RX_KEYS, RxTracker
from snore.mcp.schemas import SettingsEpoch, SettingsTimelineResponse


async def get_settings_timeline(
    db_session: AsyncSession,
    start: date,
    end: date,
    device_id: int | None = None,
) -> SettingsTimelineResponse:
    """Return therapy settings epochs in [start, end].

    Each epoch covers a contiguous period of identical settings.  Changed keys
    are flagged on the epoch where the change first appears.  Uses only the
    generic RX_KEYS; no vendor-specific branching (G4).
    """
    tracker = RxTracker()
    all_periods = await tracker.get_history(db_session)

    # Filter to requested date range and optional device
    filtered = [
        p
        for p in all_periods
        if p.end_date >= start
        and p.start_date <= end
        and (device_id is None or p.device_id == device_id)
    ]
    filtered.sort(key=lambda p: (p.device_id or 0, p.start_date))

    epochs: list[SettingsEpoch] = []
    prev_settings: dict[int, dict[str, str | None]] = {}

    for period in filtered:
        dev_id = period.device_id or 0
        raw = period.settings

        # Restrict to generic RX_KEYS; absent keys become None
        settings: dict[str, str | None] = {k: raw.get(k) for k in RX_KEYS}

        # Determine which keys changed vs. previous epoch for this device
        prev = prev_settings.get(dev_id, {})
        changed_keys = [k for k in settings if settings[k] != prev.get(k)]
        prev_settings[dev_id] = settings

        # Clip epoch boundaries to requested range
        epoch_start = max(period.start_date, start)
        epoch_end = min(period.end_date, end)
        nights = (epoch_end - epoch_start).days + 1

        epochs.append(
            SettingsEpoch(
                start_date=epoch_start,
                end_date=epoch_end,
                nights=nights,
                settings=settings,
                changed_keys=changed_keys if prev else [],
                device_id=dev_id,
            )
        )

    return SettingsTimelineResponse(epochs=epochs, total_epochs=len(epochs))
