"""Callback-style idempotent transaction retry runner.

Design
------
``run_txn`` opens a fresh database session per attempt using
``session_scope(immediate=True)``, executes the caller-supplied unit of work
(a coroutine factory), and retries on SQLite busy/locked errors with bounded
exponential backoff.

``BEGIN IMMEDIATE`` is used so contending writers queue on ``busy_timeout``
rather than failing instantly on a WAL snapshot-upgrade conflict (SQLite returns
``SQLITE_BUSY`` immediately on a deferred→write upgrade, bypassing
``busy_timeout``).

This is ONLY safe for named, demonstrably idempotent units.  The current allowlist:
    1. Invite redemption (conditional UPDATE on redeemed_at IS NULL)
    2. Import chunk writes protected by UNIQUE(device_id, device_session_id)

Analysis storage retries at its call site (``snore.services.analysis_facade``):
a ``SQLITE_BUSY`` on flush/commit means nothing persisted (the transaction was
rolled back), so the unit of work is safe to replay.  ``is_sqlite_contention``
is exposed as a public helper so the analysis facade can apply the same
detection logic without duplicating it here.

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


def is_sqlite_contention(exc: BaseException) -> bool:
    """Return True if *exc* is a SQLite busy/locked error.

    A contention error means the transaction was rolled back before anything
    persisted, so idempotent units of work are safe to retry.  Exposed as a
    public helper so callers outside this module (e.g. analysis storage) can
    apply the same detection logic without duplicating it.

    Detection is substring-based on the stringified exception message.  Any
    exception whose message contains ``"SQLITE_BUSY"``, ``"SQLITE_LOCKED"``,
    or ``"database is locked"`` (case-insensitive) is classified as retriable.
    This is acceptable for local SQLite where these strings are SQLite's own
    output, but a coincidentally matching message from a non-SQLite error
    would be misclassified — an accepted trade-off for simplicity in this
    single-backend codebase.
    """
    msg = str(exc).lower()
    return any(code.lower() in msg for code in _SQLITE_BUSY_CODES)


# Backward-compatible private alias — internal callers and patches that
# reference ``snore.database.txn._is_sqlite_contention`` continue to work.
_is_sqlite_contention = is_sqlite_contention


def backoff_delay(attempt: int) -> float:
    """Return the retry delay in seconds for a given attempt number (1-based).

    Uses bounded exponential backoff with jitter: base delay doubles each
    attempt, plus 0–20 ms of uniform random jitter so simultaneous retriers
    do not all collide on the same retry slot.  Capped at ``MAX_DELAY_SECONDS``.

    Exported so that callers outside this module (e.g. ``_store_with_retry``
    in the analysis facade) can apply an identical backoff schedule without
    duplicating the formula.

    Args:
        attempt: The current attempt number, starting at 1.

    Returns:
        Delay in seconds.
    """
    delay: float = min(
        BASE_DELAY_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.02),
        MAX_DELAY_SECONDS,
    )
    return delay


async def run_txn[T](
    unit_of_work: Callable[[AsyncSession], Awaitable[T]],
    *,
    max_attempts: int = MAX_ATTEMPTS,
) -> T:
    """Execute *unit_of_work* in a fresh session, retrying on SQLite contention.

    ``unit_of_work`` receives a fresh ``AsyncSession`` with an open transaction.
    The session is committed on success; rolled back on any exception.

    Retries use bounded exponential backoff with jitter (see ``backoff_delay``).

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
            async with session_scope(immediate=True) as db:
                return await unit_of_work(db)
        except Exception as exc:
            if not _is_sqlite_contention(exc) or attempt >= max_attempts:
                raise
            last_exc = exc
            delay = backoff_delay(attempt)
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
