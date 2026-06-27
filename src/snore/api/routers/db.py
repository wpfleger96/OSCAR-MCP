import os

from typing import Annotated

from fastapi import APIRouter, Depends

from snore.api.deps import service_dep
from snore.constants import DEFAULT_DATABASE_PATH
from snore.services.database_service import DatabaseService
from snore.services.schemas import DatabaseStats, VacuumResult

router = APIRouter()

DatabaseServiceDep = Annotated[DatabaseService, Depends(service_dep(DatabaseService))]


@router.get("/stats", response_model=DatabaseStats)
def get_stats(service: DatabaseServiceDep) -> DatabaseStats:
    db_path = os.environ.get("SNORE_DB_PATH") or DEFAULT_DATABASE_PATH
    return service.get_stats(db_path)


@router.post("/vacuum", response_model=VacuumResult)
def vacuum_db(service: DatabaseServiceDep) -> VacuumResult:
    db_path = os.environ.get("SNORE_DB_PATH") or DEFAULT_DATABASE_PATH
    return service.vacuum(db_path)
