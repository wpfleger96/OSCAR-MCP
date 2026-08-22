"""Unit tests for the shared jobs.core machinery.

Exercises JobStore visibility/reap, JobRecordBase cancel/timestamp behaviour,
and the run_worker_loop skeleton (deposition, BaseException survival, N-worker
drain) against minimal fakes rather than the real import/analysis jobs.
"""

from __future__ import annotations

import threading
import time

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from snore.api.jobs.core import JobRecordBase, JobStore, run_worker_loop


class _State(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


_TERMINAL = frozenset({_State.DONE, _State.CANCELLED})


@dataclass(kw_only=True)
class _FakeJob(JobRecordBase[_State]):
    _TERMINAL_STATES = _TERMINAL

    _state: _State = field(default=_State.QUEUED, init=False, repr=False)
    _terminal_at: float | None = field(default=None, init=False, repr=False)
    cancel_notified: bool = field(default=False, init=False, repr=False)

    def start(self) -> bool:
        with self._lock:
            if self._state is not _State.QUEUED:
                return False
            self._start_running(_State.RUNNING)
            return True

    def finish(self) -> None:
        with self._lock:
            if self._state in _TERMINAL:
                return
            self._state = _State.CANCELLED if self._cancel_flag else _State.DONE
            self._terminal_at = time.monotonic()

    def _on_cancel_locked(self) -> bool | None:
        if self._state is _State.QUEUED:
            self._state = _State.CANCELLED
            self._terminal_at = time.monotonic()
            return True
        return None

    def _notify_after_cancel(self, payload: bool) -> None:
        self.cancel_notified = payload


def _job(job_id: str, owner: int | None = None) -> _FakeJob:
    return _FakeJob(job_id=job_id, owner_user_id=owner)


# ---------------------------------------------------------------------------
# JobStore.list_visible_to
# ---------------------------------------------------------------------------


def test_list_visible_to_none_owner_sees_all():
    store: JobStore[_FakeJob] = JobStore()
    store.add(_job("a", owner=1))
    store.add(_job("b", owner=2))
    store.add(_job("c", owner=None))
    assert {j.job_id for j in store.list_visible_to(None)} == {"a", "b", "c"}


def test_list_visible_to_owner_sees_own_and_unowned():
    store: JobStore[_FakeJob] = JobStore()
    store.add(_job("a", owner=1))
    store.add(_job("b", owner=2))
    store.add(_job("c", owner=None))  # unowned is visible to everyone
    visible = {j.job_id for j in store.list_visible_to(1)}
    assert visible == {"a", "c"}


# ---------------------------------------------------------------------------
# JobStore.reap
# ---------------------------------------------------------------------------


def test_reap_skips_active_and_recent_terminal_jobs():
    store: JobStore[_FakeJob] = JobStore()
    active = _job("active")
    fresh = _job("fresh")
    fresh.start()
    fresh.finish()  # terminal, _terminal_at = now
    store.add(active)
    store.add(fresh)

    removed = store.reap(600.0, terminal_at=lambda j: j._terminal_at)

    assert removed == []
    assert store.get("active") is active
    assert store.get("fresh") is fresh


def test_reap_removes_expired_terminal_jobs():
    store: JobStore[_FakeJob] = JobStore()
    old = _job("old")
    old.start()
    old.finish()
    old._terminal_at = time.monotonic() - 601.0  # older than TTL
    store.add(old)

    removed = store.reap(600.0, terminal_at=lambda j: j._terminal_at)

    assert removed == ["old"]
    assert store.get("old") is None


def test_reap_honours_reapable_hook():
    store: JobStore[_FakeJob] = JobStore()
    held = _job("held")
    held.start()
    held.finish()
    held._terminal_at = time.monotonic() - 601.0
    store.add(held)

    # reapable=False mirrors import's capacity-held guard: never reaped despite age.
    removed = store.reap(
        600.0,
        terminal_at=lambda j: j._terminal_at,
        reapable=lambda j: False,
    )

    assert removed == []
    assert store.get("held") is held


# ---------------------------------------------------------------------------
# JobRecordBase cancel / timestamps
# ---------------------------------------------------------------------------


def test_try_cancel_queued_terminalizes_and_notifies():
    job = _job("q")
    assert job.try_cancel() is True
    assert job.state is _State.CANCELLED
    assert job.cancel_notified is True


def test_try_cancel_running_is_cooperative():
    job = _job("r")
    job.start()
    assert job.try_cancel() is True
    assert job.state is _State.RUNNING  # no eager terminal
    assert job.cancel_requested is True
    assert job.cancel_notified is False  # hook returned None → no notify


def test_try_cancel_terminal_returns_false():
    job = _job("t")
    job.start()
    job.finish()
    assert job.try_cancel() is False


def test_start_running_stamps_started_timestamps():
    job = _job("s")
    assert job.started_at is None and job.started_at_wall is None
    job.start()
    assert job.started_at is not None
    assert job.started_at_wall is not None


# ---------------------------------------------------------------------------
# run_worker_loop
# ---------------------------------------------------------------------------


def test_worker_loop_deposition_exits_within_one_cycle():
    stop = threading.Event()
    cond = threading.Condition()
    q: deque[_FakeJob] = deque()
    deposed = threading.Event()

    def execute(item: _FakeJob) -> None:  # pragma: no cover - never dequeues
        raise AssertionError("deposed worker must not execute")

    t = threading.Thread(
        target=run_worker_loop,
        args=(stop, cond, q, execute),
        kwargs={"is_deposed": deposed.is_set},
        daemon=True,
    )
    t.start()
    deposed.set()
    with cond:
        cond.notify_all()
    t.join(timeout=3.0)
    assert not t.is_alive()


def test_worker_loop_survives_execute_baseexception():
    stop = threading.Event()
    cond = threading.Condition()
    q: deque[_FakeJob] = deque()
    bad = _job("bad")
    bad.start()
    good = _job("good")
    good.start()
    done = threading.Event()

    def execute(item: _FakeJob) -> None:
        if item.job_id == "bad":
            raise BaseException("boom")  # noqa: TRY002 - deliberate hostile case
        item.finish()
        done.set()

    def on_error(item: _FakeJob, exc: BaseException) -> None:
        item.finish()

    t = threading.Thread(
        target=run_worker_loop,
        args=(stop, cond, q, execute),
        kwargs={"on_execute_error": on_error},
        daemon=True,
    )
    t.start()
    with cond:
        q.append(bad)
        q.append(good)
        cond.notify_all()

    assert done.wait(timeout=3.0), "worker thread died on BaseException"
    assert bad.is_terminal, "on_execute_error must force the failed job terminal"
    assert good.state is _State.DONE
    stop.set()
    with cond:
        cond.notify_all()
    t.join(timeout=3.0)


def test_n_workers_share_queue_and_stop():
    stop = threading.Event()
    cond = threading.Condition()
    q: deque[_FakeJob] = deque()
    processed: list[str] = []
    processed_lock = threading.Lock()

    def execute(item: _FakeJob) -> None:
        item.finish()
        with processed_lock:
            processed.append(item.job_id)

    threads = [
        threading.Thread(
            target=run_worker_loop,
            args=(stop, cond, q, execute),
            daemon=True,
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()

    jobs = [_job(f"j{i}") for i in range(20)]
    with cond:
        q.extend(jobs)
        cond.notify_all()

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with processed_lock:
            if len(processed) == 20:
                break
        time.sleep(0.02)

    stop.set()
    with cond:
        cond.notify_all()
    for t in threads:
        t.join(timeout=3.0)

    assert sorted(processed) == sorted(j.job_id for j in jobs)
    assert not any(t.is_alive() for t in threads)
