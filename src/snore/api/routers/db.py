# WARNING: vacuum and stats endpoints are unauthenticated. Add auth middleware before exposing to untrusted networks.

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.deps import get_db, service_dep
from snore.database.target import DatabaseTarget
from snore.services.database_service import DatabaseService
from snore.services.schemas import DatabaseStats, ResetResult, VacuumResult

router = APIRouter()

DatabaseServiceDep = Annotated[DatabaseService, Depends(service_dep(DatabaseService))]


class DatabaseStatsPublic(DatabaseStats):
    # Exclude server filesystem path from API responses
    db_path: str = Field(default="", exclude=True)


def _get_target() -> DatabaseTarget:
    """Resolve the current database target from the environment/session."""
    # DatabaseTarget reads SNORE_DATABASE_URL / SNORE_DB_PATH / default chain.
    return DatabaseTarget.from_env_and_flags(db_flag=None, warn_ignored=False)


@router.get("/stats", response_model=DatabaseStatsPublic)
async def get_stats(
    service: DatabaseServiceDep,
    target: Annotated[DatabaseTarget, Depends(_get_target)],
) -> DatabaseStats:
    db_path = target.sqlite_path if target.dialect == "sqlite" else ""
    return await service.get_stats(db_path)


@router.post("/vacuum", response_model=VacuumResult)
def vacuum_db(
    service: DatabaseServiceDep,
    target: Annotated[DatabaseTarget, Depends(_get_target)],
) -> VacuumResult:
    """Vacuum the SQLite database to reclaim space after deletions.

    Requires a SQLite file target; raises 422 for non-SQLite databases.
    """
    if (
        target.dialect != "sqlite"
        or not target.location
        or target.location == ":memory:"
    ):
        from fastapi import HTTPException  # noqa: PLC0415

        raise HTTPException(
            status_code=422,
            detail="VACUUM is only available for SQLite file databases.",
        )
    return service.vacuum_sqlite(target.sqlite_path)


@router.post("/reset", response_model=ResetResult)
async def reset_db(
    service: DatabaseServiceDep,
    target: Annotated[DatabaseTarget, Depends(_get_target)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResetResult:
    """Delete all rows from all tables (generic) and vacuum if SQLite.

    Generic row reset works for any dialect.  SQLite targets additionally
    receive a VACUUM pass after the commit.
    """
    import os  # noqa: PLC0415

    is_sqlite_file = target.dialect == "sqlite" and target.location not in (
        "",
        ":memory:",
    )
    db_path = target.sqlite_path if is_sqlite_file else ""

    size_before = (
        os.path.getsize(db_path) / (1024 * 1024)
        if db_path and os.path.exists(db_path)
        else 0.0
    )

    # Generic row-deletion phase — caller-transaction-owned.
    tables_cleared = await service.reset_rows()
    total = sum(tables_cleared.values())

    # Commit before VACUUM (SQLite forbids VACUUM inside a transaction).
    await db.commit()

    # SQLite-only file maintenance.
    if is_sqlite_file:
        service.vacuum_sqlite(db_path)

    size_after = (
        os.path.getsize(db_path) / (1024 * 1024)
        if db_path and os.path.exists(db_path)
        else 0.0
    )

    return ResetResult(
        status="success",
        tables_cleared=tables_cleared,
        total_rows_deleted=total,
        size_before_mb=size_before,
        size_after_mb=size_after,
    )
