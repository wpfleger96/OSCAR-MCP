from __future__ import annotations

import ipaddress

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

from snore.services.import_service import ImportService
from snore.services.schemas import ImportResult, ImportSource

router = APIRouter()

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB
MAX_UPLOAD_FILES = 20_000


class DetectRequest(BaseModel):
    path: str


class ImportPathRequest(BaseModel):
    sources: list[ImportSource]


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
    response_model=ImportResult,
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
async def import_files(request: Request) -> ImportResult:
    async with request.form(max_files=MAX_UPLOAD_FILES) as form:
        # The multipart parser yields starlette.datastructures.UploadFile instances.
        # fastapi.UploadFile is a subclass, so isinstance against the Starlette base
        # catches both while excluding plain string form fields.
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
        file_inputs = [(f.filename or "unknown", f.file) for f in uploads]
        service = ImportService()
        # run_in_threadpool is called inside the async with block so spooled temp
        # files remain open for the duration of the synchronous service call.
        return await run_in_threadpool(service.import_from_upload, file_inputs)


@router.post("/path", response_model=ImportResult)
def import_from_path(body: ImportPathRequest, request: Request) -> ImportResult:
    _require_localhost(request)
    service = ImportService()
    return service.import_sources(body.sources, backup=True)
