from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from snore.api.deps import DateRangeParams, PaginationParams, service_dep
from snore.api.errors import NotFoundError
from snore.api.schemas import (
    AnalysisDeleteRequest,
    AnalysisRunRequest,
    PaginatedResponse,
)
from snore.services import AnalysisFacade
from snore.services.schemas import AnalysisDeletePreview, AnalysisListItem

router = APIRouter()

AnalysisFacadeDep = Annotated[AnalysisFacade, Depends(service_dep(AnalysisFacade))]


@router.get("/analysis/sessions", response_model=PaginatedResponse[AnalysisListItem])
def list_analysis_sessions(
    facade: AnalysisFacadeDep,
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
    analyzed_only: bool = Query(default=False),
    sort_by: str = Query(default="date-desc"),
) -> PaginatedResponse[AnalysisListItem]:
    total = facade.count_sessions_with_status(
        start=dates.start_datetime, end=dates.end_datetime, analyzed_only=analyzed_only
    )
    items = facade.list_sessions_with_status(
        start=dates.start_datetime,
        end=dates.end_datetime,
        limit=pagination.limit,
        offset=pagination.offset,
        analyzed_only=analyzed_only,
        sort_by=sort_by,
    )
    return PaginatedResponse(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/sessions/{session_id}/analysis")
def get_analysis(session_id: int, facade: AnalysisFacadeDep) -> Any:
    result = facade.get_analysis_result(session_id)
    if result is None:
        raise NotFoundError(f"No analysis found for session {session_id}")
    return result


@router.post("/sessions/{session_id}/analysis", status_code=201)
def run_analysis(
    session_id: int,
    body: AnalysisRunRequest,
    facade: AnalysisFacadeDep,
) -> Any:
    return facade.run_analysis(
        session_id, modes=body.modes, store_results=body.store_results
    )


@router.delete("/analysis")
def delete_analysis(
    body: AnalysisDeleteRequest, facade: AnalysisFacadeDep
) -> dict[str, int]:
    deleted_count = facade.delete_analysis(
        body.session_ids, all_versions=body.all_versions
    )
    return {"deleted_count": deleted_count}


@router.get("/analysis/delete-preview", response_model=AnalysisDeletePreview)
def get_analysis_delete_preview(
    facade: AnalysisFacadeDep,
    session_ids: list[int] = Query(default=[]),
    all_versions: bool = Query(default=False),
) -> AnalysisDeletePreview:
    if not session_ids:
        return AnalysisDeletePreview(
            sessions_with_analysis=0,
            total_analysis_records=0,
            records_to_delete=0,
            patterns_count=0,
        )
    return facade.get_delete_preview(session_ids, all_versions=all_versions)
