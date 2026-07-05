from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
import tempfile

from collections.abc import AsyncGenerator
from pathlib import Path
from queue import Empty

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

from snore.api.import_jobs import ImportJob, JobType, create_job, get_job, remove_job
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

        job = create_job(JobType.UPLOAD, temp_dir=tmp_path)
        return JobResponse(job_id=job.job_id)


@router.post("/path", response_model=JobResponse, status_code=202)
def import_from_path(body: ImportPathRequest, request: Request) -> JobResponse:
    _require_localhost(request)
    job = create_job(JobType.PATH, sources=body.sources)
    return JobResponse(job_id=job.job_id)


def _run_import(job: ImportJob) -> None:
    def progress_callback(msg: str) -> None:
        job.progress_queue.put({"event": "progress", "data": {"message": msg}})

    try:
        service = ImportService()
        if job.job_type == JobType.UPLOAD and job.temp_dir is not None:
            progress_callback("Detecting data sources...")
            sources = service.detect_sources(job.temp_dir)
            progress_callback(f"Detected {len(sources)} source(s)")
            result = service.import_sources(
                sources, backup=True, progress_callback=progress_callback
            )
        elif job.job_type == JobType.PATH and job.sources is not None:
            result = service.import_sources(
                job.sources, backup=True, progress_callback=progress_callback
            )
        else:
            raise ValueError("Invalid job configuration")

        job.progress_queue.put(
            {"event": "complete", "data": {"result": result.model_dump()}}
        )
    except Exception as e:
        logger.exception("Import job %s failed", job.job_id)
        job.progress_queue.put({"event": "error", "data": {"message": str(e)}})


async def _sse_generator(job: ImportJob) -> AsyncGenerator[str]:
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(
                    loop.run_in_executor(None, job.progress_queue.get, True, 1.0),
                    timeout=2.0,
                )
            except (TimeoutError, Empty):
                yield ": keepalive\n\n"
                continue

            event_type = msg.get("event", "progress")
            data = json.dumps(msg.get("data", {}))
            yield f"event: {event_type}\ndata: {data}\n\n"

            if event_type in ("complete", "error"):
                break
    finally:
        if job.temp_dir is not None:
            shutil.rmtree(job.temp_dir, ignore_errors=True)
        remove_job(job.job_id)


@router.get("/{job_id}/progress")
async def import_progress(job_id: str) -> StreamingResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Import job not found")

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_import, job)

    return StreamingResponse(
        _sse_generator(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
