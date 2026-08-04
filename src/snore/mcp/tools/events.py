"""get_events tool — BreathService.get_contextual_events() adapter.

Timestamp contract (A6):
- Event positions are stored as device wall-clock (naive datetime).
- Output uses tier-2 (offset-free ISO 8601 + timezone_status="unknown")
  for absolute times and tier-3 (offset_seconds from Session.start_time)
  for in-session positions.  No UTC offsets are fabricated.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.errors import ValidationError
from snore.mcp.schemas import EventContext, EventRow, EventsResponse


async def get_events(
    db_session: AsyncSession,
    event_date: date,
    profile_id: int = 0,
    device_id: int | None = None,
    types: list[str] | None = None,
    min_duration: float | None = None,
    include_context: bool = True,
) -> EventsResponse:
    """Return respiratory events for a session date with inline waveform context.

    Uses BreathService.get_contextual_events() to fetch events enriched with
    pressure/leak at event time and MV in the prior 120 s.

    Args:
        db_session: Async database session.
        event_date: The date to query (YYYY-MM-DD).
        profile_id: Profile scope for BreathService ownership checks.
        device_id: Optional device filter; required when multiple devices share a date.
        types: Optional list of event types to filter (e.g. ["OA", "CA", "H"]).
        min_duration: Minimum event duration in seconds (optional filter).
        include_context: Whether to attach per-event waveform context block.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        DeviceAmbiguityError,
        DeviceNotOwnedError,
    )

    if not profile_id:
        # profile_id not yet resolved (lifespan not started) — fall back to
        # the legacy direct query path so unit tests without a lifespan still work.
        return await _legacy_get_events(
            db_session,
            event_date,
            types=types,
            min_duration=min_duration,
            include_context=include_context,
        )

    bs = BreathService(db_session, profile_id)
    try:
        contextual_events = await bs.get_contextual_events(
            therapy_date=event_date,
            event_types=types,
            min_duration=min_duration,
            device_id=device_id,
        )
    except (DeviceAmbiguityError, DeviceNotOwnedError) as exc:
        raise ValidationError(str(exc)) from exc
    except ValueError as exc:
        raise ValidationError(
            f"No therapy data found for date {event_date}. "
            "Use get_data_overview to check which dates have imported data."
        ) from exc

    if not contextual_events:
        # No events — still return a well-formed response with session anchor.
        return await _legacy_get_events(
            db_session,
            event_date,
            types=types,
            min_duration=min_duration,
            include_context=False,
        )

    session_id = contextual_events[0].session_id
    session_start = contextual_events[0].session_start_wall_clock

    rows: list[EventRow] = []
    for ev in contextual_events:
        context: EventContext | None = None
        if include_context:
            context = EventContext(
                pressure_at_event_cmh2o=ev.pressure_at_event_cmh2o,
                leak_at_event_lpm=ev.leak_at_event_lpm,
                mv_prior_120s_lpm=ev.mv_prior_120s_lpm,
                minutes_since_session_start=round(ev.minutes_since_session_start, 2),
            )

        rows.append(
            EventRow(
                id=None,  # BreathService seam does not expose internal event IDs
                event_type=ev.event_type,
                start_time_wall_clock=ev.event_start_wall_clock.isoformat(),
                timezone_status="unknown",
                offset_seconds=ev.offset_seconds,
                duration_seconds=ev.duration_seconds,
                spo2_drop_pct=None,
                peak_flow_limitation=None,
                context=context,
            )
        )

    return EventsResponse(
        date=event_date.isoformat(),
        session_id=session_id,
        session_start_wall_clock=session_start.isoformat(),
        timezone_status="unknown",
        events=rows,
        total_events=len(rows),
    )


# ---------------------------------------------------------------------------
# Legacy fallback (no profile_id / empty events result)
# ---------------------------------------------------------------------------


async def _legacy_get_events(
    db_session: AsyncSession,
    event_date: date,
    types: list[str] | None = None,
    min_duration: float | None = None,
    include_context: bool = True,
) -> EventsResponse:
    """Direct DB query path — used when profile_id is 0 or events list is empty."""
    from datetime import datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415

    day_row = (
        (
            await db_session.execute(
                select(models.Day).where(models.Day.date == event_date)
            )
        )
        .scalars()
        .first()
    )

    if day_row is None:
        raise ValidationError(
            f"No therapy data found for date {event_date}. "
            "Use get_data_overview to check which dates have imported data."
        )

    session_row = (
        (
            await db_session.execute(
                select(models.Session)
                .where(
                    models.Session.day_id == day_row.id,
                    models.Session.enabled.is_(True),
                )
                .order_by(models.Session.start_time)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    if session_row is None:
        raise ValidationError(f"No enabled session found for date {event_date}.")

    session_id = int(session_row.id)
    session_start: datetime = session_row.start_time

    event_q = (
        select(models.Event)
        .where(models.Event.session_id == session_id)
        .order_by(models.Event.start_time)
    )
    if types:
        event_q = event_q.where(models.Event.event_type.in_(types))
    if min_duration is not None:
        event_q = event_q.where(models.Event.duration_seconds >= min_duration)

    event_rows = (await db_session.execute(event_q)).scalars().all()

    rows: list[EventRow] = []
    for ev in event_rows:
        context: EventContext | None = None
        if include_context:
            offset_seconds = (ev.start_time - session_start).total_seconds()
            context = EventContext(
                pressure_at_event_cmh2o=None,
                leak_at_event_lpm=None,
                mv_prior_120s_lpm=None,
                minutes_since_session_start=round(offset_seconds / 60.0, 2),
            )

        rows.append(
            EventRow(
                id=int(ev.id),
                event_type=ev.event_type,
                start_time_wall_clock=ev.start_time.isoformat(),
                timezone_status="unknown",
                offset_seconds=(ev.start_time - session_start).total_seconds(),
                duration_seconds=ev.duration_seconds,
                spo2_drop_pct=ev.spo2_drop,
                peak_flow_limitation=ev.peak_flow_limitation,
                context=context,
            )
        )

    return EventsResponse(
        date=event_date.isoformat(),
        session_id=session_id,
        session_start_wall_clock=session_start.isoformat(),
        timezone_status="unknown",
        events=rows,
        total_events=len(rows),
    )
