from typing import Annotated

from fastapi import APIRouter, Depends

from snore.api.deps import service_dep
from snore.services import DeviceService
from snore.services.schemas import DeviceDetail, DeviceInfo

router = APIRouter()

DeviceServiceDep = Annotated[DeviceService, Depends(service_dep(DeviceService))]


@router.get("/", response_model=list[DeviceInfo])
async def list_devices(service: DeviceServiceDep) -> list[DeviceInfo]:
    return await service.list_devices()


@router.get("/{device_id}", response_model=DeviceDetail)
async def get_device_detail(device_id: int, service: DeviceServiceDep) -> DeviceDetail:
    return await service.get_device_detail(device_id)
