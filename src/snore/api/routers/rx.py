from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.rx_tracker import RxTracker
from snore.api.deps import get_db
from snore.api.schemas import (
    RxAllResponse,
    RxChangesResponse,
    RxComparisonResponse,
    RxPeriodResponse,
)

router = APIRouter()

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/history", response_model=list[RxPeriodResponse])
async def get_rx_history(db: DbDep) -> list[RxPeriodResponse]:
    return await RxTracker().get_history(db)


@router.get(
    "/current",
    response_model=None,
    responses={
        200: {"model": RxPeriodResponse},
        204: {"description": "No RX data available"},
    },
)
async def get_rx_current(db: DbDep) -> RxPeriodResponse | Response:
    result = await RxTracker().get_current(db)
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/compare", response_model=RxComparisonResponse)
async def compare_rx(
    db: DbDep,
    min_days: int = Query(default=7, ge=1),
) -> RxComparisonResponse:
    return await RxTracker().get_comparison(db, min_days)


@router.get("/all", response_model=RxAllResponse)
async def get_rx_all(
    db: DbDep,
    min_days: int = Query(default=7, ge=1),
) -> RxAllResponse:
    return await RxTracker().get_all(db, min_days)


@router.get("/changes", response_model=RxChangesResponse)
async def get_rx_changes(db: DbDep) -> RxChangesResponse:
    return await RxTracker().get_changes(db)
