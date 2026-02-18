from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.api.errors import NotFoundError
from snore.api.schemas import EventItem
from snore.database import models
from snore.services import AnalysisFacade, EventService
from snore.services.schemas import EventMatchResult

router = APIRouter()


def _event_to_item(event: models.Event, session_start: datetime) -> EventItem:
    return EventItem(
        id=event.id,
        event_type=event.event_type,
        start_time=event.start_time.timestamp(),
        duration_seconds=event.duration_seconds or 0.0,
        offset_seconds=(event.start_time - session_start).total_seconds(),
    )


@router.get("/{session_id}/events", response_model=list[EventItem])
def list_events(
    session_id: int,
    event_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[EventItem]:
    result = EventService(db).list_session_events(session_id, event_type)
    if result is None:
        raise NotFoundError(f"Session {session_id} not found")
    events, session_start = result
    return [_event_to_item(e, session_start) for e in events]


@router.get("/{session_id}/events/match", response_model=EventMatchResult)
def match_events(
    session_id: int,
    mode: str = Query(default="aasm"),
    db: Session = Depends(get_db),
) -> EventMatchResult:
    svc = EventService(db)

    machine_times = svc.get_machine_event_times(session_id)
    if machine_times is None:
        raise NotFoundError(f"Session {session_id} not found")

    facade = AnalysisFacade(db)
    analysis = facade.get_analysis_result(session_id)
    if not analysis:
        raise NotFoundError(f"No analysis results found for session {session_id}")

    programmatic_times: list[float] = []
    mode_result = analysis.mode_results.get(mode)
    if mode_result is not None:
        for apnea in mode_result.apneas:
            programmatic_times.append(analysis.timestamp_start + apnea.start_time)
        for hyp in mode_result.hypopneas:
            programmatic_times.append(analysis.timestamp_start + hyp.start_time)
        programmatic_times.sort()

    return svc.match_events(machine_times, programmatic_times)
