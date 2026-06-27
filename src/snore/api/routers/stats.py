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


@router.get("/trends", response_model=dict[str, list[list[Any]]])
def get_trends(
    service: StatsServiceDep,
    period_type: Literal["week", "month", "6month", "year"] = Query(default="month"),
    days_limit: int | None = Query(default=None),
) -> Any:
    # Service returns dict[str, list[tuple[date, float | None]]]; tuples serialize as
    # JSON arrays, so response_model=dict[str, list[list[Any]]] reflects the wire shape.
    period_stats = service.get_period_statistics(period_type, days_limit)
    return service.get_trends(period_stats)


@router.get("/records", response_model=dict[str, dict[str, list[list[Any]]]])
def get_records(
    service: StatsServiceDep,
    days_limit: int | None = Query(default=None),
    top_n: int = Query(default=5),
) -> Any:
    # Service returns dict[str, dict[str, list[tuple[date, float]]]]; tuples serialize
    # as JSON arrays, so response_model=dict[str, dict[str, list[list[Any]]]] reflects
    # the wire shape.
    return service.get_records(days_limit, top_n)
