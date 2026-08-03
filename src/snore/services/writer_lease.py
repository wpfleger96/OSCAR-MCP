"""Cross-process advisory writer lease for raw CPAP backup files.

Design
------
Every raw-tree writer acquires a **shared** ``flock`` on ``~/.snore/writers.lock``
for the duration of the write.  Profile deletion and quarantine purge acquire it
**exclusive, non-blocking**; if any writer is live, they refuse and advise the
operator to stop the API server.

The lease manager supports nested holds within the same process via a per-process
reference count over a single file descriptor.  ``flock`` re-acquisition on the
same fd is idempotent, so an in-API backup call underneath the lifetime shared hold
neither blocks nor double-releases.

Startup recovery
----------------
The API server acquires the exclusive lease at startup (to replay any interrupted
deletion saga), then downgrades to a permanent shared hold before serving.
Because only one API process may run (SQLite one-writer constraint), the exclusive
startup slot is always available after a clean prior exit (kernel releases the fd).

Thread safety
-------------
The reference count is protected by a threading.Lock.  All public methods are
safe to call from the import worker thread.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading

from pathlib import Path

logger = logging.getLogger(__name__)


class WriterLeaseError(Exception):
    """Raised when an exclusive lease cannot be acquired (writers are active)."""


class WriterLeaseManager:
    """Manages a cross-process advisory shared/exclusive flock lease.

    Usage — per-operation shared hold (e.g. backup worker)::

        lease = WriterLeaseManager()
        with lease.shared():
            backup_svc.backup_via_parser(...)

    Usage — API lifetime shared hold (in lifespan)::

        lease = WriterLeaseManager()
        lease.acquire_shared()        # held for process lifetime
        # ... at shutdown (optional, kernel releases on exit anyway):
        lease.release()

    Usage — exclusive offline operation (e.g. profile delete)::

        lease = WriterLeaseManager()
        with lease.exclusive():       # raises WriterLeaseError if any writer active
            # rename raw dir, cascade DB, purge quarantine
            ...
    """

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path or (Path.home() / ".snore" / "writers.lock")
        self._count_lock = threading.Lock()
        self._refcount: int = 0
        self._fd: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire_shared(self) -> None:
        """Increment the per-process shared hold refcount.

        Idempotent within the same process — flock() on the same fd is a no-op,
        so multiple nested acquires are safe.
        """
        with self._count_lock:
            if self._fd is None:
                self._fd = self._open_lock_file()
            # flock LOCK_SH is idempotent on an already-shared fd.
            fcntl.flock(self._fd, fcntl.LOCK_SH)
            self._refcount += 1
            logger.debug("writer_lease: shared acquire (refcount=%d)", self._refcount)

    def release(self) -> None:
        """Decrement the per-process refcount; unlock when it hits zero."""
        with self._count_lock:
            if self._refcount <= 0:
                return
            self._refcount -= 1
            logger.debug("writer_lease: release (refcount=%d)", self._refcount)
            if self._refcount == 0 and self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None

    def acquire_exclusive(self) -> None:
        """Acquire the exclusive lease.  Non-blocking — raises WriterLeaseError if busy.

        Must only be called by offline operator commands (profile delete,
        purge-quarantine, startup recovery) when no API server is running.
        Raises WriterLeaseError if any other process holds a shared lock.
        """
        fd = self._open_lock_file()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise WriterLeaseError(
                "Cannot acquire exclusive writer lease: another process holds it. "
                "Stop the API server and wait for all imports to complete, then retry."
            ) from exc
        with self._count_lock:
            if self._fd is not None:
                # We already hold a shared fd; close the new exclusive one.
                # In practice this path only runs in tests — the API server
                # never calls acquire_exclusive while it also holds shared.
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                raise WriterLeaseError(
                    "Cannot upgrade from shared to exclusive within the same process."
                )
            self._fd = fd
            self._refcount = 1
            logger.debug("writer_lease: exclusive acquired")

    def release_exclusive(self) -> None:
        """Release an exclusively held lease."""
        with self._count_lock:
            if self._fd is None or self._refcount <= 0:
                return
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
            self._refcount = 0
            logger.debug("writer_lease: exclusive released")

    # ------------------------------------------------------------------
    # Context-manager helpers
    # ------------------------------------------------------------------

    def shared(self) -> _SharedContext:
        """Return a context manager for a per-operation shared hold."""
        return _SharedContext(self)

    def exclusive(self) -> _ExclusiveContext:
        """Return a context manager for an exclusive hold (offline ops only)."""
        return _ExclusiveContext(self)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_lock_file(self) -> int:
        """Open (create if necessary) the lock file and return its fd."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)


class _SharedContext:
    def __init__(self, mgr: WriterLeaseManager) -> None:
        self._mgr = mgr

    def __enter__(self) -> None:
        self._mgr.acquire_shared()

    def __exit__(self, *_: object) -> None:
        self._mgr.release()


class _ExclusiveContext:
    def __init__(self, mgr: WriterLeaseManager) -> None:
        self._mgr = mgr

    def __enter__(self) -> None:
        self._mgr.acquire_exclusive()

    def __exit__(self, *_: object) -> None:
        self._mgr.release_exclusive()


# Module-level singleton — shared by all code that imports this module.
_default_lease: WriterLeaseManager | None = None
_default_lease_lock = threading.Lock()


def get_writer_lease() -> WriterLeaseManager:
    """Return the process-singleton WriterLeaseManager."""
    global _default_lease
    if _default_lease is None:
        with _default_lease_lock:
            if _default_lease is None:
                _default_lease = WriterLeaseManager()
    return _default_lease
