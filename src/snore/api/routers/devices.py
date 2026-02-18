from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.services import DeviceService
from snore.services.schemas import DeviceInfo

router = APIRouter()


@router.get("/", response_model=list[DeviceInfo])
def list_devices(db: Session = Depends(get_db)) -> list[DeviceInfo]:
    service = DeviceService(db)
    return service.list_devices()
