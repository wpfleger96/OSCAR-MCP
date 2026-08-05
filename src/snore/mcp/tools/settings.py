"""get_settings_timeline tool — RxTracker adapter."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.rx_tracker import RX_KEYS, RxTracker, changed_setting_keys
from snore.mcp.schemas import (
    DeviceCapabilities,
    SettingsEpoch,
    SettingsTimelineResponse,
)
from snore.mcp.tools._capabilities import _has_analysis, build_device_capabilities


async def get_settings_timeline(
    db_session: AsyncSession,
    start: date,
    end: date,
    profile_id: int,
    device_id: int | None = None,
) -> SettingsTimelineResponse:
    """Return therapy settings epochs in [start, end].

    Each epoch covers a contiguous period of identical settings.  Changed keys
    are flagged on the epoch where the change first appears.  Uses only the
    generic RX_KEYS; no vendor-specific branching (G4).

    Device capabilities are populated per distinct device_id across the filtered
    epochs, scoped through build_device_capabilities (profile-safe).
    """
    tracker = RxTracker(profile_id)
    all_periods = await tracker.get_history(db_session)

    # Filter to requested date range and optional device
    filtered = [
        p
        for p in all_periods
        if p.end_date >= start
        and p.start_date <= end
        and (device_id is None or p.device_id == device_id)
    ]
    filtered.sort(
        key=lambda p: (p.device_id if p.device_id is not None else -1, p.start_date)
    )

    # Pre-compute analysis status once to avoid re-running the three-join query
    # per device inside build_device_capabilities.
    analysis_run = await _has_analysis(db_session, profile_id)

    # Build capabilities once per real device id across the filtered epochs
    caps_cache: dict[int, DeviceCapabilities | None] = {}
    for period in filtered:
        dev_id = period.device_id
        if dev_id is not None and dev_id not in caps_cache:
            caps_cache[dev_id] = await build_device_capabilities(
                db_session,
                profile_id,
                dev_id,
                date_start=start,
                date_end=end,
                analysis_run=analysis_run,
            )

    epochs: list[SettingsEpoch] = []
    prev_settings: dict[int, dict[str, str | None]] = {}

    for period in filtered:
        dev_id = period.device_id
        raw = period.settings

        # Restrict to generic RX_KEYS; absent keys become None
        settings: dict[str, str | None] = {k: raw.get(k) for k in RX_KEYS}

        # Determine which keys changed vs. previous epoch for this device
        key = dev_id if dev_id is not None else -1
        prev = prev_settings.get(key, {})
        changed_keys = sorted(changed_setting_keys(prev, settings))
        prev_settings[key] = settings

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
                device_capabilities=caps_cache.get(dev_id)
                if dev_id is not None
                else None,
            )
        )

    return SettingsTimelineResponse(epochs=epochs, total_epochs=len(epochs))
