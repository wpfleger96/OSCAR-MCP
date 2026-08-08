"""Shared spawn-context ProcessPoolExecutor for CPU-bound work.

Spawn context rationale
-----------------------
``spawn`` creates a fresh interpreter for every worker process.  ``fork`` is
unsafe here because the parent already holds uvicorn sockets, SQLite WAL
file-descriptors, and asyncio state — forking those into a child that then runs
NumPy computation risks corrupted state and double-close on the inherited FDs.
Spawning avoids all of that: each worker starts clean, imports only what it
needs, and never shares the parent's GIL or open handles.

Memory note
-----------
Each worker carries a full Python + NumPy runtime: expect ~75-150 MB RSS per
worker depending on the analysis modules imported.  On machines with less than
~500 MB free, set ``SNORE_COMPUTE_MAX_WORKERS=1`` to cap the pool at one
worker process.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading

from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor

logger = logging.getLogger(__name__)

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()
_SPAWN_CTX = multiprocessing.get_context("spawn")


def _read_max_workers() -> int:
    from snore.api.config import get_config  # noqa: PLC0415

    try:
        return get_config().compute_max_workers
    except Exception:
        return max(1, (os.cpu_count() or 2) - 1)


def _is_broken(pool: ProcessPoolExecutor) -> bool:
    """Return True when *pool* has been marked broken by a worker crash.

    Relies on the CPython-private ``_broken`` attribute of
    ``ProcessPoolExecutor``.  If a future CPython version renames this
    attribute, ``test_broken_sentinel_exists`` in ``tests/unit/test_process_pool.py``
    will fail in CI before the regression reaches production.
    """
    return bool(getattr(pool, "_broken", False))


def get_pool() -> ProcessPoolExecutor:
    """Return the shared ``ProcessPoolExecutor``, creating it on first call.

    Uses a double-checked lock so that the pool is only created once.  If the
    existing pool is detected as broken (``_broken`` attribute is true), the
    pool is replaced with a fresh one.

    The module global ``_pool`` is snapshotted into a local variable before
    every check so that a concurrent ``shutdown_pool()`` call cannot cause the
    fast path to return ``None`` between the guard and the return.
    """
    global _pool
    p = _pool
    if p is not None and not _is_broken(p):
        return p
    with _pool_lock:
        p = _pool
        if p is not None and not _is_broken(p):
            return p
        if p is not None and _is_broken(p):
            logger.warning(
                "Shared process pool is broken; creating replacement pool. "
                "Reduce SNORE_COMPUTE_MAX_WORKERS if memory is constrained."
            )
            try:
                p.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        max_workers = _read_max_workers()
        _pool = ProcessPoolExecutor(max_workers=max_workers, mp_context=_SPAWN_CTX)
        return _pool


def shutdown_pool(*, wait: bool = False) -> None:
    """Shut down the shared pool and clear the singleton.

    Called at app teardown only.  Callers that cancel an import job must NOT
    call this — use ``cancel_pending()`` instead.

    Args:
        wait: If ``True``, block until all running futures complete.  Defaults
            to ``False`` so the server can shut down quickly.
    """
    global _pool
    with _pool_lock:
        pool = _pool
        _pool = None
    if pool is not None:
        try:
            pool.shutdown(wait=wait, cancel_futures=True)
        except Exception:
            logger.warning("Error shutting down shared process pool", exc_info=True)


def cancel_pending(futures: Iterable[Future]) -> None:  # type: ignore[type-arg]
    """Cancel all not-yet-started futures.

    Intended for cancelling the remaining work of an import job without
    touching the shared pool.  Callers MUST NOT shut down the pool on job
    cancel.

    Args:
        futures: Iterable of ``Future`` objects.  Already-done futures are
            silently skipped.
    """
    for f in futures:
        if not f.done():
            f.cancel()
