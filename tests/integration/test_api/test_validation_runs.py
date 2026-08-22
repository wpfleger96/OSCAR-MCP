"""HTTP-surface tests for the persisted validation-run endpoints.

Covers the paths that resolve entirely through the overridden ``get_db``
session: unregistered-type rejection, dedup reuse, listing/filtering, detail
retrieval, ownership isolation, and deletion of a finished run.  The background
enqueue path (which commits through the global ``session_scope``) is covered at
unit level in ``tests/unit/test_validation_jobs.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.shared.versioning import AlgorithmIdentity
from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client

_FROM = date(2024, 1, 1)
_TO = date(2024, 1, 7)


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


def test_unregistered_type_returns_400(
    async_db_session: AsyncSession, seeded: Any
) -> None:
    actor, _pid, _uid = seeded
    client = _client(async_db_session, actor)
    resp = client.post(
        "/api/v1/validate/runs",
        json={
            "validator_type": "rera",
            "from_date": "2024-01-01",
            "to_date": "2024-01-07",
        },
    )
    assert resp.status_code == 400
    assert "rera" in resp.json()["detail"]


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
    assert resp.status_code == 202
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

    fl_only = client.get("/api/v1/validate/runs?validator_type=fl").json()
    assert fl_only["total"] == 1
    assert fl_only["runs"][0]["validator_type"] == "fl"


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
