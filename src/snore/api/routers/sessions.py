from datetime import datetime, time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from snore.api.deps import DateRangeParams, PaginationParams, service_dep
from snore.api.schemas import (
    BulkDeletePreviewRequest,
    PaginatedResponse,
    SessionDeleteRequest,
    SessionEnabledRequest,
)
from snore.services import SessionService
from snore.services.schemas import DeletePreview, SessionDetail, SessionListItem

router = APIRouter()

SessionServiceDep = Annotated[SessionService, Depends(service_dep(SessionService))]


@router.get("/", response_model=PaginatedResponse[SessionListItem])
def list_sessions(
    service: SessionServiceDep,
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
    device: str | None = Query(default=None),
    sort_by: Literal["date-asc", "date-desc", "session-id", "duration"] = Query(
        default="date-desc"
    ),
    include_disabled: bool = Query(default=False),
) -> PaginatedResponse[SessionListItem]:
    result = service.list_sessions(
        device=device,
        from_date=dates.start_datetime,
        to_date=dates.end_datetime,
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


@router.post("/delete-preview", response_model=DeletePreview)
def bulk_delete_preview(
    body: BulkDeletePreviewRequest, service: SessionServiceDep
) -> DeletePreview:
    return service.get_delete_preview(
        device=body.device,
        session_ids=body.session_ids,
        from_date=datetime.combine(body.from_date, time.min) if body.from_date else None,
        to_date=datetime.combine(body.to_date, time.max) if body.to_date else None,
        delete_all=body.delete_all,
    )


@router.get("/{session_id}/delete-preview", response_model=DeletePreview)
def get_delete_preview(session_id: int, service: SessionServiceDep) -> DeletePreview:
    return service.get_delete_preview(session_ids=[session_id])


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    service: SessionServiceDep,
    include_settings: bool = Query(default=False),
) -> SessionDetail:
    return service.get_session_detail(session_id, include_settings=include_settings)


@router.patch("/{session_id}", response_model=SessionDetail)
def update_session(
    session_id: int,
    body: SessionEnabledRequest,
    service: SessionServiceDep,
) -> SessionDetail:
    service.set_session_enabled(session_id, body.enabled)
    return service.get_session_detail(session_id)


@router.delete("/", response_model=dict)
def delete_sessions(
    body: SessionDeleteRequest, service: SessionServiceDep
) -> dict[str, int]:
    deleted_count = service.delete_sessions(body.session_ids)
    return {"deleted_count": deleted_count}
