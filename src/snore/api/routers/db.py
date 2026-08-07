import logging
import os
import secrets

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.constants import NO_STORE
from snore.api.deps import ImmediateDbDep, service_dep
from snore.api.guards import RequireAdmin
from snore.auth.invite_tokens import hash_invite_token
from snore.database import models
from snore.database.models import Base
from snore.database.target import DatabaseTarget
from snore.services.database_service import DatabaseService, _vacuum_background
from snore.services.profile_service import purge_profile_raw_dir
from snore.services.schemas import DatabaseStats, ResetResult, VacuumResult

logger = logging.getLogger(__name__)

router = APIRouter()

# Kept as an empty placeholder — reset has moved to the main router.
local_only_router = APIRouter()

DatabaseServiceDep = Annotated[DatabaseService, Depends(service_dep(DatabaseService))]

# Tables preserved by the data-only reset (include_accounts=False).
# Sleep data tables are deleted; account/auth/profile containers survive.
_DATA_RESET_SKIP = frozenset(
    {"users", "auth_identities", "invites", "profiles", "oauth_attempts"}
)


class DatabaseStatsPublic(DatabaseStats):
    # Exclude server filesystem path from API responses.
    db_path: str = Field(default="", exclude=True)


class ResetRequest(BaseModel):
    include_accounts: bool = False


def _get_target() -> DatabaseTarget:
    """Resolve the current database target from the environment/session."""
    return DatabaseTarget.from_env_and_flags(db_flag=None, warn_ignored=False)


def _raw_root() -> Path:
    from snore.constants import DEFAULT_RAW_BACKUP_DIR  # noqa: PLC0415

    return DEFAULT_RAW_BACKUP_DIR


async def _mint_admin_invite(db: AsyncSession, email: str, base_url: str) -> str:
    """Insert a new admin invite row and return its redemption URL.

    Called after a full factory reset (include_accounts=True) when all rows
    have been deleted.  At that point there are no users or pending invites,
    so the duplicate-guard queries are elided.
    """
    raw = secrets.token_urlsafe(32)
    token_hash = hash_invite_token(raw)
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=7)

    await db.execute(
        insert(models.Invite).values(
            email=email,
            token_hash=token_hash,
            role="admin",
            created_by=None,
            expires_at=expires_at,
            created_at=now,
        )
    )

    url_base = base_url.rstrip("/") if base_url else ""
    return f"{url_base}/invite#{raw}" if url_base else f"/invite#{raw}"


@router.get("/stats", response_model=DatabaseStatsPublic)
async def get_stats(
    service: DatabaseServiceDep,
    target: Annotated[DatabaseTarget, Depends(_get_target)],
    _actor: RequireAdmin,
) -> DatabaseStats:
    db_path = target.sqlite_path if target.dialect == "sqlite" else ""
    return await service.get_stats(db_path)


@router.post("/vacuum", response_model=VacuumResult)
def vacuum_db(
    service: DatabaseServiceDep,
    target: Annotated[DatabaseTarget, Depends(_get_target)],
    _actor: RequireAdmin,
) -> VacuumResult:
    """Vacuum the SQLite database to reclaim space after deletions.

    Requires a SQLite file target; raises 422 for non-SQLite databases.
    """
    if (
        target.dialect != "sqlite"
        or not target.location
        or target.location == ":memory:"
    ):
        raise HTTPException(
            status_code=422,
            detail="VACUUM is only available for SQLite file databases.",
        )
    return service.vacuum_sqlite(target.sqlite_path)


@router.post("/reset", response_model=ResetResult)
async def reset_db(
    service: DatabaseServiceDep,
    target: Annotated[DatabaseTarget, Depends(_get_target)],
    db: ImmediateDbDep,
    actor: RequireAdmin,
    background_tasks: BackgroundTasks,
    body: Annotated[ResetRequest | None, Body()] = None,
) -> JSONResponse:
    """Delete database rows and vacuum.

    Two modes selected via the request body:

    ``include_accounts=false`` (default):
        Delete all sleep data (devices, sessions, waveforms, events, statistics,
        settings, analysis results, breaths, detected patterns, days, import job
        records) and purge every ``raw/<profile_id>/`` backup directory.
        User accounts, auth identities, invites, and profile containers are
        preserved.  Safe for multiuser deployments — users keep their accounts
        and can re-import data afterward.

    ``include_accounts=true``:
        Full factory reset via ``reset_rows()`` (every row in every table) plus
        raw-dir purge and vacuum.  A fresh bootstrap admin invite is created for
        the calling admin's email and returned in ``bootstrap_invite_url``.  The
        caller's session is immediately dead; they must redeem that URL to regain
        access.

    The response carries ``Cache-Control: no-store`` so the one-time
    ``bootstrap_invite_url`` is never cached by a proxy or browser.

    VACUUM runs as a post-response background task.  ``size_after_mb`` is null
    and ``vacuum_scheduled`` is true in the response when VACUUM is queued.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    req = body or ResetRequest()

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

    raw_root = _raw_root()

    if req.include_accounts:
        # Full factory reset.
        #
        # Fetch caller email BEFORE any deletes — this is a hard precondition.
        # If the calling user's row can't be found, abort rather than wiping
        # the database and leaving the system with no admin and no invite.
        row = (
            await db.execute(
                select(models.User.canonical_email).where(
                    models.User.id == actor.user_id
                )
            )
        ).first()
        caller_email = row[0] if row else None

        if not caller_email:
            raise HTTPException(
                status_code=500,
                detail="Could not resolve caller email; reset aborted to prevent data loss.",
            )

        # Collect all profile IDs for raw-dir purge (Core SQL, avoids ORM
        # identity-map conflicts with the bulk delete that follows).
        profile_ids = list((await db.execute(select(models.Profile.id))).scalars())

        tables_cleared = await service.reset_rows()
        total = sum(tables_cleared.values())

        # After bulk deletes, expunge stale ORM state (e.g. the acting user's
        # row, which was deleted) before inserting the invite so the subsequent
        # commit does not try to flush stale ORM objects.
        db.expunge_all()

        # Insert the bootstrap invite in the same transaction as the deletion.
        base_url = cfg.public_base_url or ""
        bootstrap_invite_url: str | None = await _mint_admin_invite(
            db, caller_email, base_url
        )

        logger.warning(
            "Full factory reset committed for caller %s; bootstrap invite URL "
            "is only in the response body and will not be recoverable afterward.",
            caller_email,
        )
        await db.commit()

    else:
        # Data-only reset — delete sleep data, preserve accounts/profiles.
        profile_ids = list((await db.execute(select(models.Profile.id))).scalars())

        tables_cleared = {}
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _DATA_RESET_SKIP:
                continue
            cursor = await db.execute(table.delete())
            tables_cleared[table.name] = cursor.rowcount or 0  # type: ignore[attr-defined]

        total = sum(tables_cleared.values())
        bootstrap_invite_url = None

        await db.commit()

    # Purge raw backup dirs after commit (idempotent quarantine-rename pattern).
    for pid in profile_ids:
        purge_profile_raw_dir(pid, raw_root)

    # Schedule VACUUM as a post-response background task.  FastAPI runs sync
    # background tasks in a thread pool so the event loop is never blocked.
    if is_sqlite_file:
        background_tasks.add_task(_vacuum_background, db_path)

    return JSONResponse(
        content=ResetResult(
            status="success",
            tables_cleared=tables_cleared,
            total_rows_deleted=total,
            size_before_mb=size_before,
            size_after_mb=None,
            vacuum_scheduled=is_sqlite_file,
            bootstrap_invite_url=bootstrap_invite_url,
        ).model_dump(),
        headers=NO_STORE,
    )
