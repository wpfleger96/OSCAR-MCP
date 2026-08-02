"""get_data_overview tool — cold-start orientation."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.mcp.schemas import DataOverviewResponse, DeviceInfo
from snore.services.device_service import DeviceService


async def get_data_overview(db_session: AsyncSession) -> DataOverviewResponse:
    """Return a comprehensive overview of all imported data.

    Called by the get_data_overview MCP tool. Provides device inventory,
    date ranges, available waveform channels, event types, and analysis status
    — everything an LLM needs to orient itself to a cold database.
    """
    device_svc = DeviceService(db_session)
    raw_devices = await device_svc.list_devices()

    if not raw_devices:
        return DataOverviewResponse(devices=[])

    device_infos: list[DeviceInfo] = []
    total_sessions = 0
    global_min_date = None
    global_max_date = None

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

    # Analysis status
    analysis_count_result = await db_session.execute(
        select(func.count(models.AnalysisResult.id.distinct()))
        .join(models.Session, models.AnalysisResult.session_id == models.Session.id)
        .where(models.Session.enabled.is_(True))
    )
    analysis_session_count = (
        await db_session.execute(
            select(func.count(models.AnalysisResult.session_id.distinct()))
        )
    ).scalar_one()
    analysis_run = analysis_session_count > 0
    _ = analysis_count_result  # suppress unused var

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
