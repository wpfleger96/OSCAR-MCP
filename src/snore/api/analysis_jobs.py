"""In-memory FIFO analysis job queue.

A single persistent worker thread pulls one job at a time (SQLite
single-writer constraint).  Jobs are retained for JOB_TTL_SECONDS
after completion for status queries, then reaped.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
import uuid

from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

JOB_TTL_SECONDS: float = 600.0
MAX_QUEUED: int = 10


class AnalysisJobState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    source: str  # "import" | "batch"
    owner_user_id: int | None = None
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

    def to_dict(self) -> dict[str, object]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "profile_id": self.profile_id,
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
    source: str,
    owner_user_id: int | None = None,
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
    with _lock:
        jobs = list(_all_jobs.values())
    if owner_user_id is not None:
        jobs = [j for j in jobs if j.owner_user_id == owner_user_id]
    return jobs


def cancel_job(job_id: str) -> bool:
    """Set cancel flag. If QUEUED, immediately transitions to CANCELLED.

    Returns True if the job was found and not already terminal.
    """
    with _condition:
        job = _all_jobs.get(job_id)
        if job is None:
            return False
        with job._lock:
            if job._state in TERMINAL_STATES:
                return False
            job._cancel_flag = True
            if job._state == AnalysisJobState.QUEUED:
                job._state = AnalysisJobState.CANCELLED
                job._finished_at = time.monotonic()
                try:
                    _queue.remove(job)
                except ValueError:
                    pass
    return True


def _reap_terminal() -> None:
    now = time.monotonic()
    with _lock:
        to_remove = [
            jid
            for jid, job in _all_jobs.items()
            if job.is_terminal
            and job._finished_at is not None
            and now - job._finished_at > JOB_TTL_SECONDS
        ]
        for jid in to_remove:
            _all_jobs.pop(jid, None)
            logger.debug("Reaped terminal analysis job %s", jid)


async def _run_analysis(job: AnalysisJob) -> None:
    from snore.analysis.modes.config import DEFAULT_MODE  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415
    from snore.services.analysis_facade import AnalysisFacade  # noqa: PLC0415

    async with session_scope() as db:
        facade = AnalysisFacade(db, profile_id=job.profile_id)

        def _progress(done: int, total: int | None) -> None:
            with job._lock:
                job._progress_completed = done
                job._progress_total = total or 0

        await facade.run_batch_analysis(
            session_ids=job.session_ids,
            primary_mode=DEFAULT_MODE,
            progress_callback=_progress,
            max_workers=1,
        )


def _execute_job(job: AnalysisJob) -> None:
    with job._lock:
        job._state = AnalysisJobState.RUNNING
        job._started_at = time.monotonic()
    try:
        asyncio.run(_run_analysis(job))
        with job._lock:
            job._state = AnalysisJobState.SUCCEEDED
    except Exception as exc:
        logger.exception("Analysis job %s failed", job.job_id)
        with job._lock:
            job._state = AnalysisJobState.FAILED
            job._error_message = str(exc)
    finally:
        with job._lock:
            job._finished_at = time.monotonic()


def _worker_loop(stop_event: threading.Event, condition: threading.Condition) -> None:
    while not stop_event.is_set():
        with condition:
            while not _queue and not stop_event.is_set():
                condition.wait(timeout=1.0)
            if stop_event.is_set():
                break
            job = _queue.popleft() if _queue else None
        if job is None:
            continue
        if job.cancel_requested:
            with job._lock:
                job._state = AnalysisJobState.CANCELLED
                job._finished_at = time.monotonic()
            continue
        _execute_job(job)
        _reap_terminal()


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
    """Signal the worker to stop and cancel any running job."""
    global _worker_thread, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    # Cancel any running job so _run_analysis can observe it.
    with _lock:
        for job in _all_jobs.values():
            with job._lock:
                if job._state == AnalysisJobState.RUNNING:
                    job._cancel_flag = True
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
