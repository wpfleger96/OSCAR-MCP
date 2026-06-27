from __future__ import annotations

import tempfile

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.services.export_service import ExportService

router = APIRouter()


@router.get("/csv")
def export_csv(
    db: Session = Depends(get_db),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    device: str | None = Query(default=None),
    include_waveforms: bool = Query(default=False),
) -> StreamingResponse:
    svc = ExportService()
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "export.csv"
        svc.export_csv(
            db,
            output,
            date_from=from_date,
            date_to=to_date,
            device_serial=device,
            include_waveforms=include_waveforms,
        )
        content = output.read_bytes()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=snore_export.csv"},
    )


@router.get("/json")
def export_json(
    db: Session = Depends(get_db),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    device: str | None = Query(default=None),
) -> StreamingResponse:
    svc = ExportService()
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "export.json"
        svc.export_json(
            db,
            output,
            date_from=from_date,
            date_to=to_date,
            device_serial=device,
        )
        content = output.read_bytes()
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=snore_export.json"},
    )


@router.get("/raw")
def export_raw(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    device: str | None = Query(default=None),
    trim_str: bool = Query(default=False),
    as_zip: bool = Query(default=True),
) -> StreamingResponse:
    svc = ExportService()
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "snore_export_raw.zip"
        result = svc.export_raw(
            output,
            date_from=from_date,
            date_to=to_date,
            device_serial=device,
            trim_str=trim_str,
            as_zip=as_zip,
        )
        content = result.output_path.read_bytes()
    return StreamingResponse(
        iter([content]),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=snore_export_raw.zip"},
    )
