"""Shared device capabilities helper for MCP tools.

Provides ``build_device_capabilities`` — a profile-scoped async function that
fetches device ownership, computes analysis status, calls BreathService, and
maps the result to the MCP ``DeviceCapabilities`` schema.  Centralised here so
summary, events, and settings tools share identical mapping logic.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.mcp.schemas import DeviceCapabilities


async def build_device_capabilities(
    db_session: AsyncSession,
    profile_id: int,
    device_id: int,
    date_start: date | None = None,
    date_end: date | None = None,
    analysis_run: bool | None = None,
) -> DeviceCapabilities | None:
    """Return MCP DeviceCapabilities for *device_id*, scoped to *profile_id*.

    Returns None when the device does not exist or is not owned by the profile.
    Exceptions from BreathService propagate to the caller (no swallowing).

    When *analysis_run* is None it is computed profile-scoped: the result is
    True when at least one AnalysisResult exists on a session belonging to a
    device in this profile.
    """
    from snore.services.breath_service import BreathService  # noqa: PLC0415

    # Ownership gate — return None for unowned / missing devices
    device_row = (
        await db_session.execute(
            select(models.Device).where(
                models.Device.id == device_id,
                models.Device.profile_id == profile_id,
            )
        )
    ).scalar_one_or_none()

    if device_row is None:
        return None

    # Compute analysis status profile-scoped when caller did not pre-compute it
    if analysis_run is None:
        count = (
            await db_session.execute(
                select(func.count(models.AnalysisResult.session_id.distinct()))
                .join(
                    models.Session,
                    models.AnalysisResult.session_id == models.Session.id,
                )
                .join(
                    models.Device,
                    models.Session.device_id == models.Device.id,
                )
                .where(
                    models.Device.profile_id == profile_id,
                    models.Session.enabled.is_(True),
                )
            )
        ).scalar_one()
        analysis_run = count > 0

    bs_caps = await BreathService(db_session, profile_id).get_device_capabilities(
        device_id, date_start=date_start, date_end=date_end
    )

    channels: set[str] = set(bs_caps.channels_present or [])
    has_pressure = bool({"pressure", "therapy_pressure", "epap", "ipap"} & channels)
    null_reason = bs_caps.null_reason

    return DeviceCapabilities(
        manufacturer=device_row.manufacturer,
        model=device_row.model,
        serial_number=device_row.serial_number,
        has_flow_waveform="flow" in channels,
        has_pressure_waveform=has_pressure,
        has_leak_waveform="leak" in channels,
        has_spo2="spo2" in channels,
        has_events=bool(bs_caps.event_types_present),
        has_analysis=analysis_run,
        notes=[str(null_reason)] if null_reason is not None else [],
    )
