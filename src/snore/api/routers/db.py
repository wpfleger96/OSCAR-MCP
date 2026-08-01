# WARNING: vacuum and stats endpoints are unauthenticated. Add auth middleware before exposing to untrusted networks.

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from snore.api.deps import service_dep
from snore.database.session import get_db_path
from snore.services.database_service import DatabaseService
from snore.services.schemas import DatabaseStats, ResetResult, VacuumResult

router = APIRouter()

DatabaseServiceDep = Annotated[DatabaseService, Depends(service_dep(DatabaseService))]


class DatabaseStatsPublic(DatabaseStats):
    # Exclude server filesystem path from API responses
    db_path: str = Field(default="", exclude=True)


@router.get("/stats", response_model=DatabaseStatsPublic)
async def get_stats(service: DatabaseServiceDep) -> DatabaseStats:
    return await service.get_stats(get_db_path())


@router.post("/vacuum", response_model=VacuumResult)
async def vacuum_db(service: DatabaseServiceDep) -> VacuumResult:
    return await service.vacuum(get_db_path())


@router.post("/reset", response_model=ResetResult)
async def reset_db(service: DatabaseServiceDep) -> ResetResult:
    return await service.reset(get_db_path())
