"""Unit tests for the validation-run job queue, registry, and persistence.

Grouped as:

- In-memory state machine (mirrors ``test_analysis_jobs``): no DB.
- Registry seam: every validator type (``events``/``fl``/``breaths``/``rera``/
  ``apple``) is wired, with the correct JOB vs. SYNC mode and params.
- ``validation_runs`` persistence against a real migrated SQLite DB: the
  enqueue → run → persist lifecycle, dedup, orphan recovery, and retention —
  exercised through the exact production ``session_scope()`` code paths.
- ``rera`` job lifecycle plus a dedup test proving a changed proxy tunable in
  ``validator_params_json`` forces a fresh run.
"""

from __future__ import annotations

import asyncio
import time

from collections.abc import Awaitable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from pydantic import BaseModel

import snore.api.validation_jobs as vjobs
import snore.api.validation_registry as vreg

from snore.api.validation_jobs import (
    MAX_QUEUED,
    ValidationRunJob,
    ValidationRunState,
    cancel_job,
    enqueue,
    get_job,
    list_jobs,
)
from snore.api.validation_registry import RunMode, ValidatorSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_validation_jobs():
    """Reset the module-global in-memory store around each test."""
    vjobs.shutdown(timeout=5.0)
    vjobs._all_jobs.clear()
    vjobs._queue.clear()
    yield
    vjobs.shutdown(timeout=5.0)
    vjobs._all_jobs.clear()
    vjobs._queue.clear()


@pytest.fixture
def snapshot_registry():
    """Snapshot and restore the validator registry (tests may register stubs)."""
    saved = dict(vreg._REGISTRY)
    yield
    vreg._REGISTRY.clear()
    vreg._REGISTRY.update(saved)


class _StubReport(BaseModel):
    ok: bool = True
    n: int = 0


def _enqueue_one(**overrides: Any) -> ValidationRunJob:
    kwargs = dict(
        run_id=1,
        profile_id=1,
        validator_type="events",
        date_from=date(2024, 1, 1),
        date_to=date(2024, 1, 7),
        engine_identity={"segmenter": "v1"},
        validator_params={"mode": "aasm"},
        job_id="job-1",
    )
    kwargs.update(overrides)
    job = enqueue(**kwargs)
    assert job is not None
    return job


# ---------------------------------------------------------------------------
# 1. In-memory state machine
# ---------------------------------------------------------------------------


def test_enqueue_appears_in_store():
    job = _enqueue_one(owner_user_id=42)
    assert job.state == ValidationRunState.QUEUED
    assert get_job(job.job_id) is job
    assert any(j is job for j in list_jobs())


def test_enqueue_returns_none_when_queue_full():
    for i in range(MAX_QUEUED):
        assert _enqueue_one(run_id=i, job_id=f"job-{i}") is not None
    assert (
        enqueue(
            run_id=99,
            profile_id=1,
            validator_type="events",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 7),
            engine_identity={},
            validator_params={},
            job_id="job-overflow",
        )
        is None
    )


def test_owner_scoping():
    job = _enqueue_one(owner_user_id=7)
    assert list_jobs(owner_user_id=7) == [job]
    assert list_jobs(owner_user_id=99) == []


def test_cancel_queued_transitions_and_removes():
    job = _enqueue_one()
    assert len(vjobs._queue) == 1
    assert cancel_job(job.job_id) is True
    assert job.state == ValidationRunState.CANCELLED
    assert len(vjobs._queue) == 0


def test_forget_removes_twin_from_store():
    job = _enqueue_one()
    assert get_job(job.job_id) is job
    vjobs.forget(job.job_id)
    assert get_job(job.job_id) is None
    vjobs.forget(job.job_id)  # idempotent — no error when already gone


def test_try_start_after_cancel_flag_yields_cancelled():
    job = _enqueue_one()
    with job._lock:
        job._cancel_flag = True
    assert job.try_start() is False
    assert job.state == ValidationRunState.CANCELLED
    assert job.finished_at is not None


def test_finish_with_cancel_flag_yields_cancelled():
    job = _enqueue_one()
    job.try_start()
    with job._lock:
        job._cancel_flag = True
    job.finish(succeeded=True)
    assert job.state == ValidationRunState.CANCELLED


def test_finish_failed_sets_error():
    job = _enqueue_one()
    job.try_start()
    job.finish(succeeded=False, error_message="boom")
    assert job.state == ValidationRunState.FAILED
    assert job.error_message == "boom"


def test_reap_terminal_removes_expired():
    job = _enqueue_one()
    cancel_job(job.job_id)
    with job._lock:
        job._finished_at = time.monotonic() - vjobs.JOB_TTL_SECONDS - 1
    vjobs._reap_terminal()
    assert get_job(job.job_id) is None


def test_shutdown_cancels_queued():
    job = _enqueue_one()
    vjobs.shutdown(timeout=0.1)
    assert job.state == ValidationRunState.CANCELLED


def test_to_dict_shape():
    job = _enqueue_one(owner_user_id=5)
    d = job.to_dict()
    assert d["run_id"] == 1
    assert d["job_id"] == "job-1"
    assert d["validator_type"] == "events"
    assert d["date_from"] == "2024-01-01"
    assert d["date_to"] == "2024-01-07"
    assert d["state"] == "queued"
    assert d["owner_user_id"] == 5
    assert d["reused"] is False
    assert d["started_at"] is None
    assert d["finished_at"] is None
    # Timestamps are wall-clock datetimes, not time.monotonic().
    assert isinstance(d["created_at"], datetime)
    assert abs((d["created_at"] - datetime.now(UTC)).total_seconds()) < 60


# ---------------------------------------------------------------------------
# 2. Registry seam
# ---------------------------------------------------------------------------


def test_all_validator_types_are_registered():
    from typing import get_args

    from snore.api.schemas import ValidatorType

    # The ValidatorType Literal is the single source of accepted types; every one
    # of its args must have a registered spec.
    assert vreg.registered_types() == frozenset(get_args(ValidatorType))
    assert vreg.registered_types() == frozenset(
        {"events", "fl", "breaths", "rera", "apple"}
    )


@pytest.mark.parametrize("vtype", ["events", "fl", "breaths", "rera"])
def test_job_validators_have_specs(vtype):
    spec = vreg.get_spec(vtype)
    assert spec is not None
    assert spec.mode is RunMode.JOB
    # current_params is stable across calls (dedup relies on it).
    assert spec.current_params(None) == spec.current_params(None)


def test_apple_is_a_sync_validator():
    spec = vreg.get_spec("apple")
    assert spec is not None
    assert spec.mode is RunMode.SYNC


def test_rera_params_carry_proxy_tunables():
    from snore.analysis.modes.postprocess import EVENT_MATCH_TOLERANCE_SECONDS
    from snore.analysis.shared.versioning import RERA_PROXY_ALGO_VERSION
    from snore.constants import RERAProxyConstants

    assert vreg.get_spec("rera").current_params(None) == {
        "rera_proxy_algo_version": RERA_PROXY_ALGO_VERSION,
        "fl_class_threshold": RERAProxyConstants.FL_CLASS_THRESHOLD,
        "min_fl_run_length": RERAProxyConstants.MIN_FL_RUN_LENGTH,
        "recovery_amplitude_margin": RERAProxyConstants.RECOVERY_AMPLITUDE_MARGIN,
        "match_tolerance_seconds": EVENT_MATCH_TOLERANCE_SECONDS,
    }


def test_apple_params_carry_min_pairs():
    from snore.validation.apple_cross_report import _MIN_PAIRS

    assert vreg.get_spec("apple").current_params(None) == {"min_pairs": _MIN_PAIRS}


def test_apple_params_include_device_id_only_when_pinned():
    """device_id changes results, so it enters the dedup key when pinned and is
    omitted otherwise (keeping the unpinned key identical to pre-pinning)."""
    spec = vreg.get_spec("apple")
    assert "device_id" not in spec.current_params(None)
    assert "device_id" not in spec.current_params({"device_id": None})
    pinned = spec.current_params({"device_id": 7})
    assert pinned["device_id"] == 7
    # A pinned run's params differ from an unpinned run's → they never dedup.
    assert pinned != spec.current_params(None)


def test_events_params_carry_mode():
    assert vreg.get_spec("events").current_params({"mode": "resmed"}) == {
        "mode": "resmed"
    }


def test_engine_identity_matches_algorithm_identity():
    from snore.analysis.shared.versioning import AlgorithmIdentity

    assert vreg.engine_identity() == AlgorithmIdentity.current().model_dump()


# ---------------------------------------------------------------------------
# 3. Persistence against a real migrated DB
# ---------------------------------------------------------------------------

_IDENTITY = {"segmenter": "v1", "fl_classifier": "v2"}
_PARAMS = {"mode": "aasm"}


def _run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


async def _seed_run(**overrides: Any) -> int:
    from snore.database import models
    from snore.database.session import session_scope

    now = datetime.now(UTC)
    values = dict(
        job_id=None,
        profile_id=1,
        owner_user_id=None,
        validator_type="events",
        date_from=date(2024, 1, 1),
        date_to=date(2024, 1, 7),
        engine_identity_json=_IDENTITY,
        validator_params_json=_PARAMS,
        report_json={"ok": True},
        state="succeeded",
        created_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
    )
    values.update(overrides)
    async with session_scope(immediate=True) as db:
        row = models.ValidationRun(**values)
        db.add(row)
        await db.flush()
        return row.id


async def _fetch_run(run_id: int) -> Any:
    from snore.database import models
    from snore.database.session import session_scope

    async with session_scope() as db:
        return await db.get(models.ValidationRun, run_id)


@pytest.fixture
def init_db(temp_db: Any) -> Any:
    """Initialise the global database at a temp path; migrate + tear down."""
    from snore.database.session import cleanup_database, init_database

    # init_database() is process-global and returns early when another test has
    # already initialized an engine.  Reset first so this fixture always owns
    # the unique temp_db it was given, regardless of test execution order.
    _run(cleanup_database())
    _run(init_database(str(temp_db)))
    yield
    _run(cleanup_database())


def test_enqueue_run_persist_lifecycle(init_db, snapshot_registry):
    """insert_queued_run → _execute_job (stub validator) → row SUCCEEDED + report."""

    async def _stub_run(
        db: Any, profile_id: int, date_from: str, date_to: str, params: Any
    ) -> _StubReport:
        return _StubReport(ok=True, n=3)

    vreg.register(
        ValidatorSpec(
            validator_type="events",
            mode=RunMode.JOB,
            run=_stub_run,
            current_params=lambda p: _PARAMS,
        )
    )

    run_id = _run(
        vjobs.insert_queued_run(
            job_id="job-lifecycle",
            profile_id=1,
            owner_user_id=5,
            validator_type="events",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 7),
            engine_identity=_IDENTITY,
            validator_params=_PARAMS,
        )
    )
    job = _enqueue_one(
        run_id=run_id, job_id="job-lifecycle", owner_user_id=5, validator_type="events"
    )

    vjobs._execute_job(job)

    assert job.state == ValidationRunState.SUCCEEDED
    row = _run(_fetch_run(run_id))
    assert row.state == "succeeded"
    assert row.report_json == {"ok": True, "n": 3}
    assert row.finished_at is not None


def test_execute_job_failure_persists_error(init_db, snapshot_registry):
    async def _boom(
        db: Any, profile_id: int, date_from: str, date_to: str, params: Any
    ) -> _StubReport:
        raise RuntimeError("kaboom")

    vreg.register(
        ValidatorSpec(
            validator_type="events",
            mode=RunMode.JOB,
            run=_boom,
            current_params=lambda p: _PARAMS,
        )
    )

    run_id = _run(
        vjobs.insert_queued_run(
            job_id="job-fail",
            profile_id=1,
            owner_user_id=None,
            validator_type="events",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 7),
            engine_identity=_IDENTITY,
            validator_params=_PARAMS,
        )
    )
    job = _enqueue_one(run_id=run_id, job_id="job-fail", validator_type="events")

    vjobs._execute_job(job)

    assert job.state == ValidationRunState.FAILED
    row = _run(_fetch_run(run_id))
    assert row.state == "failed"
    assert "kaboom" in row.error_message
    assert row.report_json is None


def test_dedup_hit_returns_matching_run(init_db):
    async def _check() -> None:
        from snore.database.session import session_scope

        run_id = await _seed_run(owner_user_id=5)
        async with session_scope() as db:
            found = await vjobs.find_reusable_run(
                db,
                profile_id=1,
                validator_type="events",
                date_from=date(2024, 1, 1),
                date_to=date(2024, 1, 7),
                engine_identity=_IDENTITY,
                validator_params=_PARAMS,
                owner_user_id=5,
            )
        assert found is not None
        assert found.id == run_id

    _run(_check())


def test_dedup_miss_on_identity_or_params(init_db):
    async def _check() -> None:
        from snore.database.session import session_scope

        await _seed_run()
        async with session_scope() as db:
            # Engine identity differs → no reuse.
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="events",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity={"segmenter": "v2"},
                    validator_params=_PARAMS,
                    owner_user_id=None,
                )
                is None
            )
            # Params differ → no reuse.
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="events",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params={"mode": "resmed"},
                    owner_user_id=None,
                )
                is None
            )

    _run(_check())


@pytest.mark.parametrize("state", ["failed", "cancelled", "queued", "running"])
def test_dedup_skips_non_succeeded(init_db, state):
    async def _check() -> None:
        from snore.database.session import session_scope

        await _seed_run(state=state, report_json=None)
        async with session_scope() as db:
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="events",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=_PARAMS,
                    owner_user_id=None,
                )
                is None
            )

    _run(_check())


@pytest.mark.parametrize("state", ["queued", "running"])
def test_find_inflight_matches_non_terminal(init_db, state):
    async def _check() -> None:
        from snore.database.session import session_scope

        run_id = await _seed_run(state=state, report_json=None)
        async with session_scope() as db:
            found = await vjobs.find_inflight_run(
                db,
                profile_id=1,
                validator_type="events",
                date_from=date(2024, 1, 1),
                date_to=date(2024, 1, 7),
                engine_identity=_IDENTITY,
                validator_params=_PARAMS,
                owner_user_id=None,
            )
        assert found is not None
        assert found.id == run_id

    _run(_check())


def test_find_inflight_ignores_succeeded(init_db):
    async def _check() -> None:
        from snore.database.session import session_scope

        await _seed_run(state="succeeded")
        async with session_scope() as db:
            assert (
                await vjobs.find_inflight_run(
                    db,
                    profile_id=1,
                    validator_type="events",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=_PARAMS,
                    owner_user_id=None,
                )
                is None
            )

    _run(_check())


def test_persist_run_after_row_deletion_does_not_reinsert(init_db, snapshot_registry):
    """A terminal persist for a concurrently-deleted run must not re-create it."""

    async def _stub_run(
        db: Any, profile_id: int, date_from: str, date_to: str, params: Any
    ) -> _StubReport:
        return _StubReport(ok=True, n=1)

    vreg.register(
        ValidatorSpec(
            validator_type="events",
            mode=RunMode.JOB,
            run=_stub_run,
            current_params=lambda p: _PARAMS,
        )
    )

    run_id = _run(
        vjobs.insert_queued_run(
            job_id="job-del",
            profile_id=1,
            owner_user_id=None,
            validator_type="events",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 7),
            engine_identity=_IDENTITY,
            validator_params=_PARAMS,
        )
    )
    job = _enqueue_one(run_id=run_id, job_id="job-del", validator_type="events")

    # Delete the row out from under the worker, then drive it to a terminal
    # persist.  The UPDATE-by-id must be a no-op, not resurrect the row.
    _run(vjobs.delete_run(run_id))
    job.try_start()
    job.set_report({"ok": True})
    job.finish(succeeded=True)
    _run(vjobs._persist_run(job))

    assert _run(_fetch_run(run_id)) is None


def test_dedup_owner_scoping(init_db):
    async def _check() -> None:
        from snore.database.session import session_scope

        await _seed_run(owner_user_id=7)
        async with session_scope() as db:
            # A different owner cannot reuse another user's run.
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="events",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=_PARAMS,
                    owner_user_id=99,
                )
                is None
            )

    _run(_check())


def test_create_sync_run_inserts_succeeded_row(init_db):
    async def _check() -> None:
        from snore.database.session import session_scope

        async with session_scope() as db:
            run = await vjobs.create_sync_run(
                db,
                profile_id=1,
                owner_user_id=5,
                validator_type="apple",
                date_from=date(2024, 1, 1),
                date_to=date(2024, 1, 7),
                engine_identity=_IDENTITY,
                validator_params={},
                report_json={"ok": True},
            )
            run_id = run.id
        row = await _fetch_run(run_id)
        assert row.job_id is None
        assert row.state == "succeeded"
        assert row.report_json == {"ok": True}

    _run(_check())


def test_recover_orphaned_runs_marks_non_terminal_failed(init_db):
    async def _check() -> None:
        queued = await _seed_run(state="queued", job_id="q", report_json=None)
        running = await _seed_run(state="running", job_id="r", report_json=None)
        done = await _seed_run(state="succeeded", job_id="s")

        count = await vjobs.recover_orphaned_runs()
        assert count == 2

        assert (await _fetch_run(queued)).state == "failed"
        assert (await _fetch_run(running)).state == "failed"
        assert (await _fetch_run(done)).state == "succeeded"

    _run(_check())


def test_job_mode_validator_uses_short_per_session_scopes(init_db):
    """A JOB validator (db_session=None) opens its own scopes and still runs.

    Exercises the WAL-snapshot fix end-to-end with real sessions: the list query
    and each per-session validation run under their own short ``session_scope()``
    (no single read transaction spans the run).  The seeded session has no FLG
    waveform, so it is skipped — the point is that the None-mode scope plumbing
    resolves against a real DB.
    """

    async def _check() -> None:
        from datetime import datetime as _dt

        from snore.database import models
        from snore.database.session import session_scope
        from snore.validation import FlowLimitationValidator

        async with session_scope(immediate=True) as db:
            user = models.User(canonical_email="jobmode@example.com", role="admin")
            db.add(user)
            await db.flush()
            profile = models.Profile(user_id=user.id, name="JobMode")
            db.add(profile)
            await db.flush()
            device = models.Device(
                profile_id=profile.id,
                manufacturer="ResMed",
                model="AirSense 11",
                serial_number="SN1",
            )
            db.add(device)
            await db.flush()
            session = models.Session(
                device_id=device.id,
                device_session_id="s1",
                start_time=_dt(2024, 1, 3, 22, 0, 0),
                end_time=_dt(2024, 1, 4, 6, 0, 0),
                duration_seconds=28800.0,
            )
            db.add(session)
            await db.flush()
            profile_id = profile.id

        # None → JOB mode: the validator manages its own per-session scopes.
        report = await FlowLimitationValidator(None, profile_id).validate_date_range(
            date_from="2024-01-01", date_to="2024-01-31"
        )
        assert len(report.sessions) == 1
        assert report.sessions[0].skipped_reason == "no_flg_waveform"

    _run(_check())


def test_prune_retention_keeps_newest_per_group(init_db):
    async def _check() -> None:
        base = datetime(2024, 1, 1, tzinfo=UTC)
        ids = []
        for i in range(3):
            ids.append(
                await _seed_run(job_id=f"run-{i}", created_at=base + timedelta(days=i))
            )
        # A different group must be untouched by the prune.
        other = await _seed_run(validator_type="fl", job_id="fl-0")

        deleted = await vjobs.prune_retention(keep=2)
        assert deleted == 1

        # Oldest (ids[0]) pruned; the two newest survive.
        assert await _fetch_run(ids[0]) is None
        assert await _fetch_run(ids[1]) is not None
        assert await _fetch_run(ids[2]) is not None
        assert await _fetch_run(other) is not None

    _run(_check())


# ---------------------------------------------------------------------------
# 4. rera (JOB) — lifecycle + dedup on a changed proxy tunable
# ---------------------------------------------------------------------------

_RERA_PARAMS = vreg.get_spec("rera").current_params(None)


def test_rera_job_lifecycle(init_db, snapshot_registry):
    """rera enqueue → _execute_job (stub run) → row SUCCEEDED with report."""

    async def _stub_run(
        db: Any, profile_id: int, date_from: str, date_to: str, params: Any
    ) -> _StubReport:
        return _StubReport(ok=True, n=7)

    vreg.register(
        ValidatorSpec(
            validator_type="rera",
            mode=RunMode.JOB,
            run=_stub_run,
            current_params=lambda p: _RERA_PARAMS,
        )
    )

    run_id = _run(
        vjobs.insert_queued_run(
            job_id="job-rera",
            profile_id=1,
            owner_user_id=5,
            validator_type="rera",
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 7),
            engine_identity=_IDENTITY,
            validator_params=_RERA_PARAMS,
        )
    )
    job = _enqueue_one(
        run_id=run_id, job_id="job-rera", owner_user_id=5, validator_type="rera"
    )

    vjobs._execute_job(job)

    assert job.state == ValidationRunState.SUCCEEDED
    row = _run(_fetch_run(run_id))
    assert row.state == "succeeded"
    assert row.report_json == {"ok": True, "n": 7}


def test_rera_dedup_changed_tunable_forces_new_run(init_db, monkeypatch):
    """A changed RERA-proxy tunable changes validator_params_json → no reuse."""

    async def _check() -> None:
        from snore.constants import RERAProxyConstants
        from snore.database.session import session_scope

        # Seed a succeeded rera run stamped with the CURRENT proxy tunables.
        baseline = vreg.get_spec("rera").current_params(None)
        await _seed_run(
            validator_type="rera",
            validator_params_json=baseline,
        )

        # Same tunables → dedup hit.
        async with session_scope() as db:
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="rera",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=baseline,
                    owner_user_id=None,
                )
                is not None
            )

        # Bump a proxy tunable → current_params changes → the seeded run no
        # longer matches, so a fresh run is required.
        monkeypatch.setattr(RERAProxyConstants, "FL_CLASS_THRESHOLD", 99)
        changed = vreg.get_spec("rera").current_params(None)
        assert changed != baseline
        async with session_scope() as db:
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="rera",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=changed,
                    owner_user_id=None,
                )
                is None
            )

    _run(_check())


def test_apple_dedup_distinguishes_device_pinning(init_db):
    """An unpinned apple run must not be reused for a device-pinned request."""

    async def _check() -> None:
        from snore.database.session import session_scope

        unpinned = vreg.get_spec("apple").current_params(None)
        pinned = vreg.get_spec("apple").current_params({"device_id": 7})
        await _seed_run(validator_type="apple", validator_params_json=unpinned)

        async with session_scope() as db:
            # Same (unpinned) params → dedup hit.
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="apple",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=unpinned,
                    owner_user_id=None,
                )
                is not None
            )
            # Pinned params → no reuse of the unpinned run.
            assert (
                await vjobs.find_reusable_run(
                    db,
                    profile_id=1,
                    validator_type="apple",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 1, 7),
                    engine_identity=_IDENTITY,
                    validator_params=pinned,
                    owner_user_id=None,
                )
                is None
            )

    _run(_check())
