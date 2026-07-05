from typing import Annotated

from fastapi import APIRouter, Depends

from snore.api.deps import service_dep
from snore.services import DeviceService
from snore.services.schemas import DeviceDetail, DeviceInfo

router = APIRouter()

DeviceServiceDep = Annotated[DeviceService, Depends(service_dep(DeviceService))]


@router.get("/", response_model=list[DeviceInfo])
def list_devices(service: DeviceServiceDep) -> list[DeviceInfo]:
    return service.list_devices()


@router.get("/{device_id}", response_model=DeviceDetail)
def get_device_detail(device_id: int, service: DeviceServiceDep) -> DeviceDetail:
    return service.get_device_detail(device_id)
