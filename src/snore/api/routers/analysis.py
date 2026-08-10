from datetime import datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.types import AnalysisResult
from snore.api import analysis_jobs
from snore.api.deps import DateRangeParams, PaginationParams, get_db, service_dep
from snore.api.errors import NotFoundError
from snore.api.guards import RequireAuth, RequireWritable
from snore.api.schemas import (
    AnalysisDeleteRequest,
    AnalysisJobEnqueued,
    AnalysisJobsListResponse,
    AnalysisRunRequest,
    BatchAnalysisRequest,
    PaginatedResponse,
)
from snore.services import AnalysisFacade
from snore.services.schemas import (
    AnalysisDeletePreview,
    AnalysisListItem,
)

router = APIRouter()

AnalysisFacadeDep = Annotated[AnalysisFacade, Depends(service_dep(AnalysisFacade))]


@router.get("/analysis/sessions", response_model=PaginatedResponse[AnalysisListItem])
async def list_analysis_sessions(
    facade: AnalysisFacadeDep,
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
    analyzed_only: bool = Query(default=False),
    sort_by: str = Query(default="date-desc"),
) -> PaginatedResponse[AnalysisListItem]:
    total = await facade.count_sessions_with_status(
        start=dates.start_datetime, end=dates.end_datetime, analyzed_only=analyzed_only
    )
    items = await facade.list_sessions_with_status(
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


@router.get("/sessions/{session_id}/analysis", response_model=AnalysisResult)
async def get_analysis(session_id: int, facade: AnalysisFacadeDep) -> AnalysisResult:
    result = await facade.get_analysis_result(session_id)
    if result is None:
        raise NotFoundError(f"No analysis found for session {session_id}")
    return result


@router.post(
    "/sessions/{session_id}/analysis", status_code=201, response_model=AnalysisResult
)
async def run_analysis(
    session_id: int,
    body: AnalysisRunRequest,
    facade: AnalysisFacadeDep,
    _actor: RequireWritable,
) -> AnalysisResult:
    try:
        return await facade.run_analysis(
            session_id,
            modes=body.modes,
            primary_mode=body.primary_mode,
            store_results=body.store_results,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/analysis")
async def delete_analysis(
    body: AnalysisDeleteRequest,
    facade: AnalysisFacadeDep,
    _actor: RequireWritable,
) -> dict[str, int]:
    if body.session_ids:
        owned = await facade.get_owned_session_ids(body.session_ids)
        if owned != set(body.session_ids):
            raise HTTPException(
                status_code=404, detail="One or more sessions not found"
            )
    deleted_count = await facade.delete_analysis(
        body.session_ids, all_versions=body.all_versions
    )
    return {"deleted_count": deleted_count}


@router.get("/analysis/delete-preview", response_model=AnalysisDeletePreview)
async def get_analysis_delete_preview(
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
    return await facade.get_delete_preview(session_ids, all_versions=all_versions)


@router.post("/analysis/batch", status_code=202, response_model=AnalysisJobEnqueued)
async def run_batch_analysis(
    body: BatchAnalysisRequest,
    facade: AnalysisFacadeDep,
    actor: RequireWritable,
) -> AnalysisJobEnqueued:
    from snore.analysis.modes.config import (  # noqa: PLC0415
        AVAILABLE_CONFIGS,
        DEFAULT_MODE,
    )

    if body.from_date is None and body.to_date is None and not body.missing_only:
        raise HTTPException(
            status_code=400,
            detail="At least one of from_date or to_date is required",
        )

    # Validate modes and primary_mode at the endpoint so invalid input fails fast.
    invalid_modes = [m for m in body.modes if m not in AVAILABLE_CONFIGS]
    if invalid_modes:
        raise HTTPException(status_code=422, detail=f"Unknown mode(s): {invalid_modes}")
    if body.primary_mode is not None and body.primary_mode not in body.modes:
        raise HTTPException(
            status_code=422,
            detail="primary_mode must be a member of modes",
        )
    if body.primary_mode is None and DEFAULT_MODE not in body.modes:
        raise HTTPException(
            status_code=422,
            detail="primary_mode must be supplied explicitly when aasm is not in modes",
        )

    from_dt = datetime.combine(body.from_date, time.min) if body.from_date else None
    to_dt = datetime.combine(body.to_date, time.max) if body.to_date else None

    session_ids = await facade.list_session_ids(
        from_date=from_dt, to_date=to_dt, missing_only=body.missing_only
    )

    if not session_ids:
        detail = (
            "No unanalyzed sessions to backfill"
            if body.missing_only
            else "No sessions found for the specified date range"
        )
        raise HTTPException(status_code=422, detail=detail)

    aj = analysis_jobs.enqueue(
        profile_id=facade.profile_id,
        session_ids=session_ids,
        source=analysis_jobs.AnalysisJobSource.BATCH,
        owner_user_id=actor.user_id,
        modes=list(body.modes),
        primary_mode=body.primary_mode,
        store_results=body.store_results,
    )
    if aj is None:
        raise HTTPException(
            status_code=429, detail="Analysis queue is full; try again later"
        )

    return AnalysisJobEnqueued(job_id=aj.job_id, session_count=len(session_ids))


@router.get("/analysis/jobs", response_model=AnalysisJobsListResponse)
async def list_analysis_jobs(
    actor: RequireAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnalysisJobsListResponse:
    from sqlalchemy import or_, select  # noqa: PLC0415

    from snore.api.schemas import AnalysisJobStatus  # noqa: PLC0415
    from snore.database import models  # noqa: PLC0415

    result: list[AnalysisJobStatus] = []
    in_memory_ids: set[str] = set()

    for aj in analysis_jobs.list_jobs(owner_user_id=actor.user_id):
        in_memory_ids.add(aj.job_id)
        result.append(AnalysisJobStatus.model_validate(aj.to_dict()))

    # Historical terminal records from the database.
    stmt = (
        select(models.AnalysisJobRecord)
        .where(
            or_(
                models.AnalysisJobRecord.owner_user_id == actor.user_id,
                models.AnalysisJobRecord.owner_user_id.is_(None),
            ),
            models.AnalysisJobRecord.state.in_(
                [s.value for s in analysis_jobs.TERMINAL_STATES]
            ),
        )
        .order_by(models.AnalysisJobRecord.created_at.desc())
        .limit(50)
    )
    for rec in (await db.execute(stmt)).scalars():
        if rec.job_id in in_memory_ids:
            continue
        result.append(
            AnalysisJobStatus(
                job_id=rec.job_id,
                state=rec.state,
                source=rec.source,
                session_count=len(rec.session_ids_json) if rec.session_ids_json else 0,
                progress_completed=rec.progress_completed,
                progress_total=rec.progress_total,
                error_message=rec.error_message,
                created_at=rec.created_at.timestamp(),
                started_at=rec.started_at.timestamp() if rec.started_at else None,
                finished_at=rec.finished_at.timestamp() if rec.finished_at else None,
                owner_user_id=rec.owner_user_id,
            )
        )

    result.sort(key=lambda j: j.created_at, reverse=True)
    return AnalysisJobsListResponse(jobs=result)


@router.delete("/analysis/jobs/{job_id}", status_code=204)
async def cancel_analysis_job(job_id: str, actor: RequireWritable) -> None:
    job = analysis_jobs.get_job(job_id)
    if job is None or (
        job.owner_user_id is not None and job.owner_user_id != actor.user_id
    ):
        # 404 instead of 403 — no information leak about other users' job IDs.
        raise HTTPException(status_code=404, detail="Job not found")
    if not analysis_jobs.cancel_job(job_id):
        raise HTTPException(
            status_code=409,
            detail="Analysis job is already finished and cannot be cancelled",
        )
