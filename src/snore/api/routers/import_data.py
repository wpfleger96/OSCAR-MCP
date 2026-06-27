from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from snore.api.deps import service_dep
from snore.services.import_service import ImportService
from snore.services.schemas import ImportResult, ImportSource

router = APIRouter()

ImportServiceDep = Annotated[ImportService, Depends(service_dep(ImportService))]


class DetectRequest(BaseModel):
    path: str


@router.post("/detect", response_model=list[ImportSource])
def detect_sources(
    body: DetectRequest, service: ImportServiceDep
) -> list[ImportSource]:
    return service.detect_sources(Path(body.path))


@router.post("/", response_model=ImportResult)
async def import_files(
    service: ImportServiceDep,
    files: list[UploadFile] = File(...),
) -> ImportResult:
    uploads = [(file.filename or "unknown", await file.read()) for file in files]
    return service.import_from_upload(uploads)
