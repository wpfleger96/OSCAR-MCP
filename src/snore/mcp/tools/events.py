"""get_events tool — EventService adapter with inline context.

Timestamp contract (A6):
- Event positions are stored as device wall-clock (naive datetime).
- Output uses tier-2 (offset-free ISO 8601 + timezone_status="unknown")
  for absolute times and tier-3 (offset_seconds from Session.start_time)
  for in-session positions.  No UTC offsets are fabricated.
"""

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
            offset_seconds = (ev.start_time - session_start).total_seconds()
            minutes_since_start = offset_seconds / 60.0
            context = EventContext(
                # TODO(PR-A seam): pressure/leak at event and MV-prior-120s require
                # BreathService.get_contextual_events() — a multi-channel waveform
                # window lookup. Deferred to Phase 4 when PR-A merges.
                # Swap site: replace None values here with seam call results.
                # Ref: docs/mcp-server-plan.md Appendix A §8 (ContextualEvent).
                pressure_at_event_cmh2o=None,
                leak_at_event_lpm=None,
                mv_prior_120s_lpm=None,
                minutes_since_session_start=round(minutes_since_start, 2),
            )

        rows.append(
            EventRow(
                id=int(ev.id),
                event_type=ev.event_type,
                # Tier-2: device wall-clock, offset-free ISO 8601, no TZ fabricated
                start_time_wall_clock=ev.start_time.isoformat(),
                timezone_status="unknown",
                # Tier-3: in-session position
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
        # Tier-2: session start as device wall-clock anchor for offset_seconds
        session_start_wall_clock=session_start.isoformat(),
        timezone_status="unknown",
        events=rows,
        total_events=len(rows),
    )
