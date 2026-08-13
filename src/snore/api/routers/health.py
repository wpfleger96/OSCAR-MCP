from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from snore.api.deps import DateRangeParams, PaginationParams, service_dep
from snore.api.schemas import (
    DateListResponse,
    HealthNightDetailRead,
    HealthNightSummaryRead,
    HealthSampleRead,
    PaginatedResponse,
)
from snore.services import HealthService

router = APIRouter()

HealthServiceDep = Annotated[HealthService, Depends(service_dep(HealthService))]


@router.get("/nights/dates", response_model=DateListResponse)
async def list_night_dates(service: HealthServiceDep) -> DateListResponse:
    """Return all night dates that have Apple Health sleep data for this profile."""
    dates = await service.list_night_dates()
    return DateListResponse(dates=dates)


@router.get("/nights", response_model=PaginatedResponse[HealthNightSummaryRead])
async def list_nights(
    service: HealthServiceDep,
    pagination: PaginationParams = Depends(),
    dates: DateRangeParams = Depends(),
) -> PaginatedResponse[HealthNightSummaryRead]:
    """Return paginated nightly sleep summaries, most-recent first."""
    items, total = await service.list_nights(
        from_date=dates.from_date,
        to_date=dates.to_date,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return PaginatedResponse(
        items=items, total=total, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/nights/{night_date}", response_model=HealthNightDetailRead)
async def get_night_detail(
    night_date: date,
    service: HealthServiceDep,
) -> HealthNightDetailRead:
    """Return nightly sleep detail with aggregated SpO2 and respiratory rate metrics."""
    return await service.get_night_detail(night_date)


@router.get("/nights/{night_date}/samples", response_model=list[HealthSampleRead])
async def get_night_samples(
    night_date: date,
    service: HealthServiceDep,
    source_name: str | None = Query(default=None, max_length=200),
) -> list[HealthSampleRead]:
    """Return sleep-stage samples for the night ordered by start time.

    When source_name is omitted, samples are filtered to the night's preferred
    source (no filter if preferred_source is also unset).
    """
    return await service.get_night_samples(night_date, source_name=source_name)
