from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
import tempfile
import threading

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
from snore.api.guards import RequireWritable
from snore.api.import_jobs import (
    ImportJob,
    JobPhase,
    JobType,
    cancel_job,
    create_job,
    get_job,
    remove_job,
    reserve_slot,
)
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
    """Return (max_upload_bytes, max_upload_files, max_file_bytes) from config with safe fallbacks."""
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


@local_only_router.post("/detect", response_model=list[ImportSource])
def detect_sources(body: DetectRequest, request: Request) -> list[ImportSource]:
    _require_localhost(request)
    service = ImportService()
    return service.detect_sources(Path(body.path))


def _run_import(job: ImportJob, profile_raw_root: Path | None = None) -> None:
    """Worker function — runs in a background thread. Must be started exactly once.

    Ordering contract:
        1. Do the import work.
        2. Call phase_complete(IMPORT) — non-terminal milestone for observers.
        3. Run analysis phase (session IDs from import result).
        4. Publish terminal state (always carries import_committed + import_result
           when data was committed, even on analysis failure or cancellation).
        5. Clean parser spool + job temp.
        6. Release capacity (slot owns the disk it admitted).
    """
    import asyncio  # noqa: PLC0415

    def _make_terminal(
        event: str,
        *,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a terminal payload, injecting import_committed + import_result
        when the import phase already committed."""
        data: dict[str, Any] = {}
        if message is not None:
            data["message"] = message
        if extra:
            data.update(extra)
        with job._lock:
            committed = job._import_committed
            import_result = job._import_result
        if committed:
            data["import_committed"] = True
            data["import_result"] = import_result
        return {"event": event, "data": data}

    try:
        service = ImportService()
        # Consume the snapshotted target_profile_id so DB writes land in the
        # correct profile even if the default profile changes between job creation
        # and worker execution.
        target_profile_id = job.target_profile_id
        if target_profile_id is None:
            raise ValueError("Import job has no target profile — cannot proceed")
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

        # --- Phase 1 complete: import committed ---
        import_result_dict = result.model_dump()
        job.phase_complete(JobPhase.IMPORT, import_result_dict)

        if job.cancel_requested:
            job._finish(
                succeeded=False,
                terminal_msg=_make_terminal("error", message="Cancelled"),
            )
            return

        # Enqueue background analysis for imported sessions, then immediately
        # emit terminal "complete" so the user can upload more files.
        analysis_job_id = None
        imported_ids = result.imported_session_ids
        if imported_ids:
            from snore.api import analysis_jobs  # noqa: PLC0415

            aj = analysis_jobs.enqueue(
                profile_id=target_profile_id,
                session_ids=imported_ids,
                source=analysis_jobs.AnalysisJobSource.IMPORT,
                owner_user_id=job.owner_user_id,
            )
            if aj is not None:
                analysis_job_id = aj.job_id
            else:
                logger.warning(
                    "Analysis queue full; skipping auto-analysis for import job %s",
                    job.job_id,
                )

        terminal_extra: dict[str, Any] = {"result": import_result_dict}
        if analysis_job_id is not None:
            terminal_extra["analysis_job_id"] = analysis_job_id
        elif imported_ids:
            # Queue was full — tell the client so it can distinguish from
            # "nothing was imported" (where analysis_queued is absent).
            terminal_extra["analysis_queued"] = False
        terminal_msg = _make_terminal("complete", extra=terminal_extra)
        job._finish(succeeded=True, terminal_msg=terminal_msg)
    except Exception as e:
        logger.exception("Import job %s failed", job.job_id)
        job._finish(
            succeeded=False,
            terminal_msg=_make_terminal("error", message=str(e)),
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

        # Resolve target profile: validate ownership if caller specified one.
        if _requested_profile_id is not None:
            owned = (
                (
                    await db.execute(
                        select(models.Profile).where(
                            models.Profile.id == _requested_profile_id,
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
            resolved_profile_id = _requested_profile_id
        else:
            resolved_profile_id = actor.profile_id

        # Transfer ownership to the job; the worker will clean up on completion.
        job.temp_dir = tmp_path
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

    # Start worker immediately — /progress is observer-only.
    _start_worker(job, profile_raw_root)
    return JobResponse(job_id=job.job_id)


@local_only_router.post("/path", response_model=JobResponse, status_code=202)
async def import_from_path(
    body: ImportPathRequest,
    request: Request,
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    _require_localhost(request)

    if body.profile_id is not None:
        owned = (
            (
                await db.execute(
                    select(models.Profile).where(
                        models.Profile.id == body.profile_id,
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
        resolved_profile_id = body.profile_id
    else:
        resolved_profile_id = actor.profile_id

    try:
        job = create_job(
            JobType.PATH, owner_user_id=actor.user_id, sources=body.sources
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail="Too many active imports.") from exc

    job.target_profile_id = resolved_profile_id

    from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

    profile_raw_root = DEFAULT_RAW_BACKUP_DIR / str(resolved_profile_id)

    _start_worker(job, profile_raw_root)
    return JobResponse(job_id=job.job_id)


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
