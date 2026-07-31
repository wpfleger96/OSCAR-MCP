"""In-memory job store for streaming import progress via SSE.

State machine
-------------
::

    POST /            → pending
    GET /{id}/progress → starts worker (pending → running) once; subsequent GETs attach an observer
    DELETE /{id}      → cancelled (idempotent after any terminal state)
    worker finishes   → succeeded | failed
    reaper            → removes terminal jobs after TTL

Terminal states: succeeded, failed, cancelled.
Active states:   pending, running.

Guarantees
----------
- Start-once: the worker starts exactly once at POST; /progress GETs are observer-only.
- Fan-out: each observer has its own capacity-one/coalescing channel backed by the
  latest-progress snapshot; a stalled observer never accumulates unbounded messages.
  Terminal delivery is never dropped (capacity-one channels are upgraded to terminal
  on arrival regardless of current fill).
- Late observers: connecting after the job has reached a terminal state immediately
  receive the terminal event; no 404 after completion.
- Reaper: removes terminal jobs only; active jobs are never reaped regardless of age.
- POST failure: if temp-dir creation succeeds but job registration fails, the caller is
  responsible for cleanup; the job store never holds a reference to an incomplete job.
- Shutdown: `shutdown()` cancels all non-terminal jobs and awaits worker threads.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# How long to retain terminal jobs before the reaper removes them.
JOB_TTL_SECONDS: float = 600.0


class JobState(Enum):
    """All states a job can occupy."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})
ACTIVE_STATES = frozenset({JobState.PENDING, JobState.RUNNING})


class JobType(Enum):
    UPLOAD = "upload"
    PATH = "path"


# ---------------------------------------------------------------------------
# Per-observer coalescing channel
# ---------------------------------------------------------------------------


class ObserverChannel:
    """Capacity-one coalescing notification channel for a single SSE observer.

    Each observer holds exactly one pending message slot.  A new message
    overwrites an un-consumed one (coalescing), *except* when the pending
    message is already a terminal event — terminal events are never dropped.

    Usage::

        ch = ObserverChannel()
        ch.put({"event": "progress", "data": {"message": "..."}})  # writer
        msg = ch.get(timeout=1.0)  # blocking poll; None on timeout
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._slot: dict[str, Any] | None = None
        self._closed = False

    def put(self, msg: dict[str, Any]) -> None:
        """Deliver msg to this observer, coalescing non-terminal messages."""
        with self._cond:
            if self._slot is not None and self._slot.get("event") in (
                "complete",
                "error",
            ):
                # A terminal event is already waiting; do not overwrite.
                return
            self._slot = msg
            self._cond.notify_all()

    def get(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Block up to *timeout* seconds and return the next message, or None."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._slot is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if self._closed and self._slot is None:
                return None
            msg = self._slot
            self._slot = None
            return msg

    def close(self) -> None:
        """Unblock any waiting get() call with a None return."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# ImportJob
# ---------------------------------------------------------------------------


@dataclass
class ImportJob:
    """Represents a single import operation with full lifecycle management."""

    job_id: str
    job_type: JobType
    created_at: float = field(default_factory=time.monotonic)

    # UPLOAD jobs: temp dir with written files.
    temp_dir: Path | None = None
    # PATH jobs: sources list.
    sources: list[Any] | None = None

    # State machine fields — protected by _lock.
    _state: JobState = field(default=JobState.PENDING, init=False, repr=False)
    _terminal_msg: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _latest_progress: dict[str, Any] | None = field(
        default=None, init=False, repr=False
    )
    _worker_thread: threading.Thread | None = field(
        default=None, init=False, repr=False
    )
    _cancel_flag: bool = field(default=False, init=False, repr=False)
    _observers: list[ObserverChannel] = field(
        default_factory=list, init=False, repr=False
    )
    _terminal_at: float | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    @property
    def state(self) -> JobState:
        with self._lock:
            return self._state

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._state in TERMINAL_STATES

    @property
    def cancel_requested(self) -> bool:
        """True if cancellation has been requested (checked at batch boundaries)."""
        with self._lock:
            return self._cancel_flag

    def attach_observer(self) -> ObserverChannel:
        """Register a new SSE observer.

        If the job is already terminal, the channel is pre-loaded with the
        terminal event so the observer receives it immediately.  If the job
        has a latest-progress snapshot, that is also pre-loaded first.
        """
        ch = ObserverChannel()
        with self._lock:
            if self._state in TERMINAL_STATES:
                # Late observer: deliver terminal state immediately.
                if self._terminal_msg is not None:
                    ch.put(self._terminal_msg)
            else:
                # Deliver latest progress snapshot so observer has context.
                if self._latest_progress is not None:
                    ch.put(self._latest_progress)
                self._observers.append(ch)
        return ch

    def detach_observer(self, ch: ObserverChannel) -> None:
        """Remove an observer (SSE connection closed).  Does not affect the job."""
        with self._lock:
            try:
                self._observers.remove(ch)
            except ValueError:
                pass
        ch.close()

    def _broadcast(self, msg: dict[str, Any]) -> None:
        """Deliver msg to all attached observers.  Must be called without _lock."""
        with self._lock:
            observers = list(self._observers)
        for ch in observers:
            ch.put(msg)

    def report_progress(self, message: str) -> None:
        """Record a progress snapshot and broadcast to all observers."""
        msg = {"event": "progress", "data": {"message": message}}
        with self._lock:
            self._latest_progress = msg
        self._broadcast(msg)

    def try_start(self) -> bool:
        """Transition pending → running.

        Returns True if the transition succeeded (caller should start the worker).
        Returns False if the job is already running/terminal (start-once guarantee).
        """
        with self._lock:
            if self._state != JobState.PENDING:
                return False
            self._state = JobState.RUNNING
            return True

    def try_cancel(self) -> bool:
        """Request cancellation.

        Allowed from any state.  Idempotent after terminal.  Returns True if
        the job was in a non-terminal state (the caller may need to await the
        worker).
        """
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False  # Already done; no-op.
            self._cancel_flag = True
            if self._state == JobState.PENDING:
                # Cancel before worker starts: transition directly to CANCELLED.
                self._state = JobState.CANCELLED
                terminal_msg = {"event": "error", "data": {"message": "Cancelled"}}
                self._terminal_msg = terminal_msg
                self._terminal_at = time.monotonic()
                observers = list(self._observers)
            else:
                # Running: set the flag; worker's finally block will call _finish.
                observers = []
        # Notify observers outside the lock for cancel-before-start.
        for ch in observers:
            ch.put(terminal_msg)
        return True

    def _finish(self, *, succeeded: bool, terminal_msg: dict[str, Any]) -> None:
        """Transition running → succeeded/failed.  Called by the worker thread."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                # cancel() already won the race; honour it.
                return
            self._state = JobState.SUCCEEDED if succeeded else JobState.FAILED
            self._terminal_msg = terminal_msg
            self._terminal_at = time.monotonic()
            observers = list(self._observers)
            self._observers.clear()
        for ch in observers:
            ch.put(terminal_msg)

    def wait_for_worker(self, timeout: float = 5.0) -> None:
        """Block until the worker thread exits (used during shutdown)."""
        with self._lock:
            t = self._worker_thread
        if t is not None:
            t.join(timeout=timeout)

    def cleanup_files(self) -> None:
        """Remove the temp directory if this is an UPLOAD job."""
        if self.temp_dir is not None and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------

_jobs: dict[str, ImportJob] = {}
_lock = threading.Lock()


def create_job(job_type: JobType, **kwargs: Any) -> ImportJob:
    """Create a new job in PENDING state and register it in the store.

    The caller must start the worker thread immediately after this call.
    If registration fails, the caller is responsible for any cleanup.
    """
    _reap_terminal()
    job = ImportJob(job_id=uuid.uuid4().hex, job_type=job_type, **kwargs)
    with _lock:
        _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> ImportJob | None:
    with _lock:
        return _jobs.get(job_id)


def remove_job(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


def cancel_job(job_id: str) -> bool:
    """Cancel a job.  Returns True if the job existed and was not already terminal."""
    job = get_job(job_id)
    if job is None:
        return False
    return job.try_cancel()


def shutdown(timeout: float = 10.0) -> None:
    """Cancel all non-terminal jobs and wait for worker threads to exit.

    Called during application shutdown to ensure clean teardown.
    """
    with _lock:
        active = [j for j in _jobs.values() if not j.is_terminal]
    for job in active:
        job.try_cancel()
    for job in active:
        job.wait_for_worker(timeout=timeout)


def _reap_terminal() -> None:
    """Remove terminal jobs older than JOB_TTL_SECONDS.

    Active jobs (PENDING/RUNNING) are NEVER reaped regardless of age.
    """
    now = time.monotonic()
    with _lock:
        to_remove = [
            jid
            for jid, job in _jobs.items()
            if job.is_terminal
            and job._terminal_at is not None
            and now - job._terminal_at > JOB_TTL_SECONDS
        ]
    for jid in to_remove:
        with _lock:
            job = _jobs.pop(jid, None)
        if job is not None:
            job.cleanup_files()
            logger.debug("Reaped terminal job %s", jid)
