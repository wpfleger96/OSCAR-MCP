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

from snore.api.import_jobs import (
    ImportJob,
    JobType,
    cancel_job,
    create_job,
    get_job,
)
from snore.services.import_service import ImportService, safe_relative_path
from snore.services.schemas import ImportSource

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB
MAX_UPLOAD_FILES = 20_000


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


def _run_import(job: ImportJob) -> None:
    """Worker function — runs in a background thread.  Must be started exactly once."""
    try:
        service = ImportService()
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
            result = service.import_sources(
                sources,
                backup=True,
                progress_callback=lambda msg: job.report_progress(msg),
                cancel_predicate=lambda: job.cancel_requested,
            )
        elif job.job_type == JobType.PATH and job.sources is not None:
            result = service.import_sources(
                job.sources,
                backup=True,
                progress_callback=lambda msg: job.report_progress(msg),
                cancel_predicate=lambda: job.cancel_requested,
            )
        else:
            raise ValueError("Invalid job configuration")

        # Check cancellation after import_sources returns (it may have exited early).
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
        # Remove the upload temp directory at the end of worker execution.
        job.cleanup_files()


def _start_worker(job: ImportJob) -> None:
    """Attempt to start the worker thread for *job* (start-once guarantee).

    Wraps Thread construction, assignment, and start in one rollback-safe block.
    Any failure — construction OR start — cancels the job, removes it from the
    store, and cleans up the upload directory.  No orphaned RUNNING job is possible.
    """
    from snore.api.import_jobs import remove_job  # noqa: PLC0415

    if not job.try_start():
        return  # Already running or terminal.
    try:
        t = threading.Thread(
            target=_run_import, args=(job,), daemon=True, name=f"import-{job.job_id}"
        )
        with job._lock:
            job._worker_thread = t
        t.start()
    except Exception:
        # Thread construction or start failed — leave no orphaned RUNNING job.
        job._finish_cancelled()
        remove_job(job.job_id)
        job.cleanup_files()
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
                    detail=f"Total upload size exceeds {MAX_UPLOAD_BYTES // (1024**3)} GiB limit",
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

        # create_job may raise; if so, clean up the temp dir.
        job = create_job(JobType.UPLOAD, temp_dir=tmp_path)
        tmp = None  # Job owns the directory now.
    except HTTPException:
        raise
    except Exception:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        raise

    # Start worker immediately — /progress is observer-only.
    _start_worker(job)
    return JobResponse(job_id=job.job_id)


@router.post("/path", response_model=JobResponse, status_code=202)
def import_from_path(body: ImportPathRequest, request: Request) -> JobResponse:
    _require_localhost(request)
    job = create_job(JobType.PATH, sources=body.sources)
    _start_worker(job)
    return JobResponse(job_id=job.job_id)


@router.delete("/{job_id}", status_code=204)
def cancel_import(job_id: str) -> None:
    """Cancel an import job.  Idempotent — returns 204 whether or not the job existed."""
    cancel_job(job_id)


_SSE_TIMEOUT = object()  # Sentinel: poll timed out (not a closed channel)


async def _sse_generator(job: ImportJob) -> AsyncGenerator[str]:
    """SSE stream for one observer.  Attaches, drains events, then detaches.

    ``ObserverChannel.get()`` returns ``None`` when the channel is explicitly
    closed (job cancelled / shutdown).  A poll timeout returns ``_SSE_TIMEOUT``
    via the executor wrapper below — we emit a keepalive and continue polling.
    Only a ``None`` (closed channel) or a terminal event breaks the loop.
    """
    loop = asyncio.get_running_loop()
    ch = job.attach_observer()

    def _get_or_sentinel() -> object:
        """Return the next message, or _SSE_TIMEOUT on poll timeout."""
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
                # Poll timed out — channel is still open; send keepalive.
                yield ": keepalive\n\n"
                continue

            if msg is None:
                # Channel closed (job cancelled / shutdown).
                break

            # At this point msg is dict[str, Any] — not _SSE_TIMEOUT, not None.
            assert isinstance(msg, dict)
            event_type = msg.get("event", "progress")
            data = json.dumps(msg.get("data", {}))
            yield f"event: {event_type}\ndata: {data}\n\n"

            if event_type in ("complete", "error"):
                break
    finally:
        job.detach_observer(ch)


@router.get("/{job_id}/progress")
async def import_progress(job_id: str) -> StreamingResponse:
    """Attach an SSE observer to an existing job.  Never starts/restarts the worker."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Import job not found")

    return StreamingResponse(
        _sse_generator(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
