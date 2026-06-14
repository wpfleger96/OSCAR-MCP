from typing import Annotated

from fastapi import APIRouter, Depends

from snore.api.deps import service_dep
from snore.services import DatabaseService
from snore.services.schemas import DeviceInfo

router = APIRouter()

DatabaseServiceDep = Annotated[DatabaseService, Depends(service_dep(DatabaseService))]


@router.get("/", response_model=list[DeviceInfo])
def list_devices(service: DatabaseServiceDep) -> list[DeviceInfo]:
    return service.list_devices()
