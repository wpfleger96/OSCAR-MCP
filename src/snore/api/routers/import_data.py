from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
import tempfile
import threading

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

from snore.api.deps import ActorDep
from snore.api.import_jobs import (
    ImportJob,
    JobType,
    cancel_job,
    create_job,
    get_job,
    remove_job,
    reserve_slot,
)
from snore.services.import_service import ImportService, safe_relative_path
from snore.services.schemas import ImportSource

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB per upload (configurable via env)
MAX_UPLOAD_FILES = 500  # sane default; 20k was unreasonably high


class DetectRequest(BaseModel):
    path: str


class ImportPathRequest(BaseModel):
    sources: list[ImportSource]


class JobResponse(BaseModel):
    job_id: str


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


@router.post("/detect", response_model=list[ImportSource])
def detect_sources(body: DetectRequest, request: Request) -> list[ImportSource]:
    _require_localhost(request)
    service = ImportService()
    return service.detect_sources(Path(body.path))


def _run_import(job: ImportJob, profile_raw_root: Path | None = None) -> None:
    """Worker function — runs in a background thread. Must be started exactly once.

    Ordering contract:
        1. Do the import work.
        2. Publish terminal state (for SSE observers).
        3. Clean parser spool + job temp.
        4. Release capacity (slot owns the disk it admitted).
    """
    import asyncio  # noqa: PLC0415

    try:
        service = ImportService()
        # Consume the snapshotted target_profile_id so DB writes land in the
        # correct profile even if the default profile changes between job creation
        # and worker execution.
        target_profile_id = job.target_profile_id
        if job.job_type == JobType.UPLOAD and job.temp_dir is not None:
            job.report_progress("Detecting data sources...")
            if job.cancel_requested:
                job._finish_cancelled()
                return
            sources = service.detect_sources(job.temp_dir)
            job.report_progress(f"Detected {len(sources)} source(s)")
            if job.cancel_requested:
                job._finish_cancelled()
                return
            result = asyncio.run(
                service.import_sources(
                    sources,
                    backup=True,
                    backup_root=profile_raw_root,
                    profile_id=target_profile_id,
                    progress_callback=lambda msg: job.report_progress(msg),
                    cancel_predicate=lambda: job.cancel_requested,
                )
            )
        elif job.job_type == JobType.PATH and job.sources is not None:
            result = asyncio.run(
                service.import_sources(
                    job.sources,
                    backup=True,
                    backup_root=profile_raw_root,
                    profile_id=target_profile_id,
                    progress_callback=lambda msg: job.report_progress(msg),
                    cancel_predicate=lambda: job.cancel_requested,
                )
            )
        else:
            raise ValueError("Invalid job configuration")

        if job.cancel_requested:
            job._finish_cancelled()
            return

        terminal_msg = {"event": "complete", "data": {"result": result.model_dump()}}
        job._finish(succeeded=True, terminal_msg=terminal_msg)
    except Exception as e:
        logger.exception("Import job %s failed", job.job_id)
        job._finish(
            succeeded=False,
            terminal_msg={"event": "error", "data": {"message": str(e)}},
        )
    finally:
        # Ordering: publish terminal (done above), then clean, then release capacity.
        job.cleanup_files()
        job.release_capacity()


def _start_worker(job: ImportJob, profile_raw_root: Path | None = None) -> None:
    """Attempt to start the worker thread for *job* (start-once guarantee)."""
    from snore.api.import_jobs import remove_job  # noqa: PLC0415

    if not job.try_start():
        return
    try:
        t = threading.Thread(
            target=_run_import,
            args=(job, profile_raw_root),
            daemon=True,
            name=f"import-{job.job_id}",
        )
        with job._lock:
            job._worker_thread = t
        t.start()
    except Exception:
        job._finish_cancelled()
        remove_job(job.job_id)
        job.cleanup_files()
        job.release_capacity()
        raise


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
                            }
                        },
                    }
                }
            },
        }
    },
)
async def import_files(request: Request) -> JobResponse:
    # Step 1: Reserve admission slot BEFORE reading any body bytes.
    owner_user_id: int | None = None
    actor = getattr(request.state, "actor", None)
    if actor is not None:
        owner_user_id = actor.user_id

    job = reserve_slot(owner_user_id)
    if job is None:
        raise HTTPException(
            status_code=429,
            detail="Too many active imports. Please wait for existing imports to complete.",
        )

    tmp: str | None = None
    try:
        async with request.form(max_files=MAX_UPLOAD_FILES) as form:
            uploads = [
                f for f in form.getlist("files") if isinstance(f, StarletteUploadFile)
            ]
            if not uploads:
                raise HTTPException(status_code=422, detail="No files provided")
            total_size = sum(f.size or 0 for f in uploads)
            if total_size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload size exceeds {MAX_UPLOAD_BYTES // (1024**2)} MiB limit",
                )

            tmp = tempfile.mkdtemp()
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
                content = await upload.read()
                dest.write_bytes(content)

        job.temp_dir = tmp_path
        tmp = None  # Job owns the directory now.
        job.convert_to_pending()
        target_profile_id = getattr(actor, "profile_id", None) if actor else None
        job.target_profile_id = target_profile_id
    except HTTPException:
        # Release capacity before re-raising: job is abandoned.
        job.try_cancel()
        remove_job(job.job_id)
        job.cleanup_files()
        job.release_capacity()
        raise
    except Exception:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        job.try_cancel()
        remove_job(job.job_id)
        job.cleanup_files()
        job.release_capacity()
        raise

    # Derive profile-scoped backup root from actor.
    profile_raw_root: Path | None = None
    if actor is not None:
        from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

        profile_raw_root = DEFAULT_RAW_BACKUP_DIR / str(actor.profile_id)

    # Start worker immediately — /progress is observer-only.
    _start_worker(job, profile_raw_root)
    return JobResponse(job_id=job.job_id)


@router.post("/path", response_model=JobResponse, status_code=202)
def import_from_path(body: ImportPathRequest, request: Request) -> JobResponse:
    _require_localhost(request)
    actor = getattr(request.state, "actor", None)
    owner_user_id: int | None = getattr(actor, "user_id", None) if actor else None
    target_profile_id: int | None = (
        getattr(actor, "profile_id", None) if actor else None
    )

    try:
        job = create_job(
            JobType.PATH, owner_user_id=owner_user_id, sources=body.sources
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail="Too many active imports.") from exc

    job.target_profile_id = target_profile_id
    profile_raw_root: Path | None = None
    if target_profile_id is not None:
        from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

        profile_raw_root = DEFAULT_RAW_BACKUP_DIR / str(target_profile_id)

    _start_worker(job, profile_raw_root)
    return JobResponse(job_id=job.job_id)


@router.delete("/{job_id}", status_code=204)
def cancel_import(job_id: str, actor: ActorDep) -> None:
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
    if not actor.can_write:
        raise HTTPException(status_code=403, detail="Write access required")
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
