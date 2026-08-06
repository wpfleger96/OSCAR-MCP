"""Unit tests for the analysis job queue state machine.

Tests cover the module-level store functions and the AnalysisJob state machine
without starting real worker threads or touching a database.
"""

from __future__ import annotations

import time

import pytest

import snore.api.analysis_jobs as jobs

from snore.api.analysis_jobs import (
    JOB_TTL_SECONDS,
    MAX_QUEUED,
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobState,
    _reap_terminal,
    cancel_job,
    enqueue,
    get_job,
    list_jobs,
    shutdown,
)

# ---------------------------------------------------------------------------
# Fixture: clean module state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_analysis_jobs():
    """Reset global analysis job state before and after each test."""
    jobs._all_jobs.clear()
    jobs._queue.clear()
    jobs._worker_thread = None
    jobs._stop_event = None
    yield
    jobs._all_jobs.clear()
    jobs._queue.clear()
    jobs._worker_thread = None
    jobs._stop_event = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enqueue_one(session_ids: list[int] | None = None) -> AnalysisJob:
    job = enqueue(
        profile_id=1,
        session_ids=session_ids or [1, 2, 3],
        source=AnalysisJobSource.BATCH,
    )
    assert job is not None
    return job


# ---------------------------------------------------------------------------
# 1. Enqueue happy path
# ---------------------------------------------------------------------------


def test_enqueue_happy_path_appears_in_store():
    job = enqueue(
        profile_id=1,
        session_ids=[10, 20],
        source=AnalysisJobSource.BATCH,
        owner_user_id=42,
    )
    assert job is not None
    assert job.state == AnalysisJobState.QUEUED
    assert get_job(job.job_id) is job
    assert any(j is job for j in list_jobs())


def test_enqueue_owner_scoping():
    job = enqueue(
        profile_id=1,
        session_ids=[1],
        source=AnalysisJobSource.IMPORT,
        owner_user_id=7,
    )
    assert job is not None
    assert list_jobs(owner_user_id=7) == [job]
    assert list_jobs(owner_user_id=99) == []


def test_enqueue_stores_modes_and_primary_mode():
    job = enqueue(
        profile_id=1,
        session_ids=[1],
        source=AnalysisJobSource.BATCH,
        modes=["aasm", "resmed"],
        primary_mode="resmed",
        store_results=False,
    )
    assert job is not None
    assert job.modes == ["aasm", "resmed"]
    assert job.primary_mode == "resmed"
    assert job.store_results is False


# ---------------------------------------------------------------------------
# 2. Enqueue returns None at MAX_QUEUED
# ---------------------------------------------------------------------------


def test_enqueue_returns_none_when_queue_full():
    for _ in range(MAX_QUEUED):
        j = enqueue(profile_id=1, session_ids=[1], source=AnalysisJobSource.BATCH)
        assert j is not None
    assert (
        enqueue(profile_id=1, session_ids=[1], source=AnalysisJobSource.BATCH) is None
    )


# ---------------------------------------------------------------------------
# 3. Cancel of QUEUED job → CANCELLED, removed from queue
# ---------------------------------------------------------------------------


def test_cancel_queued_job_transitions_and_removes():
    job = _enqueue_one()
    # Read via locals: asserting on job.state directly makes mypy narrow the
    # property to a literal and flag the post-mutation comparison below.
    state_before = job.state
    assert state_before == AnalysisJobState.QUEUED
    assert len(jobs._queue) == 1

    result = cancel_job(job.job_id)

    assert result is True
    state_after = job.state
    assert state_after == AnalysisJobState.CANCELLED
    assert len(jobs._queue) == 0


def test_cancel_unknown_job_returns_false():
    assert cancel_job("nonexistent") is False


def test_cancel_already_terminal_job_returns_false():
    job = _enqueue_one()
    cancel_job(job.job_id)
    # Already CANCELLED; second call should return False.
    assert cancel_job(job.job_id) is False


# ---------------------------------------------------------------------------
# 4. try_start after cancel_flag set returns False → TOCTOU guard
# ---------------------------------------------------------------------------


def test_try_start_after_cancel_flag_returns_false_and_is_cancelled():
    job = _enqueue_one()
    # Simulate cancel flag set before the worker dequeues the job.
    with job._lock:
        job._cancel_flag = True
        job._state = AnalysisJobState.QUEUED  # reset to QUEUED
        job._finished_at = None

    result = job.try_start()

    assert result is False
    assert job.state == AnalysisJobState.CANCELLED
    assert job.finished_at is not None


def test_try_start_without_cancel_transitions_to_running():
    job = _enqueue_one()
    result = job.try_start()
    assert result is True
    assert job.state == AnalysisJobState.RUNNING
    assert job.started_at is not None


# ---------------------------------------------------------------------------
# 5. finish(succeeded=True) after cancel_flag set → CANCELLED not SUCCEEDED
# ---------------------------------------------------------------------------


def test_finish_with_cancel_flag_yields_cancelled():
    job = _enqueue_one()
    job.try_start()
    # Cancel arrives while the job is RUNNING.
    with job._lock:
        job._cancel_flag = True

    job.finish(succeeded=True)

    assert job.state == AnalysisJobState.CANCELLED
    assert job.finished_at is not None


def test_finish_succeeded_without_cancel():
    job = _enqueue_one()
    job.try_start()
    job.finish(succeeded=True)
    assert job.state == AnalysisJobState.SUCCEEDED


def test_finish_failed_sets_error_message():
    job = _enqueue_one()
    job.try_start()
    job.finish(succeeded=False, error_message="boom")
    assert job.state == AnalysisJobState.FAILED
    assert job.error_message == "boom"


def test_finish_is_idempotent_after_terminal():
    job = _enqueue_one()
    job.try_start()
    job.finish(succeeded=True)
    job.finish(succeeded=False, error_message="should be ignored")
    # First finish wins.
    assert job.state == AnalysisJobState.SUCCEEDED
    assert job.error_message is None


# ---------------------------------------------------------------------------
# 6. _reap_terminal removes expired jobs; retains fresh and non-terminal ones
# ---------------------------------------------------------------------------


def test_reap_terminal_removes_expired_and_retains_others():
    # Expired terminal job
    job_expired = _enqueue_one()
    cancel_job(job_expired.job_id)
    with job_expired._lock:
        job_expired._finished_at = time.monotonic() - JOB_TTL_SECONDS - 1

    # Non-terminal job
    job_running = enqueue(
        profile_id=1, session_ids=[5], source=AnalysisJobSource.IMPORT
    )
    assert job_running is not None
    with job_running._lock:
        job_running._state = AnalysisJobState.RUNNING

    # Recent terminal job (within TTL)
    job_recent = enqueue(profile_id=1, session_ids=[6], source=AnalysisJobSource.BATCH)
    assert job_recent is not None
    cancel_job(job_recent.job_id)  # finished_at = now

    _reap_terminal()

    assert get_job(job_expired.job_id) is None
    assert get_job(job_running.job_id) is job_running
    assert get_job(job_recent.job_id) is job_recent


# ---------------------------------------------------------------------------
# 7. shutdown marks QUEUED jobs CANCELLED
# ---------------------------------------------------------------------------


def test_shutdown_cancels_queued_jobs():
    job1 = enqueue(profile_id=1, session_ids=[1], source=AnalysisJobSource.BATCH)
    job2 = enqueue(profile_id=1, session_ids=[2], source=AnalysisJobSource.BATCH)
    assert job1 is not None
    assert job2 is not None

    shutdown(timeout=0.1)

    assert job1.state == AnalysisJobState.CANCELLED
    assert job2.state == AnalysisJobState.CANCELLED
    assert len(jobs._queue) == 0


def test_shutdown_sets_cancel_flag_on_running_job():
    job = _enqueue_one()
    with job._lock:
        job._state = AnalysisJobState.RUNNING  # Simulate worker picked it up.
        jobs._queue.clear()

    shutdown(timeout=0.1)

    assert job.cancel_requested is True


# ---------------------------------------------------------------------------
# 8. to_dict shape matches AnalysisJobStatus (no profile_id)
# ---------------------------------------------------------------------------


def test_to_dict_fields_and_no_profile_id():
    job = enqueue(
        profile_id=99,
        session_ids=[1, 2],
        source=AnalysisJobSource.IMPORT,
        owner_user_id=5,
    )
    assert job is not None
    d = job.to_dict()

    assert "profile_id" not in d
    assert d["job_id"] == job.job_id
    assert d["session_count"] == 2
    assert d["source"] == "import"
    assert d["owner_user_id"] == 5
    assert d["state"] == "queued"
    assert "created_at" in d
    assert d["started_at"] is None
    assert d["finished_at"] is None
