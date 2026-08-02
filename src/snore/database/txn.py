"""Callback-style idempotent transaction retry runner.

Design
------
``run_txn`` opens a fresh database session per attempt, executes the caller-supplied
unit of work (a coroutine factory), and retries on SQLite busy/locked errors with
bounded exponential backoff.

This is ONLY safe for named, demonstrably idempotent units.  The current allowlist:
    1. Invite redemption (conditional UPDATE on redeemed_at IS NULL)
    2. Import chunk writes protected by UNIQUE(device_id, device_session_id)

Analysis storage is NOT retried: ``analysis_results`` supports multiple versions
with no natural uniqueness, so a replay cannot distinguish "retry" from "new version".
Analysis storage relies on ``busy_timeout=5000`` and returns 503 on residual contention.

A generic scope-helper retry is deliberately rejected: ``session_scope()`` yields to
an arbitrary caller body it cannot replay; retrying a failed session risks invalid
transactions and duplicate non-idempotent writes.
"""

from __future__ import annotations

import asyncio
import logging
import random

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.session import session_scope

logger = logging.getLogger(__name__)

# SQLite error codes for "busy" / "locked" conditions.
_SQLITE_BUSY_CODES = frozenset({"SQLITE_BUSY", "SQLITE_LOCKED", "database is locked"})

MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 0.05
MAX_DELAY_SECONDS = 1.0


def _is_sqlite_contention(exc: BaseException) -> bool:
    """Return True if *exc* is a SQLite busy/locked error."""
    msg = str(exc).lower()
    return any(code.lower() in msg for code in _SQLITE_BUSY_CODES)


async def run_txn[T](
    unit_of_work: Callable[[AsyncSession], Awaitable[T]],
    *,
    max_attempts: int = MAX_ATTEMPTS,
) -> T:
    """Execute *unit_of_work* in a fresh session, retrying on SQLite contention.

    ``unit_of_work`` receives a fresh ``AsyncSession`` with an open transaction.
    The session is committed on success; rolled back on any exception.

    Retries use bounded exponential backoff with jitter.

    Args:
        unit_of_work: ``async def f(db: AsyncSession) -> T`` — must be idempotent.
        max_attempts: Maximum number of attempts (default 5).

    Returns:
        The return value of *unit_of_work*.

    Raises:
        The last exception if all attempts are exhausted.
        Any non-contention exception on first occurrence.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with session_scope() as db:
                return await unit_of_work(db)
        except Exception as exc:
            if not _is_sqlite_contention(exc) or attempt >= max_attempts:
                raise
            last_exc = exc
            delay = min(
                BASE_DELAY_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.02),
                MAX_DELAY_SECONDS,
            )
            logger.debug(
                "run_txn: attempt %d/%d hit contention (%s); retrying in %.3fs",
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    # Should never reach here (loop raises on last attempt), but satisfy the type checker.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("run_txn: exhausted attempts without error — this is a bug")
