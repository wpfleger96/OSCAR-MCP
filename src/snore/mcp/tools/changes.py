"""get_settings_changes tool — merged device-settings + mask-log change log."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from fastmcp import Context

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from snore.services.schemas import MaskLogEntryResponse
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.rx_tracker import RxTracker
from snore.mcp.schemas import SettingsChangeEntry, SettingsChangesResponse
from snore.mcp.tools._scaffold import _scope_and_run, tool_error_boundary
from snore.services.mask_log_service import MaskLogService


def _describe_mask(entry: MaskLogEntryResponse) -> str:
    """Return "Brand Model (size)", omitting the parens when size is null."""
    desc = f"{entry.brand} {entry.model}"
    return f"{desc} ({entry.size})" if entry.size else desc


async def get_settings_changes(
    db_session: AsyncSession,
    start: date,
    end: date,
    profile_id: int,
    device_id: int | None = None,
) -> SettingsChangesResponse:
    """Return a merged, source-tagged log of settings changes in [start, end].

    Combines RxTracker.get_changes (per-key device settings diffs across ALL
    persisted keys) with the profile's user-entered mask equipment log
    (key="mask_equipment").  Mask log entries are profile-level, so the
    optional device_id filter narrows only the device_settings entries.
    """
    tracker = RxTracker(profile_id)
    device_changes = (await tracker.get_changes(db_session)).changes
    entries = [
        SettingsChangeEntry(
            date=c.date,
            source="device_settings",
            device_id=c.device_id,
            device_name=c.device_name,
            key=c.key,
            old_value=c.old_value,
            new_value=c.new_value,
        )
        for c in device_changes
        if start <= c.date <= end and (device_id is None or c.device_id == device_id)
    ]

    # Full ordered log (start_date, id) so the first in-range entry's old_value
    # can come from the latest entry before the range.
    mask_entries = await MaskLogService(db_session, profile_id).list_entries()
    prev_desc: str | None = None
    for entry in mask_entries:
        desc = _describe_mask(entry)
        if start <= entry.start_date <= end:
            entries.append(
                SettingsChangeEntry(
                    date=entry.start_date,
                    source="mask_log",
                    key="mask_equipment",
                    old_value=prev_desc,
                    new_value=desc,
                    mask_brand=entry.brand,
                    mask_model=entry.model,
                    mask_size=entry.size,
                    mask_style=entry.style,
                    notes=entry.notes,
                )
            )
        prev_desc = desc

    entries.sort(
        key=lambda e: (
            e.date,
            e.source,
            e.device_id if e.device_id is not None else -1,
            e.key,
        )
    )
    return SettingsChangesResponse(changes=entries, total_changes=len(entries))


def register(mcp: FastMCP) -> None:
    from snore.mcp.validation import parse_date_range  # noqa: PLC0415

    @mcp.tool()
    @tool_error_boundary
    async def get_settings_changes(
        ctx: Context,
        start: str,
        end: str,
        device_id: int | None = None,
    ) -> dict[str, Any]:
        """Return a merged, source-tagged log of every settings change in a date range.

        Combines two sources into one date-ordered list:
        - ``source="device_settings"``: per-key changes read from the device's
          stored settings, diffed day-over-day per device across ALL persisted
          keys — including comfort settings such as mask_type and
          humidity_level.
        - ``source="mask_log"``: user-logged mask equipment changes
          (``key="mask_equipment"``) with mask_brand/mask_model/mask_size/
          mask_style detail fields and optional notes.  ``old_value`` is the
          previously logged mask, or ``null`` for the first entry ever logged.

        Mask log entries are profile-level, not device-level: they are ALWAYS
        included even when ``device_id`` is passed — the filter narrows only
        the device_settings entries.

        Args:
            start: Start date in YYYY-MM-DD format.
            end: End date in YYYY-MM-DD format.
            device_id: Optional device ID filter for device_settings entries.
                Use get_data_overview to list devices.

        Returns:
            SettingsChangesResponse with changes sorted by
            (date, source, device_id, key) and a total_changes count.
        """
        from snore.mcp.tools.changes import (
            get_settings_changes as _impl,  # noqa: PLC0415
        )

        start_d, end_d = parse_date_range(start, end)
        return await _scope_and_run(
            ctx,
            _impl,
            tool_name="get_settings_changes",
            start=start_d,
            end=end_d,
            device_id=device_id,
        )
