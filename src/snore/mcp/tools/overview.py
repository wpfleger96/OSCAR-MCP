"""get_data_overview tool — cold-start orientation."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.mcp.schemas import DataOverviewResponse, DeviceCapabilities, DeviceInfo
from snore.services.device_service import DeviceService


def _map_device_capabilities(
    bs_caps: Any,
    manufacturer: str,
    model_name: str,
    serial_number: str,
    analysis_run: bool,
) -> DeviceCapabilities:
    """Map BreathService.DeviceCapabilities → MCP DeviceCapabilities."""
    channels: set[str] = set(bs_caps.channels_present or [])
    has_pressure = bool({"pressure", "therapy_pressure", "epap", "ipap"} & channels)
    null_reason = bs_caps.null_reason
    return DeviceCapabilities(
        manufacturer=manufacturer,
        model=model_name,
        serial_number=serial_number,
        has_flow_waveform="flow" in channels,
        has_pressure_waveform=has_pressure,
        has_leak_waveform="leak" in channels,
        has_spo2="spo2" in channels,
        has_events=bool(bs_caps.event_types_present),
        has_analysis=analysis_run,
        notes=[str(null_reason)] if null_reason is not None else [],
    )


async def get_data_overview(
    db_session: AsyncSession, profile_id: int = 0
) -> DataOverviewResponse:
    """Return a comprehensive overview of all imported data.

    Called by the get_data_overview MCP tool. Provides device inventory,
    date ranges, available waveform channels, event types, and analysis status
    — everything an LLM needs to orient itself to a cold database.
    """
    from snore.services.breath_service import BreathService  # noqa: PLC0415

    device_svc = DeviceService(db_session, profile_id)
    raw_devices = await device_svc.list_devices()

    if not raw_devices:
        return DataOverviewResponse(devices=[])

    # Analysis status — count distinct sessions that have at least one AnalysisResult
    analysis_session_count = (
        await db_session.execute(
            select(func.count(models.AnalysisResult.session_id.distinct()))
        )
    ).scalar_one()
    analysis_run = analysis_session_count > 0

    device_infos: list[DeviceInfo] = []
    total_sessions = 0
    global_min_date: date | None = None
    global_max_date: date | None = None

    bs = BreathService(db_session, profile_id) if profile_id else None

    for d in raw_devices:
        # Per-device session stats
        result = await db_session.execute(
            select(
                func.count(models.Session.id),
                func.min(models.Session.start_time),
                func.max(models.Session.start_time),
            ).where(
                models.Session.device_id == d.id,
                models.Session.enabled.is_(True),
            )
        )
        row = result.one()
        count, min_dt, max_dt = row

        first_date = min_dt.date() if min_dt else None
        last_date = max_dt.date() if max_dt else None

        if first_date and (global_min_date is None or first_date < global_min_date):
            global_min_date = first_date
        if last_date and (global_max_date is None or last_date > global_max_date):
            global_max_date = last_date

        total_sessions += count or 0

        # Therapy modes for this device
        mode_rows = (
            (
                await db_session.execute(
                    select(models.Session.therapy_mode)
                    .where(
                        models.Session.device_id == d.id,
                        models.Session.enabled.is_(True),
                        models.Session.therapy_mode.is_not(None),
                    )
                    .distinct()
                    .order_by(models.Session.therapy_mode)
                )
            )
            .scalars()
            .all()
        )

        # Device capabilities from BreathService (G2 — capability-honest)
        dev_caps: DeviceCapabilities | None = None
        if bs is not None:
            try:
                bs_caps = await bs.get_device_capabilities(
                    d.id, date_start=first_date, date_end=last_date
                )
                dev_caps = _map_device_capabilities(
                    bs_caps, d.manufacturer, d.model, d.serial_number, analysis_run
                )
            except Exception:
                pass  # capabilities are best-effort; don't fail the overview

        device_infos.append(
            DeviceInfo(
                id=d.id,
                manufacturer=d.manufacturer,
                model=d.model,
                serial_number=d.serial_number,
                first_session_date=first_date,
                last_session_date=last_date,
                session_count=count or 0,
                therapy_modes=[m for m in mode_rows if m],
                device_capabilities=dev_caps,
            )
        )

    # Available waveform channel types across all sessions
    waveform_types = (
        (
            await db_session.execute(
                select(models.Waveform.waveform_type)
                .distinct()
                .order_by(models.Waveform.waveform_type)
            )
        )
        .scalars()
        .all()
    )

    # Available event types
    event_types = (
        (
            await db_session.execute(
                select(models.Event.event_type)
                .distinct()
                .order_by(models.Event.event_type)
            )
        )
        .scalars()
        .all()
    )

    return DataOverviewResponse(
        devices=device_infos,
        date_range_start=global_min_date,
        date_range_end=global_max_date,
        total_sessions=total_sessions,
        available_waveform_channels=list(waveform_types),
        available_event_types=list(event_types),
        analysis_run=analysis_run,
        analysis_session_count=analysis_session_count,
    )
