from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from snore.analysis.calculations import PeriodType
from snore.api.deps import service_dep
from snore.services import StatsService
from snore.services.schemas import DataRange, PeriodStatistics, TherapySummary

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
async def get_summary(
    service: StatsServiceDep,
    days_limit: int | None = Query(default=None),
) -> TherapySummary | Response:
    result = await service.get_summary(days_limit)
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/periods", response_model=list[PeriodStatistics])
async def get_periods(
    service: StatsServiceDep,
    period_type: PeriodType = Query(default="month"),
    days_limit: int | None = Query(default=None),
) -> list[PeriodStatistics]:
    return await service.get_period_statistics(period_type, days_limit)


@router.get("/trends", response_model=dict[str, list[list[Any]]])
async def get_trends(
    service: StatsServiceDep,
    period_type: PeriodType = Query(default="month"),
    days_limit: int | None = Query(
        default=None,
        description=(
            "Limit to last N days. For period_type=day, defaults to 180 when omitted "
            "to keep the response size reasonable."
        ),
    ),
) -> Any:
    # Service returns dict[str, list[tuple[date, float | None]]]; tuples serialize as
    # JSON arrays, so response_model=dict[str, list[list[Any]]] reflects the wire shape.
    if period_type == "day" and days_limit is None:
        days_limit = 180
    return await service.get_trends(period_type, days_limit)


@router.get("/data-range", response_model=DataRange)
async def get_data_range(service: StatsServiceDep) -> DataRange:
    return await service.get_data_range()


@router.get("/records", response_model=dict[str, dict[str, list[list[Any]]]])
async def get_records(
    service: StatsServiceDep,
    days_limit: int | None = Query(default=None),
    top_n: int = Query(default=5),
) -> Any:
    # Service returns dict[str, dict[str, list[tuple[date, float]]]]; tuples serialize
    # as JSON arrays, so response_model=dict[str, dict[str, list[list[Any]]]] reflects
    # the wire shape.
    return await service.get_records(days_limit, top_n)
