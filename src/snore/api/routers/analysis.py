from datetime import datetime, time
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from snore.api.deps import DateRangeParams, PaginationParams, service_dep
from snore.api.errors import NotFoundError
from snore.api.schemas import (
    AnalysisDeleteRequest,
    AnalysisRunRequest,
    BatchAnalysisRequest,
    PaginatedResponse,
)
from snore.services import AnalysisFacade
from snore.services.schemas import (
    AnalysisDeletePreview,
    AnalysisListItem,
    BatchAnalysisResult,
)

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
# response_model omitted intentionally: AnalysisResult is a complex internal Pydantic
# model whose schema changes across analysis modes. Exposing it via response_model would
# tightly couple the OpenAPI spec to internal analysis model structure.
def get_analysis(session_id: int, facade: AnalysisFacadeDep) -> Any:
    result = facade.get_analysis_result(session_id)
    if result is None:
        raise NotFoundError(f"No analysis found for session {session_id}")
    return result


@router.post("/sessions/{session_id}/analysis", status_code=201)
# response_model omitted intentionally: same reason as get_analysis above.
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


@router.post("/analysis/batch", status_code=201, response_model=BatchAnalysisResult)
def run_batch_analysis(
    body: BatchAnalysisRequest,
    facade: AnalysisFacadeDep,
) -> BatchAnalysisResult:
    if body.from_date is None and body.to_date is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of from_date or to_date is required",
        )
    return facade.run_batch_analysis(
        from_date=datetime.combine(body.from_date, time.min)
        if body.from_date
        else None,
        to_date=datetime.combine(body.to_date, time.max) if body.to_date else None,
        modes=cast(list[str], body.modes),
        store_results=body.store_results,
    )
