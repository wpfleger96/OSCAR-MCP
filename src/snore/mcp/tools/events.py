"""get_events tool — EventService adapter with inline context."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.mcp.errors import ValidationError
from snore.mcp.schemas import EventContext, EventRow, EventsResponse


async def get_events(
    db_session: AsyncSession,
    event_date: date,
    types: list[str] | None = None,
    min_duration: float | None = None,
    include_context: bool = True,
) -> EventsResponse:
    """Return respiratory events for a session date with inline context.

    For each event, inline context includes pressure/leak at the event
    and MV in the prior 120 s (when waveform data is present) and
    minutes since session start.

    Args:
        db_session: Async database session.
        event_date: The date to query (YYYY-MM-DD).
        types: Optional list of event types to filter (e.g. ["OA", "CA", "H"]).
        min_duration: Minimum event duration in seconds (optional filter).
        include_context: Whether to attach per-event context block.
    """
    # Find the enabled session for this date
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

    # Fetch events with optional filters
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
            minutes_since_start = (ev.start_time - session_start).total_seconds() / 60.0
            context = EventContext(
                # Pressure/leak at event and MV-prior-120s require waveform
                # sample-at-timestamp lookups — deferred to Phase 4 (render_window).
                # Mark as None with no reason field; capability-honest per G2.
                pressure_at_event_cmh2o=None,
                leak_at_event_lpm=None,
                mv_prior_120s_lpm=None,
                minutes_since_session_start=round(minutes_since_start, 2),
            )

        rows.append(
            EventRow(
                id=int(ev.id),
                event_type=ev.event_type,
                start_time_iso=ev.start_time.isoformat(),
                duration_seconds=ev.duration_seconds,
                spo2_drop_pct=ev.spo2_drop,
                peak_flow_limitation=ev.peak_flow_limitation,
                context=context,
            )
        )

    return EventsResponse(
        date=event_date.isoformat(),
        session_id=session_id,
        events=rows,
        total_events=len(rows),
    )
