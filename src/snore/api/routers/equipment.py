from typing import Annotated

from fastapi import APIRouter, Depends

from snore.api.deps import service_dep, service_dep_immediate
from snore.api.guards import RequireWritable
from snore.api.schemas import (
    MaskLogCreateRequest,
    MaskLogEntryResponse,
    MaskLogUpdateRequest,
)
from snore.services import MaskEpochService, MaskLogService
from snore.services.schemas import MaskEpochResponse

router = APIRouter()

MaskLogServiceDep = Annotated[MaskLogService, Depends(service_dep(MaskLogService))]
MaskLogImmediateServiceDep = Annotated[
    MaskLogService, Depends(service_dep_immediate(MaskLogService))
]
MaskEpochServiceDep = Annotated[
    MaskEpochService, Depends(service_dep(MaskEpochService))
]


@router.get("/masks/epochs", response_model=list[MaskEpochResponse])
async def list_mask_epochs(
    service: MaskEpochServiceDep,
) -> list[MaskEpochResponse]:
    return await service.list_epochs()


@router.get("/masks", response_model=list[MaskLogEntryResponse])
async def list_mask_log_entries(
    service: MaskLogServiceDep,
) -> list[MaskLogEntryResponse]:
    return await service.list_entries()


@router.post("/masks", response_model=MaskLogEntryResponse, status_code=201)
async def create_mask_log_entry(
    body: MaskLogCreateRequest,
    service: MaskLogServiceDep,
    _actor: RequireWritable,
) -> MaskLogEntryResponse:
    return await service.create_entry(**body.model_dump())


@router.patch("/masks/{entry_id}", response_model=MaskLogEntryResponse)
async def update_mask_log_entry(
    entry_id: int,
    body: MaskLogUpdateRequest,
    _actor: RequireWritable,
    service: MaskLogImmediateServiceDep,
) -> MaskLogEntryResponse:
    return await service.update_entry(entry_id, body.model_dump(exclude_unset=True))


@router.delete("/masks/{entry_id}", status_code=204)
async def delete_mask_log_entry(
    entry_id: int,
    _actor: RequireWritable,
    service: MaskLogImmediateServiceDep,
) -> None:
    await service.delete_entry(entry_id)
