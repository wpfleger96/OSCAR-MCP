"""Per-session scope management for validators run as background jobs.

A validator constructed with an injected session (the HTTP request session or a
CLI session) does all its work on that one session — a single transaction, as
before.  A validator constructed with ``db_session=None`` is running as a
background JOB, where a batch-long read snapshot would pin the SQLite WAL and
starve checkpoints (the anti-pattern :mod:`snore.api.analysis_jobs` documents
avoiding).  For that case :func:`work_session` hands out a fresh short-lived
``session_scope()`` per unit of work, so the read snapshot is released between
sessions and no single transaction spans the whole run.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def work_session(injected: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """Yield the injected session, or a fresh short scope when there is none.

    Shared mode (``injected`` is a session): yields it directly, opening and
    closing no transaction of its own — every unit of work runs on the one
    session, exactly as before.  JOB mode (``injected is None``): opens a fresh
    ``session_scope()`` that commits and closes on exit, releasing the WAL read
    snapshot between units of work.
    """
    if injected is not None:
        yield injected
        return
    from snore.database.session import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        yield db
