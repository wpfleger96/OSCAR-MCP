"""HTTP-surface tests for the persisted validation-run endpoints.

Covers the paths that resolve entirely through the overridden ``get_db``
session: invalid-type rejection, dedup reuse, listing/filtering, detail
retrieval, ownership isolation, deletion of a finished run, and the synchronous
``apple`` run end-to-end.  The background enqueue path (which commits through
the global ``session_scope``) is covered at unit level in
``tests/unit/test_validation_jobs.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import snore.api.validation_jobs as vjobs

from snore.analysis.shared.versioning import AlgorithmIdentity
from snore.api.validation_jobs import ValidationRunJob, ValidationRunState
from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client

_FROM = date(2024, 1, 1)
_TO = date(2024, 1, 7)


def _assert_utc_timestamp(value: str) -> None:
    assert datetime.fromisoformat(value).utcoffset() == timedelta(0)


@pytest.fixture(autouse=True)
def _clean_vjobs():
    """Isolate the module-global in-memory validation-job store around each test."""
    vjobs._all_jobs.clear()
    vjobs._queue.clear()
    yield
    vjobs.shutdown(timeout=1.0)
    vjobs._all_jobs.clear()
    vjobs._queue.clear()


def _twin(run_id: int, *, job_id: str, state: ValidationRunState) -> ValidationRunJob:
    """Register an in-memory job twin directly in the store (no worker involved)."""
    job = ValidationRunJob(
        job_id=job_id,
        run_id=run_id,
        profile_id=1,
        validator_type="events",
        date_from=_FROM,
        date_to=_TO,
        engine_identity={},
        validator_params={},
    )
    job._state = state
    vjobs._store.jobs[job_id] = job
    return job


@pytest.fixture
def seeded(db_session: Any) -> tuple[ActorContext, int, int]:
    """Seed a User + Profile and return (actor, profile_id, user_id)."""
    import uuid

    user = models.User(
        canonical_email=f"vr_{uuid.uuid4().hex[:8]}@example.com", role="admin"
    )
    db_session.add(user)
    db_session.flush()
    profile = models.Profile(user_id=user.id, name="VR Profile")
    db_session.add(profile)
    db_session.flush()
    actor = ActorContext(
        user_id=user.id,
        profile_id=profile.id,
        role=Role.ADMIN,
        mode=AuthMode.LOCAL,
    )
    return actor, profile.id, user.id


def _seed_run(
    db_session: Any, *, profile_id: int, owner_user_id: int, **overrides: Any
) -> int:
    now = datetime.now(UTC)
    values = dict(
        job_id=None,
        profile_id=profile_id,
        owner_user_id=owner_user_id,
        validator_type="events",
        date_from=_FROM,
        date_to=_TO,
        engine_identity_json=AlgorithmIdentity.current().model_dump(),
        validator_params_json={"mode": "aasm"},
        report_json={"aggregate": {"total_sessions": 3}},
        state="succeeded",
        created_at=now,
        started_at=now,
        finished_at=now,
        updated_at=now,
    )
    values.update(overrides)
    row = models.ValidationRun(**values)
    db_session.add(row)
    db_session.flush()
    return row.id


def _client(async_db_session: AsyncSession, actor: ActorContext) -> TestClient:
    return make_test_client(async_db_session, actor=actor)


def test_invalid_type_returns_422(async_db_session: AsyncSession, seeded: Any) -> None:
    # Every ValidatorType Literal value is now registered, so an unknown type is
    # rejected by request validation (422) before the handler runs. The 400
    # "unregistered" branch remains as defensive code for a future Literal value
    # added ahead of its registration.
    actor, _pid, _uid = seeded
    client = _client(async_db_session, actor)
    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "bogus",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    assert resp.status_code == 422


def test_dedup_hit_returns_reused(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    actor, pid, uid = seeded
    run_id = _seed_run(db_session, profile_id=pid, owner_user_id=uid)
    client = _client(async_db_session, actor)

    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "events",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    # A dedup-reuse hit is already terminal — nothing was queued, so 200, not 202.
    assert resp.status_code == 200
    body = resp.json()
    assert body["reused"] is True
    assert body["run_id"] == run_id
    assert body["state"] == "succeeded"


def test_list_and_filter_runs(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    actor, pid, uid = seeded
    _seed_run(db_session, profile_id=pid, owner_user_id=uid)
    _seed_run(db_session, profile_id=pid, owner_user_id=uid, validator_type="fl")
    client = _client(async_db_session, actor)

    all_runs = client.get("/api/v1/validate/runs").json()
    assert all_runs["total"] == 2

    run = all_runs["runs"][0]
    _assert_utc_timestamp(run["created_at"])
    _assert_utc_timestamp(run["started_at"])
    _assert_utc_timestamp(run["finished_at"])

    fl_only = client.get("/api/v1/validate/runs?validator_type=fl").json()
    assert fl_only["total"] == 1
    assert fl_only["runs"][0]["validator_type"] == "fl"


def test_list_in_memory_run_has_null_execution_timestamps(
    async_db_session: AsyncSession, seeded: Any
) -> None:
    actor, _pid, _uid = seeded
    _twin(123, job_id="queued-memory-run", state=ValidationRunState.QUEUED)
    client = _client(async_db_session, actor)

    response = client.get("/api/v1/validate/runs")
    assert response.status_code == 200
    run = next(
        run for run in response.json()["runs"] if run["job_id"] == "queued-memory-run"
    )
    _assert_utc_timestamp(run["created_at"])
    assert run["started_at"] is None
    assert run["finished_at"] is None


def test_get_run_detail_includes_report(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    actor, pid, uid = seeded
    run_id = _seed_run(db_session, profile_id=pid, owner_user_id=uid)
    client = _client(async_db_session, actor)

    resp = client.get(f"/api/v1/validate/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["report_json"] == {"aggregate": {"total_sessions": 3}}


def test_get_foreign_run_returns_404(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    actor, pid, uid = seeded
    # Owned by a different user → not visible.
    run_id = _seed_run(db_session, profile_id=pid, owner_user_id=uid + 999)
    client = _client(async_db_session, actor)

    assert client.get(f"/api/v1/validate/runs/{run_id}").status_code == 404


def test_delete_finished_run(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    actor, pid, uid = seeded
    run_id = _seed_run(db_session, profile_id=pid, owner_user_id=uid)
    client = _client(async_db_session, actor)

    assert client.delete(f"/api/v1/validate/runs/{run_id}").status_code == 204
    assert client.get(f"/api/v1/validate/runs/{run_id}").status_code == 404


def test_apple_sync_run_end_to_end(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    """apple is a SYNC validator: POST computes inline and persists a
    succeeded, job-less run that GET can immediately read back."""
    actor, _pid, _uid = seeded
    client = _client(async_db_session, actor)

    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "apple",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    # SYNC apple is computed inline and already terminal → 200, not 202.
    assert resp.status_code == 200
    body = resp.json()
    assert body["validator_type"] == "apple"
    assert body["state"] == "succeeded"
    assert body["job_id"] is None  # synchronous run — never queued
    assert body["reused"] is False
    # min_pairs tunable stamped into the params half of the dedup key.
    assert "min_pairs" in body["validator_params"]

    run_id = body["run_id"]
    detail = client.get(f"/api/v1/validate/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["report_json"] is not None

    # A second identical request dedups onto the first succeeded run.
    again = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "apple",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    assert again.status_code == 200
    assert again.json()["reused"] is True
    assert again.json()["run_id"] == run_id


def test_force_bypasses_dedup(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    """force=True computes a fresh run even when a matching succeeded run exists."""
    actor, pid, uid = seeded
    existing = _seed_run(
        db_session,
        profile_id=pid,
        owner_user_id=uid,
        validator_type="apple",
        validator_params_json={"min_pairs": 5},
    )
    client = _client(async_db_session, actor)

    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "apple",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
            "force": True,
        },
    )
    assert resp.status_code == 200  # SYNC apple completes inline
    body = resp.json()
    assert body["reused"] is False
    assert body["run_id"] != existing


def test_dedup_skips_failed_run(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    """A FAILED run must not be reused; a fresh run is computed instead."""
    actor, pid, uid = seeded
    failed = _seed_run(
        db_session,
        profile_id=pid,
        owner_user_id=uid,
        validator_type="apple",
        validator_params_json={"min_pairs": 5},
        state="failed",
        report_json=None,
    )
    client = _client(async_db_session, actor)

    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "apple",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reused"] is False
    assert body["run_id"] != failed
    assert body["state"] == "succeeded"


def test_inflight_dedup_collapses_duplicate(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    """A duplicate request for an already-queued run returns it, not a new job."""
    actor, pid, uid = seeded
    queued = _seed_run(
        db_session,
        profile_id=pid,
        owner_user_id=uid,
        validator_type="events",
        state="queued",
        job_id="inflight-1",
        report_json=None,
    )
    client = _client(async_db_session, actor)

    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "events",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["run_id"] == queued
    # The request collapsed onto the in-flight run: no second job was enqueued.
    assert len(vjobs._queue) == 0


def test_delete_running_job_cancels_and_keeps_row(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    """DELETE of a still-queued run cancels it (202) and the row persists."""
    actor, pid, uid = seeded
    run_id = _seed_run(
        db_session,
        profile_id=pid,
        owner_user_id=uid,
        state="queued",
        job_id="cancel-1",
        report_json=None,
    )
    twin = _twin(run_id, job_id="cancel-1", state=ValidationRunState.QUEUED)
    client = _client(async_db_session, actor)

    resp = client.delete(f"/api/v1/validate/runs/{run_id}")
    assert resp.status_code == 202
    assert twin.state is ValidationRunState.CANCELLED
    # The row was not deleted (still fetchable).
    assert client.get(f"/api/v1/validate/runs/{run_id}").status_code == 200


def test_delete_completed_job_forgets_twin(
    async_db_session: AsyncSession, db_session: Any, seeded: Any
) -> None:
    """Deleting a finished JOB run removes it from the list immediately.

    Without forgetting the in-memory twin, the merged list would resurrect the
    just-deleted run until the TTL reaper ran.
    """
    actor, pid, uid = seeded
    run_id = _seed_run(db_session, profile_id=pid, owner_user_id=uid, job_id="done-1")
    _twin(run_id, job_id="done-1", state=ValidationRunState.SUCCEEDED)
    client = _client(async_db_session, actor)

    # The twin makes the run visible in the list before deletion.
    assert client.get("/api/v1/validate/runs").json()["total"] == 1

    assert client.delete(f"/api/v1/validate/runs/{run_id}").status_code == 204
    assert vjobs.get_job("done-1") is None  # twin forgotten
    assert client.get("/api/v1/validate/runs").json()["total"] == 0
    assert client.get(f"/api/v1/validate/runs/{run_id}").status_code == 404


async def test_job_enqueue_returns_queued(temp_db: Any) -> None:
    """POST /runs for a JOB validator enqueues and returns 202 + queued state.

    Exercises the real background-enqueue path (the global ``session_scope``
    insert) with no workers running, so the run stays queued.
    """
    import uuid

    from httpx import ASGITransport, AsyncClient

    from snore.api.app import create_app
    from snore.api.deps import get_actor
    from snore.database.session import cleanup_database, init_database, session_scope

    await init_database(str(temp_db))
    try:
        async with session_scope(immediate=True) as db:
            user = models.User(
                canonical_email=f"job_{uuid.uuid4().hex[:8]}@example.com", role="admin"
            )
            db.add(user)
            await db.flush()
            profile = models.Profile(user_id=user.id, name="Job Profile")
            db.add(profile)
            await db.flush()
            actor = ActorContext(
                user_id=user.id,
                profile_id=profile.id,
                role=Role.ADMIN,
                mode=AuthMode.LOCAL,
            )

        app = create_app()
        app.dependency_overrides[get_actor] = lambda: actor
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/validate/runs",
                json={
                    "validator_type": "fl",
                    "from_date": "2024-01-01",
                    "to_date": "2024-01-07",
                },
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["state"] == "queued"
        assert body["job_id"] is not None
        assert body["reused"] is False
    finally:
        vjobs.shutdown(timeout=1.0)
        vjobs._all_jobs.clear()
        vjobs._queue.clear()
        await cleanup_database()


def test_bad_device_id_returns_422(async_db_session: AsyncSession, seeded: Any) -> None:
    """A non-integer apple device_id is a clean request error, not a 500."""
    actor, _pid, _uid = seeded
    client = _client(async_db_session, actor)

    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "apple",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
            "params": {"device_id": "not-an-int"},
        },
    )
    assert resp.status_code == 422
