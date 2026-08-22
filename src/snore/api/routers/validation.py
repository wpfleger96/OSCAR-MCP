import uuid

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api import validation_jobs
from snore.api.deps import PaginationParams, get_db
from snore.api.guards import RequireAuth, RequireWritable
from snore.api.schemas import (
    BreathTrendsValidationRequest,
    FlValidationRequest,
    ReraValidationRequest,
    ValidationRequest,
    ValidationRunDetail,
    ValidationRunRequest,
    ValidationRunsListResponse,
    ValidationRunStatus,
    ValidatorType,
)
from snore.api.validation_registry import RunMode, engine_identity, get_spec
from snore.validation import (
    BatchValidator,
    BreathTrendsValidationReport,
    BreathTrendsValidator,
    FlowLimitationValidator,
    FlValidationReport,
    ReraValidationReport,
    ReraValidator,
    ValidationReport,
)

router = APIRouter()


@router.post("/", response_model=ValidationReport)
async def run_validation(
    body: ValidationRequest,
    actor: RequireWritable,
    db: AsyncSession = Depends(get_db),
) -> ValidationReport:
    validator = BatchValidator(db, actor.profile_id)
    return await validator.validate_date_range(
        date_from=body.from_date.isoformat(),
        date_to=body.to_date.isoformat(),
        mode=body.mode,
    )


@router.post("/fl", response_model=FlValidationReport)
async def run_fl_validation(
    body: FlValidationRequest,
    actor: RequireAuth,
    db: AsyncSession = Depends(get_db),
) -> FlValidationReport:
    validator = FlowLimitationValidator(db, actor.profile_id)
    return await validator.validate_date_range(
        date_from=body.from_date.isoformat(),
        date_to=body.to_date.isoformat(),
    )


@router.post("/rera", response_model=ReraValidationReport)
async def run_rera_validation(
    body: ReraValidationRequest,
    actor: RequireAuth,
    db: AsyncSession = Depends(get_db),
) -> ReraValidationReport:
    validator = ReraValidator(db, actor.profile_id)
    return await validator.validate_date_range(
        date_from=body.from_date.isoformat(),
        date_to=body.to_date.isoformat(),
    )


@router.post("/breaths", response_model=BreathTrendsValidationReport)
async def run_breath_trends_validation(
    body: BreathTrendsValidationRequest,
    actor: RequireAuth,
    db: AsyncSession = Depends(get_db),
) -> BreathTrendsValidationReport:
    validator = BreathTrendsValidator(db, actor.profile_id)
    return await validator.validate_date_range(
        date_from=body.from_date.isoformat(),
        date_to=body.to_date.isoformat(),
    )


# ---------------------------------------------------------------------------
# Persisted validation runs
# ---------------------------------------------------------------------------


def _row_to_status(row: Any) -> ValidationRunStatus:
    """Map a ``ValidationRun`` ORM row to the list/status schema."""
    return ValidationRunStatus(
        run_id=row.id,
        job_id=row.job_id,
        validator_type=row.validator_type,
        date_from=row.date_from.isoformat(),
        date_to=row.date_to.isoformat(),
        state=row.state,
        error_message=row.error_message,
        engine_identity=row.engine_identity_json,
        validator_params=row.validator_params_json,
        owner_user_id=row.owner_user_id,
        created_at=row.created_at.timestamp(),
        started_at=row.started_at.timestamp() if row.started_at else None,
        finished_at=row.finished_at.timestamp() if row.finished_at else None,
        reused=False,
    )


@router.post("/runs", status_code=202, response_model=ValidationRunStatus)
async def create_validation_run(
    body: ValidationRunRequest,
    actor: RequireWritable,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunStatus:
    spec = get_spec(body.validator_type)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or unregistered validator type: {body.validator_type!r}",
        )

    identity = engine_identity()
    params = spec.current_params(body.params)

    if not body.force:
        existing = await validation_jobs.find_reusable_run(
            db,
            profile_id=actor.profile_id,
            validator_type=body.validator_type,
            date_from=body.from_date,
            date_to=body.to_date,
            engine_identity=identity,
            validator_params=params,
            owner_user_id=actor.user_id,
        )
        if existing is not None:
            status = _row_to_status(existing)
            status.reused = True
            return status

    if spec.mode == RunMode.SYNC:
        report = await spec.run(
            db,
            actor.profile_id,
            body.from_date.isoformat(),
            body.to_date.isoformat(),
            params,
        )
        run = await validation_jobs.create_sync_run(
            db,
            profile_id=actor.profile_id,
            owner_user_id=actor.user_id,
            validator_type=body.validator_type,
            date_from=body.from_date,
            date_to=body.to_date,
            engine_identity=identity,
            validator_params=params,
            report_json=report.model_dump(mode="json"),
        )
        return _row_to_status(run)

    job_id = uuid.uuid4().hex
    run_id = await validation_jobs.insert_queued_run(
        job_id=job_id,
        profile_id=actor.profile_id,
        owner_user_id=actor.user_id,
        validator_type=body.validator_type,
        date_from=body.from_date,
        date_to=body.to_date,
        engine_identity=identity,
        validator_params=params,
    )
    job = validation_jobs.enqueue(
        run_id=run_id,
        profile_id=actor.profile_id,
        validator_type=body.validator_type,
        date_from=body.from_date,
        date_to=body.to_date,
        engine_identity=identity,
        validator_params=params,
        job_id=job_id,
        owner_user_id=actor.user_id,
    )
    if job is None:
        await validation_jobs.delete_run(run_id)
        raise HTTPException(
            status_code=429, detail="Validation queue is full; try again later"
        )
    return ValidationRunStatus.model_validate(job.to_dict())


@router.get("/runs", response_model=ValidationRunsListResponse)
async def list_validation_runs(
    actor: RequireAuth,
    pagination: Annotated[PaginationParams, Depends()],
    validator_type: Annotated[ValidatorType | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunsListResponse:
    from snore.api.jobs import merge_job_lists  # noqa: PLC0415
    from snore.database import models  # noqa: PLC0415

    in_memory: list[ValidationRunStatus] = []
    in_memory_ids: set[str] = set()
    for job in validation_jobs.list_jobs(owner_user_id=actor.user_id):
        if validator_type is not None and job.validator_type != validator_type:
            continue
        in_memory_ids.add(job.job_id)
        in_memory.append(ValidationRunStatus.model_validate(job.to_dict()))

    terminal_states = [s.value for s in validation_jobs.TERMINAL_STATES]
    stmt = (
        select(models.ValidationRun)
        .where(
            or_(
                models.ValidationRun.owner_user_id == actor.user_id,
                models.ValidationRun.owner_user_id.is_(None),
            ),
            models.ValidationRun.state.in_(terminal_states),
        )
        .order_by(models.ValidationRun.created_at.desc())
    )
    if validator_type is not None:
        stmt = stmt.where(models.ValidationRun.validator_type == validator_type)
    db_rows = (await db.execute(stmt)).scalars().all()

    merged = merge_job_lists(
        in_memory,
        in_memory_ids,
        db_rows,
        to_status=_row_to_status,
        sort_key=lambda s: s.created_at,
    )
    total = len(merged)
    page = merged[pagination.offset : pagination.offset + pagination.limit]
    return ValidationRunsListResponse(
        runs=page, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/runs/{run_id}", response_model=ValidationRunDetail)
async def get_validation_run(
    run_id: int,
    actor: RequireAuth,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunDetail:
    from snore.api.jobs import owned_or_404  # noqa: PLC0415
    from snore.database import models  # noqa: PLC0415

    row = await db.get(models.ValidationRun, run_id)
    row = owned_or_404(row, actor.user_id, not_found_detail="Validation run not found")
    status = _row_to_status(row)
    return ValidationRunDetail(**status.model_dump(), report_json=row.report_json)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_validation_run(
    run_id: int,
    actor: RequireWritable,
    db: AsyncSession = Depends(get_db),
) -> None:
    from sqlalchemy import delete as sa_delete  # noqa: PLC0415

    from snore.api.jobs import cancel_or_409, owned_or_404  # noqa: PLC0415
    from snore.database import models  # noqa: PLC0415

    row = await db.get(models.ValidationRun, run_id)
    row = owned_or_404(row, actor.user_id, not_found_detail="Validation run not found")

    # A still-running (or queued) run is cancelled rather than deleted so the
    # worker's terminal write does not resurrect a half-deleted row.
    job_id = row.job_id
    job = validation_jobs.get_job(job_id) if job_id is not None else None
    if job_id is not None and job is not None and not job.is_terminal:
        cancel_or_409(
            validation_jobs.cancel_job,
            job_id,
            already_detail="Validation run is already finished",
        )
        return

    await db.execute(
        sa_delete(models.ValidationRun).where(models.ValidationRun.id == run_id)
    )
