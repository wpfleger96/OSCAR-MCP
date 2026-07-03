from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from snore.api.deps import service_dep
from snore.services import ReportService

router = APIRouter()

ReportServiceDep = Annotated[ReportService, Depends(service_dep(ReportService))]


@router.get("/summary")
def get_summary_report(
    service: ReportServiceDep,
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
) -> HTMLResponse:
    if from_date > to_date:
        raise HTTPException(
            status_code=422, detail="from_date must not be after to_date"
        )
    html = service.generate_summary_report(from_date, to_date)
    filename = f"snore-report-summary-{from_date}-{to_date}.html"
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/comparison")
def get_comparison_report(
    service: ReportServiceDep,
    from_a: Annotated[date, Query()],
    to_a: Annotated[date, Query()],
    from_b: Annotated[date, Query()],
    to_b: Annotated[date, Query()],
) -> HTMLResponse:
    if from_a > to_a:
        raise HTTPException(status_code=422, detail="from_a must not be after to_a")
    if from_b > to_b:
        raise HTTPException(status_code=422, detail="from_b must not be after to_b")
    html = service.generate_comparison_report((from_a, to_a), (from_b, to_b))
    filename = f"snore-report-comparison-{from_a}-{to_a}-vs-{from_b}-{to_b}.html"
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
