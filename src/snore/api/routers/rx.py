from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from snore.analysis.rx_tracker import RxTracker
from snore.api.deps import get_db
from snore.api.schemas import (
    RxAllResponse,
    RxChangesResponse,
    RxComparisonResponse,
    RxPeriodResponse,
)

router = APIRouter()


@router.get("/history", response_model=list[RxPeriodResponse])
def get_rx_history(db: Session = Depends(get_db)) -> list[RxPeriodResponse]:
    return RxTracker().get_history(db)


@router.get(
    "/current",
    response_model=None,
    responses={
        200: {"model": RxPeriodResponse},
        204: {"description": "No RX data available"},
    },
)
def get_rx_current(db: Session = Depends(get_db)) -> RxPeriodResponse | Response:
    result = RxTracker().get_current(db)
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/compare", response_model=RxComparisonResponse)
def compare_rx(
    min_days: int = Query(default=7, ge=1),
    db: Session = Depends(get_db),
) -> RxComparisonResponse:
    return RxTracker().get_comparison(db, min_days)


@router.get("/all", response_model=RxAllResponse)
def get_rx_all(
    min_days: int = Query(default=7, ge=1),
    db: Session = Depends(get_db),
) -> RxAllResponse:
    return RxTracker().get_all(db, min_days)


@router.get("/changes", response_model=RxChangesResponse)
def get_rx_changes(db: Session = Depends(get_db)) -> RxChangesResponse:
    return RxTracker().get_changes(db)
