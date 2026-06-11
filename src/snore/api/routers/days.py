from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from snore.api.deps import DateRangeParams, PaginationParams, service_dep
from snore.api.errors import NotFoundError
from snore.api.schemas import DayDetail, DayListItem, PaginatedResponse
from snore.services import DayService

router = APIRouter()

DayServiceDep = Annotated[DayService, Depends(service_dep(DayService))]


@router.get("/", response_model=PaginatedResponse[DayListItem])
def list_days(
    service: DayServiceDep,
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
    device_id: int | None = Query(default=None),
) -> PaginatedResponse[DayListItem]:
    items, total = service.list_days(
        from_date=dates.from_date,
        to_date=dates.to_date,
        device_id=device_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return PaginatedResponse(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/{day_date}", response_model=DayDetail)
def get_day(day_date: date, service: DayServiceDep) -> DayDetail:
    result = service.get_day(day_date)
    if result is None:
        raise NotFoundError(f"No data found for date {day_date}")
    return result
