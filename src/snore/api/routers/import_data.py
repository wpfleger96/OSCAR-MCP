from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from snore.services.import_service import ImportService
from snore.services.schemas import ImportResult, ImportSource

router = APIRouter()

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB


class DetectRequest(BaseModel):
    path: str


@router.post("/detect", response_model=list[ImportSource])
def detect_sources(body: DetectRequest, request: Request) -> list[ImportSource]:
    # Filesystem access — restrict to localhost
    client_host = request.client.host if request.client else None
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403, detail="Filesystem access restricted to localhost"
        )
    service = ImportService()
    return service.detect_sources(Path(body.path))


@router.post("/", response_model=ImportResult)
async def import_files(
    files: list[UploadFile] = File(...),
) -> ImportResult:
    total_size = 0
    uploads: list[tuple[str, bytes]] = []
    for file in files:
        content = await file.read()
        total_size += len(content)
        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Total upload size exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
            )
        uploads.append((file.filename or "unknown", content))
    service = ImportService()
    return service.import_from_upload(uploads)
