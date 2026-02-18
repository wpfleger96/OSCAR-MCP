from datetime import datetime, time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from snore.api.deps import DateRangeParams, PaginationParams, get_db
from snore.api.errors import NotFoundError
from snore.api.schemas import (
    AnalysisDeleteRequest,
    AnalysisRunRequest,
    PaginatedResponse,
)
from snore.services import AnalysisFacade
from snore.services.schemas import AnalysisDeletePreview, AnalysisListItem

router = APIRouter()


@router.get("/analysis/sessions", response_model=PaginatedResponse[AnalysisListItem])
def list_analysis_sessions(
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
    analyzed_only: bool = Query(default=False),
    sort_by: str = Query(default="date-desc"),
) -> PaginatedResponse[AnalysisListItem]:
    facade = AnalysisFacade(db)
    start_dt = datetime.combine(dates.from_date, time.min) if dates.from_date else None
    end_dt = datetime.combine(dates.to_date, time.max) if dates.to_date else None
    total = facade.count_sessions_with_status(
        start=start_dt, end=end_dt, analyzed_only=analyzed_only
    )
    items = facade.list_sessions_with_status(
        start=start_dt,
        end=end_dt,
        limit=pagination.limit,
        offset=pagination.offset,
        analyzed_only=analyzed_only,
        sort_by=sort_by,
    )
    return PaginatedResponse(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/sessions/{session_id}/analysis")
def get_analysis(session_id: int, db: Session = Depends(get_db)) -> Any:
    facade = AnalysisFacade(db)
    result = facade.get_analysis_result(session_id)
    if result is None:
        raise NotFoundError(f"No analysis found for session {session_id}")
    return result


@router.post("/sessions/{session_id}/analysis", status_code=201)
def run_analysis(
    session_id: int,
    body: AnalysisRunRequest,
    db: Session = Depends(get_db),
) -> Any:
    facade = AnalysisFacade(db)
    return facade.run_analysis(
        session_id, modes=body.modes, store_results=body.store_results
    )


@router.delete("/analysis")
def delete_analysis(
    body: AnalysisDeleteRequest, db: Session = Depends(get_db)
) -> dict[str, int]:
    facade = AnalysisFacade(db)
    deleted_count = facade.delete_analysis(
        body.session_ids, all_versions=body.all_versions
    )
    return {"deleted_count": deleted_count}


@router.get("/analysis/delete-preview", response_model=AnalysisDeletePreview)
def get_analysis_delete_preview(
    session_ids: list[int] = Query(default=[]),
    all_versions: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> AnalysisDeletePreview:
    if not session_ids:
        return AnalysisDeletePreview(
            sessions_with_analysis=0,
            total_analysis_records=0,
            records_to_delete=0,
            patterns_count=0,
        )
    facade = AnalysisFacade(db)
    return facade.get_delete_preview(session_ids, all_versions=all_versions)
