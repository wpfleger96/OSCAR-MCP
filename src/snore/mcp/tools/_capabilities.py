"""Shared device capabilities helper for MCP tools.

Provides ``build_device_capabilities`` — a profile-scoped async function that
computes analysis status, calls BreathService, and maps the result to the MCP
``DeviceCapabilities`` schema.  Centralised here so summary, events, and
settings tools share identical mapping logic.

Provides ``_has_analysis`` — module-private helper that runs the
profile-scoped analysis-count query exactly once and returns a bool.
overview, summary, and settings import it to pre-compute the flag before
calling ``build_device_capabilities``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.mcp.schemas import DeviceCapabilities


async def get_device_id_for_session(
    db_session: AsyncSession, session_id: int, profile_id: int
) -> int | None:
    """Return the device_id for *session_id*, scoped to *profile_id*.

    Executes a single profile-scoped join (Session → Device) to resolve the
    device without leaking sessions from other profiles.  Returns None when the
    session does not exist or is not owned by this profile.
    """
    result = await db_session.execute(
        select(models.Session.device_id)
        .join(models.Device, models.Device.id == models.Session.device_id)
        .where(
            models.Session.id == session_id,
            models.Device.profile_id == profile_id,
        )
    )
    return result.scalar_one_or_none()


async def _has_analysis(db_session: AsyncSession, profile_id: int) -> bool:
    """Return True when at least one AnalysisResult exists for this profile.

    Counts distinct session_ids with an AnalysisResult, scoped to this profile
    via Session → Device.  Used as a pre-compute flag to avoid re-running the
    three-join query once per device in callers that loop over devices.
    """
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
    return count > 0


async def build_device_capabilities(
    db_session: AsyncSession,
    profile_id: int,
    device_id: int,
    date_start: date | None = None,
    date_end: date | None = None,
    analysis_run: bool | None = None,
) -> DeviceCapabilities | None:
    """Return MCP DeviceCapabilities for *device_id*, scoped to *profile_id*.

    Returns None when the device is not owned by the profile.  Ownership is
    determined by the service DTO's identity fields: identity-None ⇒ not owned.
    Exceptions from BreathService propagate to the caller (no swallowing).

    When *analysis_run* is None it is computed via ``_has_analysis``.  Callers
    that loop over multiple devices should pre-compute it once with
    ``_has_analysis`` and pass it in to avoid repeated round-trips.
    """
    from snore.services.breath_service import BreathService  # noqa: PLC0415

    if analysis_run is None:
        analysis_run = await _has_analysis(db_session, profile_id)

    bs_caps = await BreathService(db_session, profile_id).get_device_capabilities(
        device_id, date_start=date_start, date_end=date_end
    )

    # Identity-None means this device is not owned by the requesting profile.
    if bs_caps.manufacturer is None:
        return None

    channels: set[str] = set(bs_caps.channels_present or [])
    has_pressure = bool({"pressure", "therapy_pressure", "epap", "ipap"} & channels)
    null_reason = bs_caps.null_reason

    return DeviceCapabilities(
        manufacturer=bs_caps.manufacturer,
        model=bs_caps.model,
        serial_number=bs_caps.serial_number,
        has_flow_waveform="flow" in channels,
        has_pressure_waveform=has_pressure,
        has_leak_waveform="leak" in channels,
        has_spo2="spo2" in channels,
        has_events=bool(bs_caps.event_types_present),
        has_analysis=analysis_run,
        notes=[str(null_reason)] if null_reason is not None else [],
    )
