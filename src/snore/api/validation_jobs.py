"""In-memory FIFO validation-run job queue plus the ``validation_runs`` writes.

Structurally a twin of :mod:`snore.api.analysis_jobs` — same lock/state/cancel
core (:class:`~snore.api.jobs.core.JobRecordBase`), same
:class:`~snore.api.jobs.core.JobStore`, same
:func:`~snore.api.jobs.core.run_worker_loop` skeleton, same inline TTL reaping.

One deliberate divergence: there is **no separate job-record table**.  A
validation run's results *are* the row, so the worker writes its state and the
whole ``report_json`` blob directly into ``validation_runs`` (upserting on the
row ``id`` via :func:`~snore.api.jobs.durability.upsert_job_record`).  The queued
row is inserted by the enqueuing request so the run has a stable integer
``run_id`` to return immediately; the worker then transitions it in place.

Recovery is intentionally lighter than analysis: orphaned non-terminal rows are
marked failed at startup and NOT auto-resumed — validation is idempotent, so the
user simply re-runs.  Retention keeps the newest ``_RETENTION_PER_GROUP`` rows
per ``(profile_id, validator_type)``; older rows are pruned in the startup sweep.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from snore.api.jobs.core import (
    JOB_TTL_SECONDS,
    JobRecordBase,
    JobStore,
)
from snore.api.jobs.pool import WorkerPool

if TYPE_CHECKING:
    from snore.database.models import ValidationRun

logger = logging.getLogger(__name__)

MAX_QUEUED: int = 10

# Concurrent validation workers.  One is plenty: a validation run deserializes
# FLG waveforms over hundreds of sessions and the SQLite write lock serialises
# anyway, so extra workers would only contend.  Sized small on purpose.
_DEFAULT_VALIDATION_JOB_CONCURRENCY: int = 1

# Newest rows retained per (profile_id, validator_type); older pruned at startup.
_RETENTION_PER_GROUP: int = 50


def _get_job_concurrency() -> int:
    """Return the number of concurrent validation workers (module default)."""
    return _DEFAULT_VALIDATION_JOB_CONCURRENCY


class ValidationRunState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        ValidationRunState.SUCCEEDED,
        ValidationRunState.FAILED,
        ValidationRunState.CANCELLED,
    }
)


@dataclass(kw_only=True)
class ValidationRunJob(JobRecordBase[ValidationRunState]):
    _TERMINAL_STATES = TERMINAL_STATES

    run_id: int
    profile_id: int
    validator_type: str
    date_from: date
    date_to: date
    engine_identity: dict[str, Any]
    validator_params: dict[str, Any]

    _state: ValidationRunState = field(
        default=ValidationRunState.QUEUED, init=False, repr=False
    )
    _error_message: str | None = field(default=None, init=False, repr=False)
    _finished_at: float | None = field(default=None, init=False, repr=False)
    # The serialised report, set once the run succeeds; persisted at terminal.
    _report_json: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @property
    def error_message(self) -> str | None:
        with self._lock:
            return self._error_message

    @property
    def finished_at(self) -> float | None:
        with self._lock:
            return self._finished_at

    def set_report(self, report_json: dict[str, Any]) -> None:
        with self._lock:
            self._report_json = report_json

    @property
    def report_json(self) -> dict[str, Any] | None:
        with self._lock:
            return self._report_json

    def try_start(self) -> bool:
        """Atomically transition QUEUED → RUNNING, or → CANCELLED if cancel was set."""
        with self._lock:
            if self._cancel_flag:
                self._state = ValidationRunState.CANCELLED
                self._finished_at = time.monotonic()
                self._finished_at_wall = datetime.now(UTC)
                return False
            self._start_running(ValidationRunState.RUNNING)
            return True

    def finish(self, succeeded: bool, error_message: str | None = None) -> None:
        """Transition to a terminal state. A pending cancel forces CANCELLED."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            if self._cancel_flag:
                self._state = ValidationRunState.CANCELLED
            elif succeeded:
                self._state = ValidationRunState.SUCCEEDED
            else:
                self._state = ValidationRunState.FAILED
                self._error_message = error_message
            self._finished_at = time.monotonic()
            self._finished_at_wall = datetime.now(UTC)

    def _on_cancel_locked(self) -> None:
        """Eager-terminalize a QUEUED job. Caller holds ``_lock`` (see base)."""
        if self._state == ValidationRunState.QUEUED:
            self._state = ValidationRunState.CANCELLED
            self._finished_at = time.monotonic()
            self._finished_at_wall = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """A ``model_validate`` snapshot containing wall-clock datetime values."""
        with self._lock:
            return {
                "run_id": self.run_id,
                "job_id": self.job_id,
                "validator_type": self.validator_type,
                "date_from": self.date_from.isoformat(),
                "date_to": self.date_to.isoformat(),
                "state": self._state.value,
                "error_message": self._error_message,
                "engine_identity": self.engine_identity,
                "validator_params": self.validator_params,
                "owner_user_id": self.owner_user_id,
                "created_at": self.created_at_wall,
                "started_at": self._started_at_wall,
                "finished_at": self._finished_at_wall,
                "reused": False,
            }


# ---------------------------------------------------------------------------
# Module-level store (aliases bound to the live JobStore objects — never rebind)
# ---------------------------------------------------------------------------

_queue: collections.deque[ValidationRunJob] = collections.deque()
_store: JobStore[ValidationRunJob] = JobStore()
_all_jobs = _store.jobs
_lock = _store.lock
_condition = threading.Condition(_lock)


def enqueue(
    *,
    run_id: int,
    profile_id: int,
    validator_type: str,
    date_from: date,
    date_to: date,
    engine_identity: dict[str, Any],
    validator_params: dict[str, Any],
    job_id: str,
    owner_user_id: int | None = None,
) -> ValidationRunJob | None:
    """Register a QUEUED in-memory job for an already-inserted ``validation_runs`` row.

    Returns None if the queue is already at MAX_QUEUED (the caller then deletes
    the orphaned queued row and returns 429).
    """
    with _condition:
        if len(_queue) >= MAX_QUEUED:
            return None
        job = ValidationRunJob(
            job_id=job_id,
            run_id=run_id,
            profile_id=profile_id,
            validator_type=validator_type,
            date_from=date_from,
            date_to=date_to,
            engine_identity=engine_identity,
            validator_params=validator_params,
            owner_user_id=owner_user_id,
        )
        _queue.append(job)
        _all_jobs[job.job_id] = job
        _condition.notify_all()
    logger.debug(
        "Enqueued validation run %s (type=%s, run_id=%d)",
        job.job_id,
        validator_type,
        run_id,
    )
    return job


def get_job(job_id: str) -> ValidationRunJob | None:
    return _store.get(job_id)


def list_jobs(owner_user_id: int | None = None) -> list[ValidationRunJob]:
    return _store.list_visible_to(owner_user_id)


def cancel_job(job_id: str) -> bool:
    """Set cancel flag; evict from the queue if still QUEUED."""
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


def forget(job_id: str) -> None:
    """Drop a job's in-memory twin from the store (used after its row is deleted).

    A terminal job lingers in the store until the TTL reaper removes it (up to
    ``JOB_TTL_SECONDS``).  When the DELETE handler removes the row, it must also
    forget the twin, or the merged ``GET /runs`` list would resurrect the just-
    deleted run from memory.  No-op if the job is already gone.
    """
    _store.remove(job_id)


def has_running_jobs() -> bool:
    """True if any validation run is currently RUNNING.

    Read by the ``/health/busy`` gate so a multi-minute run is not interrupted
    by a watchtower container replacement.  QUEUED jobs are excluded: they have
    no in-progress writes, and validation is idempotent (the user re-runs).
    """
    with _lock:
        return any(j.state == ValidationRunState.RUNNING for j in _all_jobs.values())


def _reap_terminal() -> None:
    removed = _store.reap(JOB_TTL_SECONDS, terminal_at=lambda job: job.finished_at)
    for jid in removed:
        logger.debug("Reaped terminal validation run job %s", jid)


# ---------------------------------------------------------------------------
# validation_runs table writes
# ---------------------------------------------------------------------------


async def _find_matching_run(
    db: Any,
    *,
    states: list[str],
    profile_id: int,
    validator_type: str,
    date_from: date,
    date_to: date,
    engine_identity: dict[str, Any],
    validator_params: dict[str, Any],
    owner_user_id: int | None,
) -> ValidationRun | None:
    """Return the newest run in one of ``states`` matching the full dedup key.

    The ``(profile_id, validator_type, date_from, date_to)`` prefix is filtered
    in SQL (backed by ``ix_validation_runs_dedup``); the two JSON components are
    compared in Python as dicts so key ordering is irrelevant.  Visibility
    follows the same own-or-unowned rule as the job list.  The report blob is
    deferred — the dedup comparison never reads it.
    """
    from sqlalchemy import or_, select  # noqa: PLC0415
    from sqlalchemy.orm import defer  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415

    stmt = (
        select(models.ValidationRun)
        .where(
            models.ValidationRun.profile_id == profile_id,
            models.ValidationRun.validator_type == validator_type,
            models.ValidationRun.date_from == date_from,
            models.ValidationRun.date_to == date_to,
            models.ValidationRun.state.in_(states),
            or_(
                models.ValidationRun.owner_user_id == owner_user_id,
                models.ValidationRun.owner_user_id.is_(None),
            ),
        )
        .options(defer(models.ValidationRun.report_json))
        .order_by(models.ValidationRun.created_at.desc())
    )
    for row in (await db.execute(stmt)).scalars():
        if (
            row.engine_identity_json == engine_identity
            and row.validator_params_json == validator_params
        ):
            return row  # type: ignore[no-any-return]
    return None


async def find_reusable_run(
    db: Any,
    *,
    profile_id: int,
    validator_type: str,
    date_from: date,
    date_to: date,
    engine_identity: dict[str, Any],
    validator_params: dict[str, Any],
    owner_user_id: int | None,
) -> ValidationRun | None:
    """Return the newest SUCCEEDED run matching the full dedup key, or None."""
    return await _find_matching_run(
        db,
        states=[ValidationRunState.SUCCEEDED.value],
        profile_id=profile_id,
        validator_type=validator_type,
        date_from=date_from,
        date_to=date_to,
        engine_identity=engine_identity,
        validator_params=validator_params,
        owner_user_id=owner_user_id,
    )


async def find_inflight_run(
    db: Any,
    *,
    profile_id: int,
    validator_type: str,
    date_from: date,
    date_to: date,
    engine_identity: dict[str, Any],
    validator_params: dict[str, Any],
    owner_user_id: int | None,
) -> ValidationRun | None:
    """Return an already QUEUED/RUNNING run matching the full dedup key, or None.

    Lets the enqueue path collapse a duplicate request onto the in-flight run
    instead of queueing a second identical job (``force=True`` still bypasses).
    """
    return await _find_matching_run(
        db,
        states=[ValidationRunState.QUEUED.value, ValidationRunState.RUNNING.value],
        profile_id=profile_id,
        validator_type=validator_type,
        date_from=date_from,
        date_to=date_to,
        engine_identity=engine_identity,
        validator_params=validator_params,
        owner_user_id=owner_user_id,
    )


async def insert_queued_run(
    *,
    job_id: str,
    profile_id: int,
    owner_user_id: int | None,
    validator_type: str,
    date_from: date,
    date_to: date,
    engine_identity: dict[str, Any],
    validator_params: dict[str, Any],
) -> int:
    """Insert a QUEUED run row in its own committed transaction; return its id.

    Committed before the in-memory job is enqueued so the worker's upsert (keyed
    on ``id``) always finds the row rather than racing to re-create it.
    """
    from snore.database import models  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    now = datetime.now(UTC)
    async with session_scope(immediate=True) as db:
        run = models.ValidationRun(
            job_id=job_id,
            profile_id=profile_id,
            owner_user_id=owner_user_id,
            validator_type=validator_type,
            date_from=date_from,
            date_to=date_to,
            engine_identity_json=engine_identity,
            validator_params_json=validator_params,
            report_json=None,
            state=ValidationRunState.QUEUED.value,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        await db.flush()
        return run.id


async def create_sync_run(
    db: Any,
    *,
    profile_id: int,
    owner_user_id: int | None,
    validator_type: str,
    date_from: date,
    date_to: date,
    engine_identity: dict[str, Any],
    validator_params: dict[str, Any],
    report_json: dict[str, Any],
) -> ValidationRun:
    """Insert an already-computed SUCCEEDED run (``job_id = NULL``) on ``db``.

    Used by the synchronous path: the POST computes the report inline, then
    records it.  The row commits with the request's transaction.
    """
    from snore.database import models  # noqa: PLC0415

    now = datetime.now(UTC)
    run = models.ValidationRun(
        job_id=None,
        profile_id=profile_id,
        owner_user_id=owner_user_id,
        validator_type=validator_type,
        date_from=date_from,
        date_to=date_to,
        engine_identity_json=engine_identity,
        validator_params_json=validator_params,
        report_json=report_json,
        state=ValidationRunState.SUCCEEDED.value,
        created_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
    )
    db.add(run)
    await db.flush()
    return run


async def delete_run(run_id: int) -> None:
    """Delete a run row by id (used to clean up a queued row that failed to enqueue)."""
    from sqlalchemy import delete  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    async with session_scope(immediate=True) as db:
        await db.execute(
            delete(models.ValidationRun).where(models.ValidationRun.id == run_id)
        )


async def _persist_run(job: ValidationRunJob) -> None:
    """Update the run row in place with the job's current state.

    An UPDATE keyed on the row id — never an upsert.  ``insert_queued_run``
    always pre-creates the row, so the worker has nothing to INSERT; and a run
    the user concurrently DELETEd must stay gone, not be resurrected by a
    terminal write, so a zero-row UPDATE is exactly the desired no-op.
    """
    from sqlalchemy import update  # noqa: PLC0415

    from snore.database.models import ValidationRun as _Run  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "state": job.state.value,
        "error_message": job.error_message,
        "started_at": job.started_at_wall,
        "updated_at": now,
    }
    if job.is_terminal:
        # report_json and finished_at are written only once the run terminates;
        # before that the row keeps the NULLs insert_queued_run set.
        values["report_json"] = job.report_json
        values["finished_at"] = job.finished_at_wall
    async with session_scope(immediate=True) as db:
        await db.execute(update(_Run).where(_Run.id == job.run_id).values(**values))


async def _finalize_run(job: ValidationRunJob) -> None:
    """Persist the terminal state, then prune this run's retention group.

    Pruning after every terminal persist (scoped to the run's own
    profile+validator group) keeps the keep-``_RETENTION_PER_GROUP`` cap honoured
    continuously in a long-lived process, not only at the startup sweep.
    """
    await _persist_run(job)
    await prune_retention(profile_id=job.profile_id, validator_type=job.validator_type)


async def _run_validation(job: ValidationRunJob) -> dict[str, Any]:
    """Construct and invoke the registered validator; return its report as JSON.

    Passes ``None`` for the session: a JOB validator opens its own short-lived
    per-session scopes so no single read transaction spans the multi-minute run
    (a batch-long read snapshot would pin the WAL and starve checkpoints).
    """
    from snore.api.validation_registry import get_spec  # noqa: PLC0415

    spec = get_spec(job.validator_type)
    if spec is None:  # Defensive: enqueue only ever happens for registered types.
        raise ValueError(f"No validator registered for type {job.validator_type!r}")

    report = await spec.run(
        None,
        job.profile_id,
        job.date_from.isoformat(),
        job.date_to.isoformat(),
        job.validator_params,
    )
    return report.model_dump(mode="json")


def _execute_job(job: ValidationRunJob) -> None:
    if not job.try_start():
        # Cancelled before start — persist the terminal (cancelled) state.
        try:
            asyncio.run(_finalize_run(job))
        except Exception:
            logger.exception(
                "Failed to persist cancelled validation run %s", job.job_id
            )
        return
    try:
        asyncio.run(_persist_run(job))
    except Exception:
        logger.exception(
            "Failed to persist RUNNING state for validation run %s", job.job_id
        )
    try:
        report_json = asyncio.run(_run_validation(job))
        job.set_report(report_json)
        job.finish(succeeded=True)
    except Exception as exc:
        logger.exception("Validation run %s failed", job.job_id)
        job.finish(succeeded=False, error_message=str(exc))
    finally:
        try:
            asyncio.run(_finalize_run(job))
        except Exception:
            logger.exception(
                "Failed to persist terminal state for validation run %s", job.job_id
            )


# The worker-pool layer (throttled reaping, per-worker loop wiring, restartable
# start/shutdown thread bookkeeping) is shared with analysis_jobs.  ``execute``
# and ``concurrency`` are wrapped so a test patching this module's ``_execute_job``
# / ``_get_job_concurrency`` is honoured at call time.
_pool: WorkerPool[ValidationRunJob] = WorkerPool(
    queue=_queue,
    condition=_condition,
    store=_store,
    execute=lambda job: _execute_job(job),
    concurrency=lambda: _get_job_concurrency(),
    thread_name_prefix="validation-job-worker",
    reap=_reap_terminal,
)


def start_worker() -> tuple[list[threading.Thread], threading.Event]:
    """Start N persistent worker threads; return (threads, stop_event)."""
    return _pool.start()


def shutdown(timeout: float = 10.0) -> list[threading.Thread]:
    """Signal all workers to stop; cancel all queued/running jobs.

    Returns any worker threads still alive after ``timeout``.
    """
    return _pool.shutdown(timeout)


# ---------------------------------------------------------------------------
# Startup sweep: orphan recovery + retention prune
# ---------------------------------------------------------------------------


async def recover_orphaned_runs() -> int:
    """Mark non-terminal ``validation_runs`` rows failed; return the count.

    A non-terminal row on startup is a run interrupted by a crash/restart.
    Validation is idempotent, so there is no auto-resume — the user re-runs.
    """
    from sqlalchemy import update  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    now = datetime.now(UTC)
    non_terminal = [
        ValidationRunState.QUEUED.value,
        ValidationRunState.RUNNING.value,
    ]
    count = 0
    try:
        async with session_scope(immediate=True) as db:
            result = await db.execute(
                update(models.ValidationRun)
                .where(models.ValidationRun.state.in_(non_terminal))
                .values(
                    state=ValidationRunState.FAILED.value,
                    error_message="Server restarted while validation run was in progress",
                    finished_at=now,
                    updated_at=now,
                )
            )
            count = result.rowcount or 0  # type: ignore[attr-defined]
        if count:
            logger.info(
                "Startup recovery: marked %d orphaned validation run(s) as failed",
                count,
            )
    except Exception as exc:
        logger.warning("Orphaned validation run recovery failed: %s", exc)
    return count


async def prune_retention(
    keep: int = _RETENTION_PER_GROUP,
    *,
    profile_id: int | None = None,
    validator_type: str | None = None,
) -> int:
    """Delete all but the newest ``keep`` rows per (profile_id, validator_type).

    Returns the number of rows deleted.  Uses a ROW_NUMBER window partitioned by
    the retention group, ordered newest-first, deleting anything ranked beyond
    ``keep``.  With ``profile_id`` and ``validator_type`` given, the sweep is
    restricted to that one group — the cheap per-run prune the worker runs after
    each terminal persist.  With neither, it sweeps every group (the startup
    sweep).
    """
    from sqlalchemy import delete, func, select  # noqa: PLC0415

    from snore.database import models  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    run = models.ValidationRun
    ranked_src = select(
        run.id,
        func.row_number()
        .over(
            partition_by=(run.profile_id, run.validator_type),
            order_by=(run.created_at.desc(), run.id.desc()),
        )
        .label("rn"),
    )
    if profile_id is not None and validator_type is not None:
        ranked_src = ranked_src.where(
            run.profile_id == profile_id, run.validator_type == validator_type
        )
    ranked = ranked_src.subquery()
    stale_ids = select(ranked.c.id).where(ranked.c.rn > keep)

    count = 0
    try:
        async with session_scope(immediate=True) as db:
            result = await db.execute(delete(run).where(run.id.in_(stale_ids)))
            count = result.rowcount or 0  # type: ignore[attr-defined]
        if count:
            logger.info("Retention: pruned %d old validation run(s)", count)
    except Exception as exc:
        logger.warning("Validation run retention prune failed: %s", exc)
    return count
