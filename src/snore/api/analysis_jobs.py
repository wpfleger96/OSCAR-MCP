"""In-memory FIFO analysis job queue.

A single persistent worker thread pulls one job at a time; the queue
itself remains strictly sequential.  Within each job, sessions run with
configurable concurrency (``SNORE_ANALYSIS_MAX_WORKERS``, default 4):
the write gate serializes SQLite writes while the read/compute phases
overlap safely.  Jobs are retained for JOB_TTL_SECONDS after completion
for status queries, then reaped.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
import uuid

from dataclasses import dataclass, field
from enum import Enum, StrEnum

from snore.api.import_jobs import JOB_TTL_SECONDS

logger = logging.getLogger(__name__)

MAX_QUEUED: int = 10

# Per-analysis-job session concurrency.  Mirrors the config default so tests
# that skip full lifespan setup get a sensible fallback without loading config.
_DEFAULT_ANALYSIS_MAX_WORKERS: int = 4


def _get_analysis_workers() -> int:
    """Return analysis_max_workers from config, falling back to the module default."""
    try:
        from snore.api.config import get_config  # noqa: PLC0415

        return get_config().analysis_max_workers
    except Exception:
        return _DEFAULT_ANALYSIS_MAX_WORKERS


class AnalysisJobState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisJobSource(StrEnum):
    IMPORT = "import"
    BATCH = "batch"


TERMINAL_STATES = frozenset(
    {
        AnalysisJobState.SUCCEEDED,
        AnalysisJobState.FAILED,
        AnalysisJobState.CANCELLED,
    }
)


@dataclass
class AnalysisJob:
    job_id: str
    profile_id: int
    session_ids: list[int]
    source: AnalysisJobSource
    owner_user_id: int | None = None
    modes: list[str] | None = None
    primary_mode: str | None = None
    store_results: bool = True
    created_at: float = field(default_factory=time.monotonic)

    _state: AnalysisJobState = field(
        default=AnalysisJobState.QUEUED, init=False, repr=False
    )
    _progress_completed: int = field(default=0, init=False, repr=False)
    _progress_total: int = field(default=0, init=False, repr=False)
    _error_message: str | None = field(default=None, init=False, repr=False)
    _started_at: float | None = field(default=None, init=False, repr=False)
    _finished_at: float | None = field(default=None, init=False, repr=False)
    _cancel_flag: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    @property
    def state(self) -> AnalysisJobState:
        with self._lock:
            return self._state

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._state in TERMINAL_STATES

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_flag

    @property
    def progress_completed(self) -> int:
        with self._lock:
            return self._progress_completed

    @property
    def progress_total(self) -> int:
        with self._lock:
            return self._progress_total

    @property
    def error_message(self) -> str | None:
        with self._lock:
            return self._error_message

    @property
    def started_at(self) -> float | None:
        with self._lock:
            return self._started_at

    @property
    def finished_at(self) -> float | None:
        with self._lock:
            return self._finished_at

    def try_start(self) -> bool:
        """Atomically transition QUEUED → RUNNING, or QUEUED → CANCELLED if cancel was set.

        Closes the TOCTOU window between dequeue and RUNNING transition.
        Returns True if the job is now RUNNING; False if it was cancelled instead.
        """
        with self._lock:
            if self._cancel_flag:
                self._state = AnalysisJobState.CANCELLED
                self._finished_at = time.monotonic()
                return False
            self._state = AnalysisJobState.RUNNING
            self._started_at = time.monotonic()
            return True

    def finish(self, succeeded: bool, error_message: str | None = None) -> None:
        """Transition to a terminal state. Respects cancel flag.

        If the cancel flag is set, the job always lands in CANCELLED regardless of
        the ``succeeded`` argument.  Idempotent once already terminal.
        """
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            if self._cancel_flag:
                self._state = AnalysisJobState.CANCELLED
            elif succeeded:
                self._state = AnalysisJobState.SUCCEEDED
            else:
                self._state = AnalysisJobState.FAILED
                self._error_message = error_message
            self._finished_at = time.monotonic()

    def try_cancel(self) -> bool:
        """Set the cancel flag; if QUEUED, immediately transition to CANCELLED.

        Returns True if the job was in a non-terminal state.
        Queue removal must be handled at the module level under _condition.
        """
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            self._cancel_flag = True
            if self._state == AnalysisJobState.QUEUED:
                self._state = AnalysisJobState.CANCELLED
                self._finished_at = time.monotonic()
            return True

    def update_progress(self, done: int, total: int | None) -> None:
        """Update progress counters; safe to call from any thread."""
        with self._lock:
            self._progress_completed = done
            self._progress_total = total or 0

    def to_dict(self) -> dict[str, object]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "session_count": len(self.session_ids),
                "source": self.source,
                "owner_user_id": self.owner_user_id,
                "state": self._state.value,
                "progress_completed": self._progress_completed,
                "progress_total": self._progress_total,
                "error_message": self._error_message,
                "created_at": self.created_at,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
            }


# ---------------------------------------------------------------------------
# Module-level store
# ---------------------------------------------------------------------------

_queue: collections.deque[AnalysisJob] = collections.deque()
_all_jobs: dict[str, AnalysisJob] = {}
_lock = threading.Lock()
_condition = threading.Condition(_lock)

_worker_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None


def enqueue(
    profile_id: int,
    session_ids: list[int],
    source: AnalysisJobSource,
    owner_user_id: int | None = None,
    modes: list[str] | None = None,
    primary_mode: str | None = None,
    store_results: bool = True,
) -> AnalysisJob | None:
    """Create a QUEUED job and append it to the queue.

    Returns None if the queue is already at MAX_QUEUED.
    """
    with _condition:
        if len(_queue) >= MAX_QUEUED:
            return None
        job = AnalysisJob(
            job_id=uuid.uuid4().hex,
            profile_id=profile_id,
            session_ids=list(session_ids),
            source=source,
            owner_user_id=owner_user_id,
            modes=modes,
            primary_mode=primary_mode,
            store_results=store_results,
        )
        _queue.append(job)
        _all_jobs[job.job_id] = job
        _condition.notify_all()
    logger.debug(
        "Enqueued analysis job %s (source=%s, sessions=%d)",
        job.job_id,
        source,
        len(session_ids),
    )
    return job


def get_job(job_id: str) -> AnalysisJob | None:
    with _lock:
        return _all_jobs.get(job_id)


def list_jobs(owner_user_id: int | None = None) -> list[AnalysisJob]:
    """Return jobs visible to *owner_user_id*.

    A job with owner_user_id=None is visible to any caller (local-mode parity).
    A job with a set owner is visible only to that owner.
    When *owner_user_id* is None the caller receives all jobs.
    """
    with _lock:
        all_jobs = list(_all_jobs.values())
    if owner_user_id is None:
        return all_jobs
    return [
        j
        for j in all_jobs
        if j.owner_user_id is None or j.owner_user_id == owner_user_id
    ]


def cancel_job(job_id: str) -> bool:
    """Set cancel flag. If QUEUED, immediately transitions to CANCELLED.

    Returns True if the job was found and not already terminal.
    """
    with _condition:
        job = _all_jobs.get(job_id)
        if job is None:
            return False
        result = job.try_cancel()
        if result:
            try:
                _queue.remove(job)
            except ValueError:
                pass
    return result


# Reaper pattern: reaped inline inside the worker loop — after each job completes
# and on idle timeouts.  The analysis store is accessed only by the worker thread
# and a low-frequency list endpoint, so the eager-reap-on-read approach used by
# import_jobs (which also runs a dedicated background reaper thread) is unnecessary
# here.  import_jobs needs eager reap because HTTP handlers poll it on every
# GET /import/{id}/progress and GET /import/jobs request.


def _reap_terminal() -> None:
    now = time.monotonic()
    with _lock:
        to_remove = [
            jid
            for jid, job in _all_jobs.items()
            if job.is_terminal
            and (fa := job.finished_at) is not None
            and now - fa > JOB_TTL_SECONDS
        ]
        for jid in to_remove:
            _all_jobs.pop(jid, None)
            logger.debug("Reaped terminal analysis job %s", jid)


async def _run_analysis(job: AnalysisJob) -> None:
    from snore.database.session import session_scope  # noqa: PLC0415
    from snore.services.analysis_facade import AnalysisFacade  # noqa: PLC0415

    async with session_scope() as db:
        facade = AnalysisFacade(db, profile_id=job.profile_id)
        await facade.run_batch_analysis(
            session_ids=job.session_ids,
            modes=job.modes,
            primary_mode=job.primary_mode,
            store_results=job.store_results,
            progress_callback=job.update_progress,
            cancel_predicate=lambda: job.cancel_requested,
            max_workers=_get_analysis_workers(),
        )


def _execute_job(job: AnalysisJob) -> None:
    if not job.try_start():
        # Cancel arrived before RUNNING transition — job is already CANCELLED.
        return
    try:
        asyncio.run(_run_analysis(job))
        # finish() checks the cancel flag: sets CANCELLED if requested, SUCCEEDED otherwise.
        job.finish(succeeded=True)
    except Exception as exc:
        logger.exception("Analysis job %s failed", job.job_id)
        job.finish(succeeded=False, error_message=str(exc))


def _worker_loop(stop_event: threading.Event, condition: threading.Condition) -> None:
    last_reap = time.monotonic()
    while not stop_event.is_set():
        job: AnalysisJob | None = None
        with condition:
            if not _queue and not stop_event.is_set():
                condition.wait(timeout=1.0)
            if not stop_event.is_set() and _queue:
                job = _queue.popleft()

        if job is None:
            # Idle timeout — reap at most every 60 s.
            now = time.monotonic()
            if now - last_reap >= 60.0:
                _reap_terminal()
                last_reap = now
            continue

        try:
            _execute_job(job)
        except BaseException as exc:
            logger.exception(
                "Unexpected error in analysis worker for job %s", job.job_id
            )
            if not job.is_terminal:
                try:
                    job.finish(succeeded=False, error_message=str(exc))
                except Exception:
                    pass
            # Do NOT re-raise — the worker thread must stay alive.

        _reap_terminal()
        last_reap = time.monotonic()


def start_worker() -> tuple[threading.Thread, threading.Event]:
    """Start the single persistent worker thread.

    Returns (thread, stop_event) so the caller can shut it down.
    """
    global _worker_thread, _stop_event
    stop_event = threading.Event()
    _stop_event = stop_event
    t = threading.Thread(
        target=_worker_loop,
        args=(stop_event, _condition),
        daemon=True,
        name="analysis-job-worker",
    )
    _worker_thread = t
    t.start()
    return t, stop_event


def shutdown(timeout: float = 10.0) -> None:
    """Signal the worker to stop; cancel all queued and running jobs."""
    global _worker_thread, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    with _condition:
        for job in list(_all_jobs.values()):
            job.try_cancel()
        _queue.clear()
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
