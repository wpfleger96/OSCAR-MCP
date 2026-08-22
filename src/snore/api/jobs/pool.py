"""Shared worker-thread pool for the analysis and validation job pipelines.

Both pipelines drive a fixed set of daemon workers over one shared FIFO queue,
reaping terminal jobs on a throttled cadence and cancelling everything on
shutdown.  That machinery — the :class:`ThrottledReaper`, the per-worker
:func:`~snore.api.jobs.core.run_worker_loop` wiring, and the start/shutdown
thread bookkeeping — was byte-identical between the two modules save for the
thread-name prefix and the concrete job type.  This single-sources it; each
module supplies its own queue, condition, store, execute callback, concurrency
source, and reaper hook.

Import's single-deposable-worker topology is intentionally NOT built on this
pool — it needs :func:`run_worker_loop`'s ``is_deposed`` replacement check — and
stays bespoke.
"""

from __future__ import annotations

import logging
import threading
import time

from collections import deque
from collections.abc import Callable
from typing import Any

from snore.api.jobs.core import JobRecordBase, JobStore, run_worker_loop

logger = logging.getLogger(__name__)


class ThrottledReaper:
    """Reap terminal jobs unconditionally after each job; throttle on idle.

    Idle cycles reap at most once per ``interval`` seconds; a completed job
    always reaps and resets the clock.  One instance is created per worker
    thread so its ``_last_reap`` needs no cross-thread synchronisation.
    """

    def __init__(self, reap: Callable[[], None], interval: float = 60.0) -> None:
        self._reap = reap
        self._interval = interval
        self._last_reap = time.monotonic()

    def on_idle(self) -> None:
        now = time.monotonic()
        if now - self._last_reap >= self._interval:
            self._reap()
            self._last_reap = now

    def after_job(self) -> None:
        self._reap()
        self._last_reap = time.monotonic()


class WorkerPool[J: JobRecordBase[Any]]:
    """A restartable set of daemon workers draining one FIFO job queue.

    ``execute`` and ``concurrency`` are called late (never captured values) so a
    test that patches the owning module's ``_execute_job`` / job-concurrency
    hook is honoured.  ``start`` may be called more than once per process (app
    restarts in tests); every stop event is retained so ``shutdown`` stops every
    generation, not just the latest — an abandoned generation with an unset stop
    event would otherwise keep stealing jobs from the shared queue.
    """

    def __init__(
        self,
        *,
        queue: deque[J],
        condition: threading.Condition,
        store: JobStore[J],
        execute: Callable[[J], None],
        concurrency: Callable[[], int],
        thread_name_prefix: str,
        reap: Callable[[], None],
        reap_interval: float = 60.0,
    ) -> None:
        self._queue = queue
        self._condition = condition
        self._store = store
        self._execute = execute
        self._concurrency = concurrency
        self._thread_name_prefix = thread_name_prefix
        self._reap = reap
        self._reap_interval = reap_interval
        self._worker_threads: list[threading.Thread] = []
        self._stop_event: threading.Event | None = None
        self._stop_events: list[threading.Event] = []

    def _worker_loop(self, stop_event: threading.Event) -> None:
        reaper = ThrottledReaper(self._reap, self._reap_interval)

        def _on_error(job: J, exc: BaseException) -> None:
            if not job.is_terminal:
                job.finish(succeeded=False, error_message=str(exc))

        run_worker_loop(
            stop_event,
            self._condition,
            self._queue,
            self._execute,
            on_idle=reaper.on_idle,
            after_execute=reaper.after_job,
            on_execute_error=_on_error,
        )

    def start(self) -> tuple[list[threading.Thread], threading.Event]:
        """Start ``concurrency()`` daemon workers; return (threads, stop_event)."""
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._stop_events.append(stop_event)
        threads: list[threading.Thread] = []
        for i in range(self._concurrency()):
            t = threading.Thread(
                target=self._worker_loop,
                args=(stop_event,),
                daemon=True,
                name=f"{self._thread_name_prefix}-{i}",
            )
            threads.append(t)
            t.start()
        self._worker_threads.extend(threads)
        return threads, stop_event

    def shutdown(self, timeout: float = 10.0) -> list[threading.Thread]:
        """Stop every worker generation and cancel all queued/running jobs.

        Returns any worker threads still alive after ``timeout`` so a later call
        can retry the join.
        """
        for ev in self._stop_events:
            ev.set()
        if self._stop_event is not None:
            self._stop_event.set()
        # ``condition`` wraps ``store.lock`` in both pipelines, so mutate the
        # store dict in place under it rather than calling a store method that
        # would re-acquire the same (non-reentrant) lock.
        with self._condition:
            for job in list(self._store.jobs.values()):
                job.try_cancel()
            self._queue.clear()
            self._condition.notify_all()
        deadline = time.monotonic() + timeout
        for t in self._worker_threads:
            remaining = max(0.0, deadline - time.monotonic())
            t.join(timeout=remaining)
        self._worker_threads[:] = [t for t in self._worker_threads if t.is_alive()]
        if not self._worker_threads:
            self._stop_events.clear()
            self._stop_event = None
        return list(self._worker_threads)
