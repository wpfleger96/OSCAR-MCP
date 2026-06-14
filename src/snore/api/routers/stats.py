from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response

from snore.api.deps import service_dep
from snore.services import StatsService
from snore.services.schemas import PeriodStatistics, TherapySummary

router = APIRouter()

StatsServiceDep = Annotated[StatsService, Depends(service_dep(StatsService))]


@router.get(
    "/summary",
    response_model=None,
    responses={
        200: {"model": TherapySummary},
        204: {"description": "No therapy data available"},
    },
)
def get_summary(
    service: StatsServiceDep,
    days_limit: int | None = Query(default=None),
) -> TherapySummary | Response:
    result = service.get_summary(days_limit)
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/periods", response_model=list[PeriodStatistics])
def get_periods(
    service: StatsServiceDep,
    period_type: Literal["week", "month", "6month", "year"] = Query(default="month"),
    days_limit: int | None = Query(default=None),
) -> list[PeriodStatistics]:
    return service.get_period_statistics(period_type, days_limit)


@router.get("/trends")
def get_trends(
    service: StatsServiceDep,
    period_type: Literal["week", "month", "6month", "year"] = Query(default="month"),
    days_limit: int | None = Query(default=None),
) -> Any:
    period_stats = service.get_period_statistics(period_type, days_limit)
    return service.get_trends(period_stats)


@router.get("/records")
def get_records(
    service: StatsServiceDep,
    days_limit: int | None = Query(default=None),
    top_n: int = Query(default=5),
) -> Any:
    return service.get_records(days_limit, top_n)
