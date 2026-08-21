"""Shared lifecycle logic for the process-pool singleton modules.

``parse_pool`` and ``process_pool`` are near-identical singletons that differ
only in log wording and worker-count defaults.  Each public module owns its
module-level ``_pool`` / ``_pool_lock`` pair (unit tests reset that state
directly on the module), so the shared logic here operates on the calling
module through the ``PoolState`` protocol instead of owning the state itself.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading

from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Protocol

_SPAWN_CTX = multiprocessing.get_context("spawn")


class PoolState(Protocol):
    """Mutable singleton state owned by each public pool module."""

    _pool: ProcessPoolExecutor | None
    _pool_lock: threading.Lock


def _is_broken(pool: ProcessPoolExecutor) -> bool:
    """Return True when *pool* has been marked broken by a worker crash.

    Relies on the CPython-private ``_broken`` attribute of
    ``ProcessPoolExecutor``.  If a future CPython version renames this
    attribute, ``test_broken_sentinel_exists`` in the pool unit tests will
    fail in CI before the regression reaches production.
    """
    return bool(getattr(pool, "_broken", False))


def get_pool(
    state: PoolState,
    *,
    logger: logging.Logger,
    broken_message: str,
    read_max_workers: Callable[[], int],
) -> ProcessPoolExecutor:
    """Return the module's ``ProcessPoolExecutor``, creating it on first call.

    Uses a double-checked lock so that the pool is only created once.  If the
    existing pool is detected as broken (``_broken`` attribute is true), the
    pool is replaced with a fresh one.

    The module global ``_pool`` is snapshotted into a local variable before
    every check so that a concurrent ``shutdown_pool()`` call cannot cause the
    fast path to return ``None`` between the guard and the return.
    """
    p = state._pool
    if p is not None and not _is_broken(p):
        return p
    with state._pool_lock:
        p = state._pool
        if p is not None and not _is_broken(p):
            return p
        if p is not None:
            logger.warning(broken_message)
            try:
                p.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        state._pool = ProcessPoolExecutor(
            max_workers=read_max_workers(), mp_context=_SPAWN_CTX
        )
        return state._pool


def shutdown_pool(
    state: PoolState,
    *,
    logger: logging.Logger,
    error_message: str,
    wait: bool,
) -> None:
    """Shut down the module's pool and clear its singleton.

    Called at app teardown only.  Callers that cancel an import job must NOT
    call this — use ``cancel_pending()`` instead.
    """
    with state._pool_lock:
        pool = state._pool
        state._pool = None
    if pool is not None:
        try:
            pool.shutdown(wait=wait, cancel_futures=True)
        except Exception:
            logger.warning(error_message, exc_info=True)


def cancel_pending(futures: Iterable[Future]) -> None:  # type: ignore[type-arg]
    """Cancel all not-yet-started futures.

    Intended for cancelling the remaining work of an import job without
    touching the pool.  Callers MUST NOT shut down the pool on job cancel.

    Args:
        futures: Iterable of ``Future`` objects.  Already-done futures are
            silently skipped.
    """
    for f in futures:
        if not f.done():
            f.cancel()
