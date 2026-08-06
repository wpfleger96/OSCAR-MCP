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
    """
    await asyncio.to_thread(_gate.acquire)
    try:
        yield
    finally:
        _gate.release()
