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
import os
import sys
import threading

from concurrent.futures import ProcessPoolExecutor
from typing import cast

from snore.utils import _pool_core
from snore.utils._pool_core import _is_broken as _is_broken  # re-export
from snore.utils._pool_core import cancel_pending as cancel_pending  # re-export

logger = logging.getLogger(__name__)

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()

_STATE = cast(_pool_core.PoolState, sys.modules[__name__])


def _read_max_workers() -> int:
    from snore.api.config import get_config  # noqa: PLC0415

    try:
        return get_config().compute_max_workers
    except Exception:
        return max(1, (os.cpu_count() or 2) - 1)


def get_pool() -> ProcessPoolExecutor:
    """Return the shared ``ProcessPoolExecutor``, creating it on first call.

    See ``snore.utils._pool_core.get_pool`` for the double-checked-lock and
    broken-pool replacement semantics.
    """
    return _pool_core.get_pool(
        _STATE,
        logger=logger,
        broken_message=(
            "Shared process pool is broken; creating replacement pool. "
            "Reduce SNORE_COMPUTE_MAX_WORKERS if memory is constrained."
        ),
        read_max_workers=_read_max_workers,
    )


def shutdown_pool(*, wait: bool = False) -> None:
    """Shut down the shared pool and clear the singleton.

    Called at app teardown only.  Callers that cancel an import job must NOT
    call this — use ``cancel_pending()`` instead.

    Args:
        wait: If ``True``, block until all running futures complete.  Defaults
            to ``False`` so the server can shut down quickly.
    """
    _pool_core.shutdown_pool(
        _STATE,
        logger=logger,
        error_message="Error shutting down shared process pool",
        wait=wait,
    )
