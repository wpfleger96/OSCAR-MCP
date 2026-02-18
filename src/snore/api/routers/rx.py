from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.api.schemas import RxComparisonResponse, RxPeriodResponse
from snore.services import RxService

router = APIRouter()


@router.get("/history", response_model=list[RxPeriodResponse])
def get_rx_history(db: Session = Depends(get_db)) -> list[RxPeriodResponse]:
    service = RxService(db)
    return service.get_history()


@router.get(
    "/current",
    response_model=None,
    responses={
        200: {"model": RxPeriodResponse},
        204: {"description": "No RX data available"},
    },
)
def get_rx_current(db: Session = Depends(get_db)) -> RxPeriodResponse | Response:
    service = RxService(db)
    result = service.get_current()
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/compare", response_model=RxComparisonResponse)
def compare_rx(
    min_days: int = Query(default=7),
    db: Session = Depends(get_db),
) -> RxComparisonResponse:
    service = RxService(db)
    return service.get_comparison(min_days)
