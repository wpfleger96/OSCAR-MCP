"""In-memory job store for streaming import progress via SSE.

State machine
-------------
::

    reserve()             → PENDING_UPLOAD (admission slot taken *before* body is read)
    convert_to_job()      → PENDING       (reservation becomes a real job after parsing)
    GET /{id}/progress    → attaches an SSE observer; never starts or restarts the worker
    DELETE /{id}          → cancelled (idempotent after any terminal state)
    worker finishes       → succeeded | failed
    reaper                → removes terminal jobs after TTL

Terminal states: succeeded, failed, cancelled.
Active states:   pending_upload, pending, running.

Admission
---------
Per-user and global caps include PENDING_UPLOAD reservations + PENDING + RUNNING jobs
(one counter, one state machine).  An over-limit request gets 429 *before* any body
bytes are consumed.  The reservation converts atomically to the job after parsing.
At no instant does the pair double-count (both reservation + job) or drop the slot.

Resource ownership
------------------
The slot owns the disk it admitted.  Capacity is released **only after** temp/spool
cleanup completes on every terminal and error path.  The terminal job record is
retained for SSE observation independently of capacity.

Guarantees
----------
- Start-once: the worker starts exactly once at convert_to_job/POST; /progress GETs
  are observer-only.
- Fan-out: each observer has its own capacity-one/coalescing channel backed by the
  latest-progress snapshot.  Terminal delivery is never dropped.
- Late observers: connecting after terminal state immediately receive the terminal event.
- Reaper: removes terminal jobs only; active jobs are never reaped regardless of age.
- Shutdown: ``shutdown()`` cancels all non-terminal jobs and awaits worker threads.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# How long to retain terminal jobs before the reaper removes them.
JOB_TTL_SECONDS: float = 600.0

# Per-user and global admission caps.
# These are read from the app config at call time so tests can override via env.
# The module-level defaults are kept as fallbacks when config is not yet loaded.
_DEFAULT_MAX_ACTIVE_PER_USER: int = 3
_DEFAULT_MAX_ACTIVE_GLOBAL: int = 10


def _get_caps() -> tuple[int, int]:
    """Return (max_per_user, max_global) from config, falling back to defaults."""
    try:
        from snore.api.config import get_config  # noqa: PLC0415

        cfg = get_config()
        return cfg.max_jobs_per_user, cfg.max_jobs_global
    except Exception:
        return _DEFAULT_MAX_ACTIVE_PER_USER, _DEFAULT_MAX_ACTIVE_GLOBAL


class JobState(Enum):
    """All states a job can occupy."""

    PENDING_UPLOAD = "pending_upload"  # Admission slot reserved; body not yet parsed.
    PENDING = "pending"  # Files received; worker not yet started.
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})
ACTIVE_STATES = frozenset({JobState.PENDING_UPLOAD, JobState.PENDING, JobState.RUNNING})


class JobType(Enum):
    UPLOAD = "upload"
    PATH = "path"


class JobPhase(StrEnum):
    """Non-terminal phases within a RUNNING job.

    Delivered via phase_complete() as a live milestone to attached observers.
    NOT a terminal state — the job stays RUNNING through the import phase.
    """

    IMPORT = "import"


# ---------------------------------------------------------------------------
# Per-observer coalescing channel
# ---------------------------------------------------------------------------


class ObserverChannel:
    """Capacity-one coalescing notification channel for a single SSE observer.

    Each observer holds exactly one pending message slot.  A new message
    overwrites an un-consumed one (coalescing), *except* when the pending
    message is already a terminal event — terminal events are never dropped.
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
    owner_user_id: int | None = None  # The user who owns this job.
    target_profile_id: int | None = None  # The profile data lands in.
    created_at: float = field(default_factory=time.monotonic)

    # UPLOAD jobs: temp dir with written files.
    temp_dir: Path | None = None
    # PATH jobs: sources list.
    sources: list[Any] | None = None

    # File count — protected by _lock (written by the request handler before the worker starts).
    _file_count: int = field(default=0, init=False, repr=False)

    # State machine fields — protected by _lock.
    _state: JobState = field(default=JobState.PENDING_UPLOAD, init=False, repr=False)
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
    # True while the slot still counts against admission caps.
    _capacity_held: bool = field(default=True, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    # Import-phase durability fields — set after import commits, before analysis.
    # Carried in EVERY terminal payload produced after import_committed=True
    # (success, analysis failure, and cancellation) so that late observers and
    # stalled observers always learn that data landed (plan §A1, pass-4 IMPORTANT-1).
    _import_committed: bool = field(default=False, init=False, repr=False)
    _import_result: dict[str, Any] | None = field(default=None, init=False, repr=False)
    # Analysis linkage — set by the worker thread via set_analysis_link() after
    # the enqueue attempt and BEFORE _finish().  The list endpoint stitches from
    # these fields; the SSE terminal payload carries the same info independently.
    _analysis_job_id: str | None = field(default=None, init=False, repr=False)
    _analysis_queued: bool | None = field(default=None, init=False, repr=False)

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

    @property
    def import_committed(self) -> bool:
        """True after the import phase has committed to the database."""
        with self._lock:
            return self._import_committed

    @property
    def finished_at(self) -> float | None:
        """Monotonic timestamp when the job reached a terminal state, or None."""
        with self._lock:
            return self._terminal_at

    @property
    def analysis_job_id(self) -> str | None:
        """ID of the downstream analysis job, or None if not yet set."""
        with self._lock:
            return self._analysis_job_id

    @property
    def analysis_queued(self) -> bool | None:
        """True when analysis was enqueued; False when the queue was full; None when unknown."""
        with self._lock:
            return self._analysis_queued

    @property
    def file_count(self) -> int:
        """Number of files submitted with this job."""
        with self._lock:
            return self._file_count

    def set_file_count(self, n: int) -> None:
        """Record the number of files submitted with this job."""
        with self._lock:
            self._file_count = n

    @property
    def latest_progress_message(self) -> str | None:
        """The most recent progress message text, or None."""
        with self._lock:
            if self._latest_progress is None:
                return None
            data = self._latest_progress.get("data")
            if not isinstance(data, dict):
                return None
            return data.get("message")

    @property
    def error_message(self) -> str | None:
        """The error message from the terminal payload, only for FAILED or CANCELLED jobs."""
        with self._lock:
            if self._state not in (JobState.FAILED, JobState.CANCELLED):
                return None
            if self._terminal_msg is None:
                return None
            data = self._terminal_msg.get("data")
            if not isinstance(data, dict):
                return None
            return data.get("message")

    @property
    def sessions_imported(self) -> int | None:
        """Total sessions committed to the DB, or None if import has not committed."""
        with self._lock:
            if not self._import_committed or self._import_result is None:
                return None
            return self._import_result.get("total_imported")

    @property
    def import_result_snapshot(self) -> dict[str, Any] | None:
        """Shallow copy of the import result dict, or None if not yet committed.

        Returns a SHALLOW copy — top-level dict copied, nested lists/dicts shared;
        callers must treat it as read-only (deliberate perf choice).
        """
        with self._lock:
            if not self._import_committed or self._import_result is None:
                return None
            return dict(self._import_result)

    def set_analysis_link(
        self,
        *,
        analysis_job_id: str | None,
        queue_full: bool,
    ) -> None:
        """Record the outcome of the analysis enqueue attempt.

        ``queue_full`` is True when the enqueue attempt was rejected because the
        analysis queue was full (not when the queued job later failed).

        Called by the worker thread after the enqueue attempt and BEFORE _finish() —
        the list endpoint stitches from these fields; the SSE terminal payload carries
        the same info independently.
        """
        with self._lock:
            self._analysis_job_id = analysis_job_id
            if analysis_job_id is not None:
                self._analysis_queued = True
            elif queue_full:
                self._analysis_queued = False
            # else leave _analysis_queued as None (no sessions imported, no attempt)

    def convert_to_pending(self) -> bool:
        """Transition PENDING_UPLOAD → PENDING (files received, body parsed).

        This is the atomic reservation→job conversion: at no instant does the
        counter both double-count (reservation + job) or drop the slot.
        """
        with self._lock:
            if self._state != JobState.PENDING_UPLOAD:
                return False
            self._state = JobState.PENDING
            return True

    def attach_observer(self) -> ObserverChannel:
        """Register a new SSE observer.

        If the job is already terminal, the channel is pre-loaded with the
        terminal event so the observer receives it immediately.
        """
        ch = ObserverChannel()
        with self._lock:
            if self._state in TERMINAL_STATES:
                if self._terminal_msg is not None:
                    ch.put(self._terminal_msg)
            else:
                if self._latest_progress is not None:
                    ch.put(self._latest_progress)
                self._observers.append(ch)
        return ch

    def detach_observer(self, ch: ObserverChannel) -> None:
        """Remove an observer (SSE connection closed)."""
        with self._lock:
            try:
                self._observers.remove(ch)
            except ValueError:
                pass
        ch.close()

    def _broadcast(self, msg: dict[str, Any]) -> None:
        """Deliver msg to all attached observers. Must be called without _lock."""
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

    def phase_complete(self, phase: JobPhase, import_result: dict[str, Any]) -> None:
        """Record import-phase completion and broadcast a non-terminal milestone.

        This is NOT a terminal state — the job stays RUNNING.  The event is
        coalesced like any progress message: late/stalled observers may miss it.
        The import outcome is persisted on the job itself so that every subsequent
        terminal payload (success, analysis failure, or cancellation) carries it —
        this is the durability guarantee (plan §A1, pass-4 IMPORTANT-1).

        Must be called by the worker thread only, after the import transaction
        commits and BEFORE the analysis phase begins.
        """
        with self._lock:
            self._import_committed = True
            self._import_result = import_result
        milestone = {
            "event": "phase_complete",
            "data": {
                "phase": phase.value,
                "import_result": import_result,
            },
        }
        self._broadcast(milestone)

    def try_start(self) -> bool:
        """Transition pending → running."""
        with self._lock:
            if self._state != JobState.PENDING:
                return False
            self._state = JobState.RUNNING
            return True

    def try_cancel(self) -> bool:
        """Request cancellation.

        Allowed from any state.  Idempotent after terminal.
        Returns True if the job was in a non-terminal state.
        """
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            self._cancel_flag = True
            if self._state in (JobState.PENDING_UPLOAD, JobState.PENDING):
                self._state = JobState.CANCELLED
                terminal_data: dict[str, Any] = {"message": "Cancelled"}
                if self._import_committed and self._import_result is not None:
                    terminal_data["import_committed"] = True
                    terminal_data["import_result"] = self._import_result
                terminal_msg = {"event": "error", "data": terminal_data}
                self._terminal_msg = terminal_msg
                self._terminal_at = time.monotonic()
                observers = list(self._observers)
            else:
                observers = []
        if observers:
            for ch in observers:
                ch.put(terminal_msg)
        return True

    def _finish(self, *, succeeded: bool, terminal_msg: dict[str, Any]) -> bool:
        """Transition running → succeeded/failed/cancelled. Called by the worker.

        NOTE: The caller must call release_capacity() AFTER cleanup_files()
        completes to ensure the slot owns the disk it admitted.
        """
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            if self._cancel_flag:
                self._state = JobState.CANCELLED
                cancel_data: dict[str, Any] = {"message": "Cancelled"}
                if self._import_committed and self._import_result is not None:
                    cancel_data["import_committed"] = True
                    cancel_data["import_result"] = self._import_result
                terminal_msg = {"event": "error", "data": cancel_data}
            else:
                self._state = JobState.SUCCEEDED if succeeded else JobState.FAILED
                # Embed import outcome in every terminal payload after import committed.
                if self._import_committed and self._import_result is not None:
                    data = terminal_msg.get("data", {})
                    if isinstance(data, dict):
                        data["import_committed"] = True
                        data["import_result"] = self._import_result
                        terminal_msg = dict(terminal_msg)
                        terminal_msg["data"] = data
            self._terminal_msg = terminal_msg
            self._terminal_at = time.monotonic()
            observers = list(self._observers)
            self._observers.clear()
        for ch in observers:
            ch.put(terminal_msg)
        return True

    def _finish_cancelled(self) -> bool:
        """Transition running → CANCELLED. Called by the worker thread."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            cancel_data: dict[str, Any] = {"message": "Cancelled"}
            if self._import_committed and self._import_result is not None:
                cancel_data["import_committed"] = True
                cancel_data["import_result"] = self._import_result
            terminal_msg = {"event": "error", "data": cancel_data}
            self._state = JobState.CANCELLED
            self._terminal_msg = terminal_msg
            self._terminal_at = time.monotonic()
            observers = list(self._observers)
            self._observers.clear()
        for ch in observers:
            ch.put(terminal_msg)
        return True

    def release_capacity(self) -> None:
        """Release the admission slot AFTER cleanup completes.

        Must be called AFTER cleanup_files() on every terminal/error path.
        The slot owns the disk it admitted; releasing early would allow a new
        request to reserve and spool while the prior job's temp tree still exists.
        """
        with self._lock:
            if not self._capacity_held:
                return
            self._capacity_held = False
        # Decrement the store counter outside the job lock to avoid lock ordering issues.
        _decrement_capacity(self.owner_user_id)

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
# Job store and admission counters
# ---------------------------------------------------------------------------

_jobs: dict[str, ImportJob] = {}
_lock = threading.Lock()

# Admission counters: per-user active count and global active count.
# "Active" = PENDING_UPLOAD + PENDING + RUNNING (capacity_held=True).
_per_user_count: dict[int | None, int] = {}
_global_count: int = 0
_counts_lock = threading.Lock()


def _decrement_capacity(owner_user_id: int | None) -> None:
    """Decrement both per-user and global counts for one released slot."""
    global _global_count
    with _counts_lock:
        current = _per_user_count.get(owner_user_id, 0)
        _per_user_count[owner_user_id] = max(0, current - 1)
        _global_count = max(0, _global_count - 1)


def _check_and_reserve(owner_user_id: int | None) -> bool:
    """Atomically check caps and increment counters.

    Returns True if the reservation was taken (within caps), False if over-limit.
    """
    global _global_count
    max_per_user, max_global = _get_caps()
    with _counts_lock:
        user_count = _per_user_count.get(owner_user_id, 0)
        if user_count >= max_per_user:
            return False
        if _global_count >= max_global:
            return False
        _per_user_count[owner_user_id] = user_count + 1
        _global_count += 1
        return True


def reserve_slot(owner_user_id: int | None) -> ImportJob | None:
    """Atomically check caps and create a PENDING_UPLOAD reservation.

    Must be called BEFORE reading any body bytes.

    Returns:
        A new ImportJob in PENDING_UPLOAD state, or None if over-limit (429).
    """
    if not _check_and_reserve(owner_user_id):
        return None
    _reap_terminal()
    job = ImportJob(
        job_id=uuid.uuid4().hex,
        job_type=JobType.UPLOAD,
        owner_user_id=owner_user_id,
    )
    with _lock:
        _jobs[job.job_id] = job
    logger.debug(
        "admission: reserved slot for user %s (job %s)", owner_user_id, job.job_id
    )
    return job


def create_job(
    job_type: JobType, owner_user_id: int | None = None, **kwargs: Any
) -> ImportJob:
    """Create a job directly in PENDING state (for PATH jobs which have no upload phase).

    Also increments caps.  If over-limit, raises RuntimeError.
    """
    if not _check_and_reserve(owner_user_id):
        raise RuntimeError("Admission caps exceeded")
    _reap_terminal()
    job = ImportJob(
        job_id=uuid.uuid4().hex,
        job_type=job_type,
        owner_user_id=owner_user_id,
        **kwargs,
    )
    # PATH jobs start directly in PENDING (no upload phase).
    job._state = JobState.PENDING
    with _lock:
        _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> ImportJob | None:
    _reap_terminal()
    with _lock:
        return _jobs.get(job_id)


def list_jobs(owner_user_id: int | None = None) -> list[ImportJob]:
    """Return jobs visible to *owner_user_id*, reaping expired terminal jobs first.

    A job with owner_user_id=None is visible to any caller (local-mode parity).
    A job with a set owner is visible only to that owner.
    When *owner_user_id* is None the caller receives all jobs.
    """
    _reap_terminal()
    with _lock:
        jobs_snapshot = list(_jobs.values())
    if owner_user_id is None:
        return jobs_snapshot
    return [
        job
        for job in jobs_snapshot
        if job.owner_user_id is None or job.owner_user_id == owner_user_id
    ]


def remove_job(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


def cancel_job(job_id: str) -> bool:
    """Cancel a job. Returns True if the job existed and was not already terminal."""
    job = get_job(job_id)
    if job is None:
        return False
    return job.try_cancel()


def shutdown(timeout: float = 10.0) -> list[str]:
    """Cancel all non-terminal jobs and wait for worker threads to exit."""
    with _lock:
        active = [j for j in _jobs.values() if not j.is_terminal]
    for job in active:
        job.try_cancel()
    still_alive: list[str] = []
    for job in active:
        job.wait_for_worker(timeout=timeout)
        t = job._worker_thread
        if t is not None and t.is_alive():
            logger.warning(
                "Worker thread for job %s still alive after %.1fs shutdown timeout",
                job.job_id,
                timeout,
            )
            still_alive.append(job.job_id)
    return still_alive


def start_reaper(interval: float = 60.0) -> tuple[threading.Thread, threading.Event]:
    """Start a background daemon thread that reaps terminal jobs every *interval* seconds."""
    stop_event = threading.Event()

    def _reap_loop() -> None:
        while not stop_event.wait(timeout=interval):
            try:
                _reap_terminal()
            except Exception:
                logger.exception("Reaper iteration failed")

    t = threading.Thread(target=_reap_loop, daemon=True, name="import-job-reaper")
    t.start()
    return t, stop_event


def _reap_terminal() -> None:
    """Remove terminal jobs older than JOB_TTL_SECONDS. Active jobs are NEVER reaped."""
    now = time.monotonic()
    with _lock:
        to_remove = [
            jid
            for jid, job in _jobs.items()
            if job.is_terminal
            and not job._capacity_held
            and job._terminal_at is not None
            and now - job._terminal_at > JOB_TTL_SECONDS
        ]
    for jid in to_remove:
        with _lock:
            job = _jobs.pop(jid, None)
        if job is not None:
            logger.debug("Reaped terminal job %s", jid)
