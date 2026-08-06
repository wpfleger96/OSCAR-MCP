from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
import tempfile

from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import IO, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from snore.api.deps import ActorDep, get_db
from snore.api.guards import RequireAuth, RequireWritable
from snore.api.import_jobs import (
    ImportJob,
    JobState,
    JobType,
    cancel_job,
    create_job,
    enqueue_for_execution,
    get_job,
    list_jobs,
    remove_job,
    reserve_slot,
)
from snore.api.schemas import (
    ImportResultSummary,
    ImportSourceResultSummary,
    LinkedAnalysisSummary,
    PipelineJobsListResponse,
    PipelineJobStatus,
)
from snore.auth.actor import ActorContext
from snore.database import models
from snore.services.import_service import ImportService, safe_relative_path
from snore.services.schemas import ImportSource

logger = logging.getLogger(__name__)


class _ByteCeilingReceive:
    """ASGI receive wrapper that raises HTTPException(413) on byte-ceiling breach.

    Used for the upload ingress ceiling.  The auth-body ceiling uses a
    pre-read buffer in AuthPathMiddleware instead, which avoids the Starlette
    body-parser translating the 413 to 400.
    """

    def __init__(
        self,
        inner: Callable[[], Awaitable[MutableMapping[str, Any]]],
        max_bytes: int,
        detail: str | None = None,
    ) -> None:
        self._inner = inner
        self._max = max_bytes
        self._detail = (
            detail or f"Request body exceeds the {max_bytes // 1024} KiB limit"
        )
        self._seen = 0

    async def __call__(self) -> MutableMapping[str, Any]:
        from fastapi import HTTPException  # noqa: PLC0415

        msg = await self._inner()
        if msg.get("type") == "http.request":
            chunk: bytes = msg.get("body", b"")
            self._seen += len(chunk)
            if self._seen > self._max:
                raise HTTPException(status_code=413, detail=self._detail)
        return msg


router = APIRouter()

# Routes that accept server-local filesystem paths.  These are registered
# ONLY in local auth mode — in multiuser the loopback-peer check is
# worthless behind Cloudflare, so we structurally exclude them.
local_only_router = APIRouter()

# Chunk size for the off-event-loop file copy.
_COPY_CHUNK = 65536  # 64 KiB


class _FileSizeExceeded(Exception):
    """Raised inside the thread worker when per-file byte limit is exceeded."""


def _copy_chunked(src_file: IO[bytes], dest: Path, max_bytes: int) -> None:
    """Synchronous chunk copy; raises ``_FileSizeExceeded`` on over-limit.

    Runs inside ``asyncio.to_thread`` so it never blocks the event loop.
    ``src_file`` must expose a ``read(n)`` method (SpooledTemporaryFile).
    """
    total = 0
    try:
        with dest.open("wb") as dst:
            while True:
                chunk = src_file.read(_COPY_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise _FileSizeExceeded()
                dst.write(chunk)
    except _FileSizeExceeded:
        dest.unlink(missing_ok=True)
        raise


class DetectRequest(BaseModel):
    path: str


class ImportPathRequest(BaseModel):
    sources: list[ImportSource]
    profile_id: int | None = None


class JobResponse(BaseModel):
    job_id: str


def _get_upload_limits() -> tuple[int, int, int]:
    """Return (max_upload_bytes, max_upload_files, max_file_bytes) from config.

    Falls back to the config.py env-var defaults (SNORE_MAX_UPLOAD_BYTES=2 GiB,
    SNORE_MAX_UPLOAD_FILES=10 000, SNORE_MAX_FILE_BYTES=256 MiB) when config is
    not yet initialised — e.g. unit tests that exercise routes without a lifespan.
    """
    try:
        from snore.api.config import get_config  # noqa: PLC0415

        cfg = get_config()
        return cfg.max_upload_bytes, cfg.max_upload_files, cfg.max_file_bytes
    except Exception:
        return 2 * 1024 * 1024 * 1024, 10_000, 256 * 1024 * 1024


def _require_localhost(request: Request) -> None:
    # is_loopback covers 127.0.0.0/8, ::1, and IPv4-mapped ::ffff:127.0.0.1
    # (seen when uvicorn binds dual-stack).
    client_host = request.client.host if request.client else None
    try:
        is_local = (
            client_host is not None and ipaddress.ip_address(client_host).is_loopback
        )
    except ValueError:
        is_local = False
    if not is_local:
        raise HTTPException(
            status_code=403, detail="Filesystem access restricted to localhost"
        )


async def _resolve_profile_id(
    db: AsyncSession,
    actor: ActorContext,
    requested_id: int | None,
) -> int:
    """Return the resolved profile ID, validating ownership when *requested_id* is given.

    Raises HTTP 403 when the requested profile does not belong to the actor or
    is being deleted.  Falls back to the actor's active profile when *requested_id*
    is None.
    """
    if requested_id is not None:
        owned = (
            (
                await db.execute(
                    select(models.Profile).where(
                        models.Profile.id == requested_id,
                        models.Profile.user_id == actor.user_id,
                        models.Profile.deleting_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if owned is None:
            raise HTTPException(status_code=403, detail="Profile not owned by user")
        return requested_id
    return actor.profile_id


@local_only_router.post("/detect", response_model=list[ImportSource])
def detect_sources(body: DetectRequest, request: Request) -> list[ImportSource]:
    _require_localhost(request)
    service = ImportService()
    return service.detect_sources(Path(body.path))


@router.post(
    "/",
    response_model=JobResponse,
    status_code=202,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["files"],
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            },
                            "profile_id": {
                                "type": "integer",
                                "description": "Target profile ID (defaults to actor's active profile)",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def import_files(
    request: Request,
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    max_upload_bytes, max_upload_files, max_file_bytes = _get_upload_limits()

    # Step 1: Reserve admission slot BEFORE reading any body bytes.
    job = reserve_slot(actor.user_id)
    if job is None:
        raise HTTPException(
            status_code=429,
            detail="Too many active imports. Please wait for existing imports to complete.",
        )

    # Wrap the ASGI receive callable with the ingress byte ceiling.
    # This raises 413 as soon as the cumulative chunk stream exceeds the
    # limit — before Starlette's multipart parser spools to temp files.
    ceiling_receive = _ByteCeilingReceive(
        request.receive,
        max_upload_bytes,
        detail=f"Upload exceeds the {max_upload_bytes // (1024**2)} MiB per-upload limit",
    )
    request._receive = ceiling_receive  # noqa: SLF001

    # _job_cleanup: True until the job is handed to the worker.  The finally
    # block uses this flag to decide whether to clean up the job.
    _job_cleanup = True
    tmp: str | None = None
    _requested_profile_id: int | None = None

    try:
        async with request.form(max_files=max_upload_files) as form:
            _profile_id_raw = form.get("profile_id")
            if isinstance(_profile_id_raw, str):
                try:
                    _requested_profile_id = int(_profile_id_raw)
                except ValueError:
                    raise HTTPException(
                        status_code=422, detail="profile_id must be an integer"
                    ) from None
            uploads = [
                f for f in form.getlist("files") if isinstance(f, StarletteUploadFile)
            ]
            if not uploads:
                raise HTTPException(status_code=422, detail="No files provided")
            # Per-file limit: reject any individual file exceeding the cap.
            oversized = [f for f in uploads if (f.size or 0) > max_file_bytes]
            if oversized:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"One or more files exceed the "
                        f"{max_file_bytes // (1024**2)} MiB per-file limit"
                    ),
                )
            # Defense-in-depth: also check post-spool total size (catches absent/lying CL).
            total_size = sum(f.size or 0 for f in uploads)
            if total_size > max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload size exceeds {max_upload_bytes // (1024**2)} MiB limit",
                )

            tmp = tempfile.mkdtemp(prefix="snore-upload-")
            tmp_path = Path(tmp)
            tmp_root = tmp_path.resolve()
            for upload in uploads:
                filename = upload.filename or "unknown"
                rel = safe_relative_path(filename) or "unknown"
                dest = tmp_path / rel
                if not dest.resolve().is_relative_to(tmp_root):
                    logger.warning("Skipping file with unsafe path: %r", filename)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Run copy in a thread so the event loop stays responsive.
                # asyncio.create_task wraps the coroutine in a task that can be
                # individually awaited; asyncio.shield lets us wait for it even
                # under task cancellation so the copy thread runs to completion
                # before we clean up.
                copy_task = asyncio.create_task(
                    asyncio.to_thread(_copy_chunked, upload.file, dest, max_file_bytes)
                )
                try:
                    await asyncio.shield(copy_task)
                except asyncio.CancelledError:
                    # Request cancelled.  Loop the shielded wait until the copy
                    # task is truly done so cleanup never races a running write.
                    # The loop is robust to repeated cancellations: each inner
                    # CancelledError is absorbed and the loop re-tests .done().
                    while not copy_task.done():
                        try:
                            await asyncio.shield(copy_task)
                        except (asyncio.CancelledError, Exception):
                            pass
                    raise  # propagates to finally → cleanup
                except _FileSizeExceeded:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File '{filename}' exceeds the "
                            f"{max_file_bytes // (1024**2)} MiB per-file limit"
                        ),
                    ) from None
                await upload.close()

        # Resolve target profile: validate ownership if caller specified one.
        resolved_profile_id = await _resolve_profile_id(
            db, actor, _requested_profile_id
        )

        # Transfer ownership to the job; the worker will clean up on completion.
        job.temp_dir = tmp_path
        job.set_file_count(len(uploads))
        tmp = None  # Job owns the directory now.
        job.convert_to_pending()
        job.target_profile_id = resolved_profile_id
        _job_cleanup = False  # Worker owns the job from here.

    finally:
        # Runs on every exit: normal (no-op), HTTPException, Exception,
        # CancelledError.  _job_cleanup is False only when the worker started.
        if _job_cleanup:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
            job.try_cancel()
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()

    # Derive profile-scoped backup root from the resolved target profile.
    from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

    profile_raw_root = DEFAULT_RAW_BACKUP_DIR / str(job.target_profile_id)

    # Enqueue for serial execution — /progress is observer-only.
    enqueue_for_execution(job, profile_raw_root)
    return JobResponse(job_id=job.job_id)


@local_only_router.post("/path", response_model=JobResponse, status_code=202)
async def import_from_path(
    body: ImportPathRequest,
    request: Request,
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    _require_localhost(request)

    resolved_profile_id = await _resolve_profile_id(db, actor, body.profile_id)

    try:
        job = create_job(
            JobType.PATH, owner_user_id=actor.user_id, sources=body.sources
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail="Too many active imports.") from exc

    job.target_profile_id = resolved_profile_id
    job.set_file_count(len(body.sources))

    from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

    profile_raw_root = DEFAULT_RAW_BACKUP_DIR / str(resolved_profile_id)

    enqueue_for_execution(job, profile_raw_root)
    return JobResponse(job_id=job.job_id)


def _derive_stage(
    import_state: JobState,
    analysis_job_id: str | None,
    analysis_queued: bool | None,
    linked: LinkedAnalysisSummary | None,
) -> str:
    """Map import + analysis state to a human-readable pipeline stage string.

    ``linked`` is a LinkedAnalysisSummary instance or None (reaped / not yet set).
    """
    if import_state == JobState.PENDING_UPLOAD:
        return "uploading"
    if import_state == JobState.PENDING:
        return "queued"
    if import_state == JobState.RUNNING:
        return "importing"
    if import_state == JobState.FAILED:
        return "failed"
    if import_state == JobState.CANCELLED:
        return "cancelled"
    # SUCCEEDED — determine analysis stage.
    if analysis_job_id is None and analysis_queued is False:
        return "analysis_skipped"
    if analysis_job_id is None:
        return "done"
    if linked is None:
        # Analysis job was reaped after the import job stored the link.
        return "done"
    _state_map = {
        "queued": "analysis_queued",
        "running": "analyzing",
        "succeeded": "done",
        "failed": "analysis_failed",
        "cancelled": "analysis_cancelled",
    }
    return _state_map.get(linked.state, "unknown")


def _to_import_result_summary(result_dict: dict[str, Any]) -> ImportResultSummary:
    """Build an ImportResultSummary from a raw import-result dict, stripping session IDs."""
    sources = [
        ImportSourceResultSummary(
            source=s.get("source", {}),
            imported=s.get("imported", 0),
            skipped=s.get("skipped", 0),
            failed=s.get("failed", 0),
            warnings=s.get("warnings", []),
        )
        for s in result_dict.get("sources", [])
    ]
    return ImportResultSummary(
        total_imported=result_dict.get("total_imported", 0),
        total_skipped=result_dict.get("total_skipped", 0),
        total_failed=result_dict.get("total_failed", 0),
        warnings=result_dict.get("warnings", []),
        sources=sources,
    )


@router.get("/jobs", response_model=PipelineJobsListResponse)
def list_pipeline_jobs(actor: RequireAuth) -> PipelineJobsListResponse:
    """List all pipeline (import) jobs visible to the authenticated actor.

    Ownership rule: jobs with owner_user_id=None are visible to any authenticated
    user (local-mode parity); jobs with a set owner are visible only to that owner.
    Foreign jobs return 404 on direct access but are simply absent from this list.

    TTL note: both stores reap terminal jobs after 600s independently — a reaped
    analysis job degrades the stage to "done"; a reaped import job drops the row
    while its analysis job may still appear in GET /analysis/jobs.
    """
    from snore.api import analysis_jobs as aj_module  # noqa: PLC0415

    result: list[PipelineJobStatus] = []
    for job in list_jobs(owner_user_id=actor.user_id):
        analysis_id = job.analysis_job_id
        linked: LinkedAnalysisSummary | None = None
        if analysis_id is not None:
            aj = aj_module.get_job(analysis_id)
            if aj is not None:
                # Defensive: analysis job should always inherit the same owner as the
                # parent import job (both are set from actor.user_id at enqueue time),
                # but guard explicitly so the invariant is self-documenting.
                if aj.owner_user_id is not None and aj.owner_user_id != actor.user_id:
                    aj = None
            if aj is not None:
                linked = LinkedAnalysisSummary(
                    job_id=aj.job_id,
                    state=aj.state.value,
                    progress_completed=aj.progress_completed,
                    progress_total=aj.progress_total,
                    error_message=aj.error_message,
                )

        import_state = job.state
        stage = _derive_stage(import_state, analysis_id, job.analysis_queued, linked)

        import_result_summary: ImportResultSummary | None = None
        snapshot = job.import_result_snapshot
        if snapshot is not None:
            import_result_summary = _to_import_result_summary(snapshot)

        result.append(
            PipelineJobStatus(
                job_id=job.job_id,
                job_type=job.job_type.value,
                state=import_state.value,
                stage=stage,
                file_count=job.file_count,
                created_at=job.created_at,
                finished_at=job.finished_at,
                progress_message=job.latest_progress_message,
                sessions_imported=job.sessions_imported,
                import_result=import_result_summary,
                error_message=job.error_message,
                analysis_job_id=analysis_id,
                analysis_queued=job.analysis_queued,
                linked_analysis=linked,
            )
        )

    result.sort(key=lambda j: j.created_at, reverse=True)
    return PipelineJobsListResponse(jobs=result)


@router.delete("/{job_id}", status_code=204)
def cancel_import(job_id: str, actor: RequireWritable) -> None:
    """Cancel an import job.

    Requires write access and ownership of the job. Returns 404 for foreign jobs
    (no information leak about other users' job IDs).

    Jobs without an owner (owner_user_id=None) are accessible in local mode.
    """
    job = get_job(job_id)
    if job is None or (
        job.owner_user_id is not None and job.owner_user_id != actor.user_id
    ):
        # 404 instead of 403 — no information about foreign job IDs.
        raise HTTPException(status_code=404, detail="Import job not found")
    cancel_job(job_id)


_SSE_TIMEOUT = object()


async def _sse_generator(job: ImportJob) -> AsyncGenerator[str]:
    loop = asyncio.get_running_loop()
    ch = job.attach_observer()

    def _get_or_sentinel() -> object:
        msg = ch.get(timeout=1.0)
        return _SSE_TIMEOUT if msg is None and not ch._closed else msg

    try:
        while True:
            try:
                msg = await asyncio.wait_for(
                    loop.run_in_executor(None, _get_or_sentinel),
                    timeout=2.0,
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue

            if msg is _SSE_TIMEOUT:
                yield ": keepalive\n\n"
                continue

            if msg is None:
                break

            assert isinstance(msg, dict)
            event_type = msg.get("event", "progress")
            data = json.dumps(msg.get("data", {}))
            yield f"event: {event_type}\ndata: {data}\n\n"

            if event_type in ("complete", "error"):
                break
    finally:
        job.detach_observer(ch)


@router.get("/{job_id}/progress")
async def import_progress(job_id: str, actor: ActorDep) -> StreamingResponse:
    """Attach an SSE observer to an existing job. Never starts/restarts the worker.

    Returns 404 for foreign jobs (no information leak about other users' job IDs).
    Jobs without an owner (owner_user_id=None) are accessible in local mode.
    """
    job = get_job(job_id)
    if job is None or (
        job.owner_user_id is not None and job.owner_user_id != actor.user_id
    ):
        raise HTTPException(status_code=404, detail="Import job not found")

    return StreamingResponse(
        _sse_generator(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
