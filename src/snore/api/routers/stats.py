from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.services import StatsService
from snore.services.schemas import PeriodStatistics, TherapySummary

router = APIRouter()


@router.get(
    "/summary",
    response_model=None,
    responses={
        200: {"model": TherapySummary},
        204: {"description": "No therapy data available"},
    },
)
def get_summary(
    days_limit: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> TherapySummary | Response:
    service = StatsService(db)
    result = service.get_summary(days_limit)
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/periods", response_model=list[PeriodStatistics])
def get_periods(
    period_type: Literal["week", "month", "6month", "year"] = Query(default="month"),
    days_limit: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[PeriodStatistics]:
    service = StatsService(db)
    return service.get_period_statistics(period_type, days_limit)


@router.get("/trends")
def get_trends(
    period_type: Literal["week", "month", "6month", "year"] = Query(default="month"),
    days_limit: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Any:
    service = StatsService(db)
    period_stats = service.get_period_statistics(period_type, days_limit)
    return service.get_trends(period_stats)


@router.get("/records")
def get_records(
    days_limit: int | None = Query(default=None),
    top_n: int = Query(default=5),
    db: Session = Depends(get_db),
) -> Any:
    service = StatsService(db)
    return service.get_records(days_limit, top_n)
