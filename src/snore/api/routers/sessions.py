from datetime import datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from snore.api.deps import DateRangeParams, PaginationParams, get_db
from snore.api.schemas import (
    PaginatedResponse,
    SessionDeleteRequest,
    SessionEnabledRequest,
)
from snore.services import SessionService
from snore.services.schemas import DeletePreview, SessionDetail, SessionListItem

router = APIRouter()


@router.get("/", response_model=PaginatedResponse[SessionListItem])
def list_sessions(
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
    device: str | None = Query(default=None),
    sort_by: Literal["date-asc", "date-desc", "session-id", "duration"] = Query(
        default="date-desc"
    ),
    include_disabled: bool = Query(default=False),
) -> PaginatedResponse[SessionListItem]:
    service = SessionService(db)
    from_dt = datetime.combine(dates.from_date, time.min) if dates.from_date else None
    to_dt = datetime.combine(dates.to_date, time.max) if dates.to_date else None
    result = service.list_sessions(
        device=device,
        from_date=from_dt,
        to_date=to_dt,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort_by,
        include_disabled=include_disabled,
    )
    return PaginatedResponse(
        items=result.sessions,
        total=result.total_count,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{session_id}/delete-preview", response_model=DeletePreview)
def get_delete_preview(session_id: int, db: Session = Depends(get_db)) -> DeletePreview:
    service = SessionService(db)
    return service.get_delete_preview(session_ids=[session_id])


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    include_settings: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> SessionDetail:
    service = SessionService(db)
    return service.get_session_detail(session_id, include_settings=include_settings)


@router.patch("/{session_id}", response_model=SessionDetail)
def update_session(
    session_id: int,
    body: SessionEnabledRequest,
    db: Session = Depends(get_db),
) -> SessionDetail:
    service = SessionService(db)
    service.set_session_enabled(session_id, body.enabled)
    return service.get_session_detail(session_id)


@router.delete("/", response_model=dict)
def delete_sessions(
    body: SessionDeleteRequest, db: Session = Depends(get_db)
) -> dict[str, int]:
    service = SessionService(db)
    deleted_count = service.delete_sessions(body.session_ids)
    return {"deleted_count": deleted_count}
