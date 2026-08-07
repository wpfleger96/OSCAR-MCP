"""Shared single-writer gate for background bulk-write transactions.

SNORE uses SQLite in WAL mode with ``busy_timeout=5000``.  Two background
workers write concurrently: the import worker (sustained bulk chunk
transactions via ``run_txn``) and the analysis worker (short INSERT-only
result stores via ``session_scope``).  When back-to-back import chunks hold
the SQLite write lock, a concurrent analysis store can exhaust its 5 s
``busy_timeout`` and fail with "database is locked".

This module exposes a module-level ``threading.Lock`` as an async context
manager.  Both workers acquire the gate OUTSIDE their transaction scope so
at most one background bulk-write transaction is open at a time.  HTTP
request-path writes (short, one-off) remain ungated and keep
``busy_timeout`` as their protection.

**Cancellation safety:**
``await asyncio.to_thread(_gate.acquire)`` can be cancelled after the pool
thread has already acquired the lock — the thread-pool future continues past
the cancellation point, leaving the lock held with no owner to release it
and causing all future callers to deadlock permanently.

To fix this, the acquire future is shielded from the outer cancellation and
an ``add_done_callback`` is registered on cancellation: the callback releases
the lock if the acquire future completed successfully (meaning the thread did
acquire it after the cancel was delivered).  This ensures every acquisition
is matched by exactly one release, even when the caller is cancelled mid-wait.
"""

from __future__ import annotations

import asyncio
import threading

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_gate = threading.Lock()


@asynccontextmanager
async def write_gate() -> AsyncIterator[None]:
    """Serialize background bulk-write transactions across worker threads.

    Import chunks and analysis result stores each run in their own worker
    thread with a private event loop; this cross-thread mutex ensures at most
    one of them is inside a write transaction at a time, so neither can
    exhaust the other's SQLite busy_timeout.  Acquired OUTSIDE the session/
    transaction scope; hold time == one transaction.

    Cancellation-safe: if this coroutine is cancelled while waiting for the
    lock, a done-callback releases it if the underlying thread-pool call
    eventually acquires it, preventing a permanent deadlock.
    """
    # Schedule the blocking acquire in a thread pool as an explicit Task so
    # asyncio.shield can protect it from our cancellation while still letting
    # this coroutine exit promptly.
    acquire_task: asyncio.Task[bool] = asyncio.create_task(
        asyncio.to_thread(_gate.acquire)
    )
    try:
        await asyncio.shield(acquire_task)
    except asyncio.CancelledError:
        # This coroutine was cancelled while waiting.  acquire_task is shielded
        # so the underlying thread continues running.  Register a callback that
        # releases the lock if the thread eventually acquires it; without this,
        # the lock leaks and all future write_gate() callers deadlock.
        def _release_if_acquired(task: asyncio.Task[bool]) -> None:
            if not task.cancelled() and task.exception() is None:
                _gate.release()

        acquire_task.add_done_callback(_release_if_acquired)
        raise
    try:
        yield
    finally:
        _gate.release()
