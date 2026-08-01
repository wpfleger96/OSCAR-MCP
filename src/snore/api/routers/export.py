from __future__ import annotations

# ExportService uses a filesystem-oriented constructor (backup_root, not db_session)
# and doesn't fit the service_dep() DI pattern. DB sessions are passed per-method call.
import shutil
import tempfile

from collections.abc import Generator
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from snore.api.deps import sync_get_db
from snore.services.export_service import ExportService

router = APIRouter()


def _stream_file(path: Path, chunk_size: int = 64 * 1024) -> Generator[bytes]:
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


def _streaming_export(
    tmpdir: str,
    output_path: Path,
    media_type: str,
    filename: str,
) -> StreamingResponse:
    return StreamingResponse(
        _stream_file(output_path),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
        background=BackgroundTask(shutil.rmtree, tmpdir),
    )


@router.get("/csv")
def export_csv(
    db: Session = Depends(sync_get_db),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    device: str | None = Query(default=None),
    include_waveforms: bool = Query(default=False),
) -> StreamingResponse:
    svc = ExportService()
    tmpdir = tempfile.mkdtemp()
    output = Path(tmpdir) / "export.csv"
    svc.export_csv(
        db,
        output,
        date_from=from_date,
        date_to=to_date,
        device_serial=device,
        include_waveforms=include_waveforms,
    )
    return _streaming_export(tmpdir, output, "text/csv", "snore_export.csv")


@router.get("/json")
def export_json(
    db: Session = Depends(sync_get_db),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    device: str | None = Query(default=None),
) -> StreamingResponse:
    svc = ExportService()
    tmpdir = tempfile.mkdtemp()
    output = Path(tmpdir) / "export.json"
    svc.export_json(
        db,
        output,
        date_from=from_date,
        date_to=to_date,
        device_serial=device,
    )
    return _streaming_export(tmpdir, output, "application/json", "snore_export.json")


@router.get("/raw")
def export_raw(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    device: str | None = Query(default=None),
    trim_str: bool = Query(default=False),
    as_zip: bool = Query(default=True),
) -> StreamingResponse:
    svc = ExportService()
    tmpdir = tempfile.mkdtemp()
    output = Path(tmpdir) / "snore_export_raw.zip"
    result = svc.export_raw(
        output,
        date_from=from_date,
        date_to=to_date,
        device_serial=device,
        trim_str=trim_str,
        as_zip=as_zip,
    )
    return _streaming_export(
        tmpdir, result.output_path, "application/zip", "snore_export_raw.zip"
    )
