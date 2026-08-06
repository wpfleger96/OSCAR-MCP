"""Pinned tests for the two-phase import-then-analysis job contract (PR-A, §A1).

Scenarios:
1. import_committed is set BEFORE any analysis phase begins.
2. phase_complete delivers a non-terminal 'phase_complete' event; the observer
   remains attached and can still receive subsequent events.
3. A stalled observer (capacity-one slot was already filled with a progress
   message) still receives import_committed in the terminal payload.
4. A late observer (attaches after the job finishes) receives import_committed
   and import_result in the stored terminal payload.
5. Analysis failure after a committed import delivers a terminal 'error' event
   that carries import_committed=True and the import_result.
6. Cancellation after import commits delivers a terminal 'error' that carries
   import_committed=True and import_result.
7. --no-analyze path (CLI): a single 'complete' terminal fires with no
   analysis-phase events.
"""

from __future__ import annotations

import pytest

import snore.api.import_jobs as job_store

from snore.api.import_jobs import (
    ImportJob,
    JobPhase,
    JobState,
    JobType,
    ObserverChannel,
    create_job,
)

# ---------------------------------------------------------------------------
# Fixture: clean job store between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_job_store():
    """Reset the job store before and after each test."""
    job_store._jobs.clear()
    job_store._per_user_count.clear()
    job_store._global_count = 0
    job_store._import_queue.clear()
    yield
    job_store._jobs.clear()
    job_store._per_user_count.clear()
    job_store._global_count = 0
    job_store._import_queue.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job() -> ImportJob:
    return create_job(JobType.PATH, sources=[])


def _drain(ch: ObserverChannel, *, timeout: float = 0.1) -> list[dict]:
    """Collect all immediately available messages from *ch*."""
    msgs = []
    while True:
        m = ch.get(timeout=timeout)
        if m is None:
            break
        msgs.append(m)
        if m.get("event") in ("complete", "error"):
            break
    return msgs


def _fake_import_result() -> dict:
    return {
        "total_imported": 2,
        "total_skipped": 0,
        "total_failed": 0,
        "sources": [],
        "warnings": [],
        "imported_session_ids": [10, 11],
    }


# ---------------------------------------------------------------------------
# Test 1: import_committed set before analysis begins
# ---------------------------------------------------------------------------


def test_import_committed_true_before_analysis_phase():
    """phase_complete() sets import_committed=True synchronously before any
    analysis event can be observed."""
    job = _make_job()
    job.try_start()

    assert not job.import_committed

    job.phase_complete(JobPhase.IMPORT, _fake_import_result())

    # After phase_complete, the flag is True — still RUNNING, not terminal.
    assert job.import_committed
    assert job.state == JobState.RUNNING


# ---------------------------------------------------------------------------
# Test 2: phase_complete is non-terminal — observer survives it
# ---------------------------------------------------------------------------


def test_phase_complete_is_non_terminal_observer_survives():
    """The SSE observer receives the 'phase_complete' event but is NOT detached;
    it can still receive the subsequent terminal event."""
    job = _make_job()
    job.try_start()

    ch = job.attach_observer()

    job.phase_complete(JobPhase.IMPORT, _fake_import_result())

    # First message: phase_complete (non-terminal)
    msg = ch.get(timeout=0.1)
    assert msg is not None
    assert msg["event"] == "phase_complete"
    assert msg["data"]["phase"] == "import"
    assert job.state == JobState.RUNNING  # still running

    # Send the real terminal
    job._finish(
        succeeded=True,
        terminal_msg={"event": "complete", "data": {"result": {}}},
    )

    # Observer receives the terminal
    msg2 = ch.get(timeout=0.1)
    assert msg2 is not None
    assert msg2["event"] == "complete"


# ---------------------------------------------------------------------------
# Test 3: stalled observer sees import_committed in terminal
# ---------------------------------------------------------------------------


def test_stalled_observer_receives_import_committed_in_terminal():
    """Even if the observer's coalescing slot was filled with a progress message
    (simulating a stalled consumer), the terminal payload carries
    import_committed=True and import_result."""
    job = _make_job()
    job.try_start()

    ch = job.attach_observer()

    # Stall: fill the slot with a progress message so it won't see phase_complete.
    ch.put({"event": "progress", "data": {"message": "busy"}})

    # Phase 1 completes.
    import_result = _fake_import_result()
    job.phase_complete(JobPhase.IMPORT, import_result)

    # Consume the stale progress message (or the coalesced phase_complete).
    _ = ch.get(timeout=0.1)

    # Build and deliver a terminal that includes import_committed.
    terminal_msg = {
        "event": "complete",
        "data": {
            "result": import_result,
            "import_committed": True,
            "import_result": import_result,
        },
    }
    job._finish(succeeded=True, terminal_msg=terminal_msg)

    msg = ch.get(timeout=0.1)
    assert msg is not None
    assert msg["event"] == "complete"
    assert msg["data"].get("import_committed") is True
    assert msg["data"].get("import_result") is not None


# ---------------------------------------------------------------------------
# Test 4: late observer sees import_committed + import_result
# ---------------------------------------------------------------------------


def test_late_observer_sees_import_committed_and_import_result():
    """An observer that attaches after the job is terminal receives the stored
    terminal payload, which includes import_committed=True when the import
    phase had committed data."""
    job = _make_job()
    job.try_start()

    import_result = _fake_import_result()
    job.phase_complete(JobPhase.IMPORT, import_result)

    terminal_msg = {
        "event": "complete",
        "data": {
            "result": import_result,
            "import_committed": True,
            "import_result": import_result,
        },
    }
    job._finish(succeeded=True, terminal_msg=terminal_msg)

    # Late attach — job is already terminal.
    ch = job.attach_observer()
    msg = ch.get(timeout=0.1)
    assert msg is not None
    assert msg["event"] == "complete"
    assert msg["data"].get("import_committed") is True
    assert msg["data"].get("import_result") is not None


# ---------------------------------------------------------------------------
# Test 5: successful import enqueues analysis — terminal carries analysis_job_id
# ---------------------------------------------------------------------------


def test_successful_import_terminal_carries_analysis_job_id():
    """After import commits and analysis is enqueued, the terminal 'complete'
    event includes analysis_job_id so the client can track the background job."""
    job = _make_job()
    job.try_start()

    import_result = _fake_import_result()
    job.phase_complete(JobPhase.IMPORT, import_result)

    # Simulate: analysis enqueue succeeded — worker builds terminal with job id.
    terminal_msg = {
        "event": "complete",
        "data": {
            "result": import_result,
            "import_committed": True,
            "import_result": import_result,
            "analysis_job_id": "abc123deadbeef",
        },
    }
    job._finish(succeeded=True, terminal_msg=terminal_msg)

    assert job.state == JobState.SUCCEEDED

    # Late observer receives the full payload.
    ch = job.attach_observer()
    msg = ch.get(timeout=0.1)
    assert msg is not None
    assert msg["event"] == "complete"
    assert msg["data"].get("import_committed") is True
    assert msg["data"].get("import_result") is not None
    assert msg["data"].get("analysis_job_id") == "abc123deadbeef"
    assert "analysis_queued" not in msg["data"]


# ---------------------------------------------------------------------------
# Test 6: analysis queue full — terminal carries analysis_queued: False
# ---------------------------------------------------------------------------


def test_queue_full_terminal_carries_analysis_queued_false():
    """When the analysis queue is full at import time, the terminal 'complete'
    event carries analysis_queued=False so the client can distinguish queue-full
    from 'nothing was imported'."""
    job = _make_job()
    job.try_start()

    import_result = _fake_import_result()
    job.phase_complete(JobPhase.IMPORT, import_result)

    # Simulate: queue was full — worker sets analysis_queued: False.
    terminal_msg = {
        "event": "complete",
        "data": {
            "result": import_result,
            "import_committed": True,
            "import_result": import_result,
            "analysis_queued": False,
        },
    }
    job._finish(succeeded=True, terminal_msg=terminal_msg)

    assert job.state == JobState.SUCCEEDED

    ch = job.attach_observer()
    msg = ch.get(timeout=0.1)
    assert msg is not None
    assert msg["event"] == "complete"
    assert msg["data"].get("import_committed") is True
    assert msg["data"].get("analysis_queued") is False
    assert "analysis_job_id" not in msg["data"]


# ---------------------------------------------------------------------------
# Test 7: --no-analyze path: single terminal, no phase_complete event
# ---------------------------------------------------------------------------


def test_no_analyze_produces_single_terminal_no_phase_complete():
    """When the --no-analyze flag is set (or no sessions were imported), the
    job fires exactly one terminal 'complete' event without any preceding
    phase_complete event."""
    job = _make_job()
    job.try_start()

    ch = job.attach_observer()

    # No phase_complete is emitted (--no-analyze path).
    terminal_msg = {"event": "complete", "data": {"result": {}}}
    job._finish(succeeded=True, terminal_msg=terminal_msg)

    msgs = _drain(ch)
    assert len(msgs) == 1
    assert msgs[0]["event"] == "complete"
    # import_committed must NOT appear when no import happened.
    assert "import_committed" not in msgs[0]["data"]
