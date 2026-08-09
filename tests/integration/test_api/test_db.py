import inspect
import uuid

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client
from tests.integration.test_api.conftest import _multiuser_env


def _patch_target(temp_db: object) -> object:
    """Context manager that patches the DB router to use the test database."""
    from snore.database.target import DatabaseTarget

    target = DatabaseTarget.from_url(str(temp_db))
    return patch("snore.api.routers.db._get_target", return_value=target)


def _member_actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=999,
        role=Role.MEMBER,
        mode=AuthMode.LOCAL,
    )


def _admin_actor(user_id: int, profile_id: int = 1) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=profile_id,
        role=Role.ADMIN,
        mode=AuthMode.MULTIUSER,
    )


def _demo_actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=999,
        role=Role.DEMO,
        mode=AuthMode.MULTIUSER,
    )


class TestDbStats:
    def test_stats_excludes_db_path(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert "db_path" not in data

    def test_stats_empty_db_counts_zero(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert data["session_count"] == 0
        assert data["device_count"] == 0

    def test_stats_counts_after_insert(
        self, api_client, db_session, test_device, test_session_factory, temp_db
    ):
        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        data = response.json()
        assert data["device_count"] >= 1
        assert data["session_count"] >= 1

    def test_admin_gets_200(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.get("/api/v1/db/stats")
        assert response.status_code == 200

    def test_member_gets_403(self, async_db_session, db_session, temp_db):
        member = models.User(
            canonical_email=f"member_{uuid.uuid4().hex[:8]}@test.local",
            role="member",
        )
        db_session.add(member)
        db_session.flush()
        client = make_test_client(async_db_session, actor=_member_actor(member.id))
        with _patch_target(temp_db):
            response = client.get("/api/v1/db/stats")
        assert response.status_code == 403


class TestDbVacuum:
    def test_vacuum_status_is_success(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/vacuum")
        assert response.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Reset — local mode (backward compat) and multiuser mode
# ---------------------------------------------------------------------------


class TestDbReset:
    def test_reset_status_is_success(self, api_client, temp_db):
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/reset")
        assert response.json()["status"] == "success"

    def test_reset_clears_data(
        self, api_client, db_session, test_device, test_session_factory, temp_db
    ):
        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/reset")
        assert response.json()["total_rows_deleted"] >= 1

    def test_reset_no_body_uses_data_only_default(self, api_client, temp_db):
        """Empty POST (no body) defaults to data-only reset — no bootstrap_invite_url."""
        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/reset")
        data = response.json()
        assert data["status"] == "success"
        assert data.get("bootstrap_invite_url") is None

    def test_reset_explicit_data_only(self, api_client, temp_db):
        """include_accounts=false explicitly — same result as default."""
        with _patch_target(temp_db):
            response = api_client.post(
                "/api/v1/db/reset", json={"include_accounts": False}
            )
        data = response.json()
        assert data["status"] == "success"
        assert data.get("bootstrap_invite_url") is None


class TestDbResetMultiuser:
    """Reset tests with an explicit admin actor in multiuser mode."""

    def _setup_multiuser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _multiuser_env(monkeypatch)
        from snore.api.config import load_config, set_config  # noqa: PLC0415

        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

    def _make_admin_client(
        self,
        async_db_session: AsyncSession,
        db_session: SyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[object, models.User, models.Profile]:
        self._setup_multiuser(monkeypatch)

        user = models.User(
            canonical_email=f"admin_{uuid.uuid4().hex[:8]}@test.local",
            role="admin",
        )
        db_session.add(user)
        db_session.flush()
        profile = models.Profile(user_id=user.id, name="Default")
        db_session.add(profile)
        db_session.flush()
        user.default_profile_id = profile.id
        db_session.flush()  # persist default_profile_id before any resets can delete users

        actor = _admin_actor(user.id, profile.id)
        client = make_test_client(async_db_session, actor=actor)
        # Include origin so multiuser CSRF middleware allows mutating requests.
        client.headers.update({"origin": "http://127.0.0.1:8000"})
        return client, user, profile

    def test_member_gets_403(self, async_db_session, db_session, monkeypatch, temp_db):
        """Non-admin users cannot trigger a reset."""
        self._setup_multiuser(monkeypatch)

        member = models.User(
            canonical_email=f"member_{uuid.uuid4().hex[:8]}@test.local",
            role="member",
        )
        db_session.add(member)
        db_session.flush()
        client = make_test_client(async_db_session, actor=_member_actor(member.id))

        with _patch_target(temp_db):
            response = client.post("/api/v1/db/reset")
        assert response.status_code == 403

    def test_demo_gets_403(self, async_db_session, db_session, monkeypatch, temp_db):
        """Demo role cannot trigger a reset."""
        self._setup_multiuser(monkeypatch)

        demo_user = models.User(
            canonical_email=f"demo_{uuid.uuid4().hex[:8]}@test.local",
            role="demo",
        )
        db_session.add(demo_user)
        db_session.flush()
        client = make_test_client(async_db_session, actor=_demo_actor(demo_user.id))

        with _patch_target(temp_db):
            response = client.post("/api/v1/db/reset")
        assert response.status_code == 403

    def test_data_only_reset_zeroes_sleep_data_preserves_accounts(
        self,
        async_db_session,
        db_session,
        test_device,
        test_session_factory,
        monkeypatch,
        temp_db,
    ):
        """Data-only reset (default) clears sleep data but preserves users and profiles."""
        from sqlalchemy import func  # noqa: PLC0415
        from sqlalchemy import select as sa_select  # noqa: PLC0415

        test_session_factory(test_device.id, start_time=datetime(2025, 1, 1, 22, 0))
        db_session.commit()

        client, user, profile = self._make_admin_client(
            async_db_session, db_session, monkeypatch
        )

        with _patch_target(temp_db):
            response = client.post("/api/v1/db/reset", json={"include_accounts": False})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["total_rows_deleted"] >= 1
        assert data.get("bootstrap_invite_url") is None

        # Verify: user and profile rows survive the data-only reset.
        db_session.expire_all()
        surviving_user = db_session.get(models.User, user.id)
        surviving_profile = db_session.get(models.Profile, profile.id)
        assert surviving_user is not None, "User row must survive data-only reset"
        assert surviving_profile is not None, "Profile row must survive data-only reset"

        # Verify: sleep data is gone.
        session_count = db_session.execute(
            sa_select(func.count()).select_from(models.Session)
        ).scalar()
        assert session_count == 0, "All sessions must be deleted by data-only reset"

    def test_include_accounts_reset_empties_users_returns_invite_url(
        self,
        async_db_session,
        db_session,
        monkeypatch,
        temp_db,
    ):
        """include_accounts=true wipes everything and returns a usable bootstrap_invite_url."""
        from sqlalchemy import func  # noqa: PLC0415
        from sqlalchemy import select as sa_select

        client, user, profile = self._make_admin_client(
            async_db_session, db_session, monkeypatch
        )

        with _patch_target(temp_db):
            response = client.post("/api/v1/db/reset", json={"include_accounts": True})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data.get("bootstrap_invite_url") is not None
        invite_url: str = data["bootstrap_invite_url"]
        # The URL must contain a token after the # fragment separator.
        assert "#" in invite_url, (
            f"bootstrap_invite_url must contain a '#' fragment: {invite_url!r}"
        )
        token = invite_url.split("#", 1)[1]
        assert len(token) >= 20, f"Token looks too short: {token!r}"

        # Expunge stale objects (full reset deleted the seeded user/profile rows)
        # so that the next db_session queries don't trigger a spurious autoflush.
        db_session.expunge_all()

        # One new invite row should exist (for the admin re-registration).
        invite_count = db_session.execute(
            sa_select(func.count()).select_from(models.Invite)
        ).scalar()
        assert invite_count == 1, (
            "Exactly one bootstrap admin invite should exist after full reset"
        )

    def test_data_only_reset_with_zero_data_succeeds(
        self, async_db_session, db_session, monkeypatch, temp_db
    ):
        """Data-only reset on a database with no sleep data succeeds (no-op for data tables)."""
        client, _, _ = self._make_admin_client(
            async_db_session, db_session, monkeypatch
        )

        with _patch_target(temp_db):
            response = client.post("/api/v1/db/reset", json={"include_accounts": False})

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_data_only_reset_purges_raw_dirs(
        self,
        async_db_session,
        db_session,
        test_device,
        monkeypatch,
        temp_db,
        tmp_path,
    ):
        """Data-only reset quarantine-renames then purges each profile's raw/ dir."""
        client, _, profile = self._make_admin_client(
            async_db_session, db_session, monkeypatch
        )

        # Create a fake raw/<profile_id>/ directory.
        raw_root = tmp_path / "raw"
        profile_dir = raw_root / str(profile.id)
        profile_dir.mkdir(parents=True)
        (profile_dir / "data.edf").write_text("fake")

        with (
            _patch_target(temp_db),
            patch("snore.api.routers.db._raw_root", return_value=raw_root),
        ):
            response = client.post("/api/v1/db/reset", json={"include_accounts": False})

        assert response.status_code == 200
        # The raw dir should no longer exist (purged via quarantine).
        assert not profile_dir.exists(), "Profile raw dir must be purged after reset"


# ---------------------------------------------------------------------------
# Concurrency guards
# ---------------------------------------------------------------------------


class TestDbResetConcurrency:
    def test_409_when_lock_held(self, api_client, monkeypatch, temp_db):
        """Reset returns 409 immediately when the reset lock is already held.

        Monkeypatching the module-level lock is safe here because
        require_reset_lock does a LOAD_GLOBAL lookup at call time, so it
        sees the replacement value on the next request.
        """
        import snore.api.deps as deps  # noqa: PLC0415

        mock_lock = MagicMock()
        mock_lock.locked = lambda: True
        monkeypatch.setattr(deps, "_reset_lock", mock_lock)

        with _patch_target(temp_db):
            response = api_client.post("/api/v1/db/reset")

        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"]

    def test_no_database_service_in_reset_db_signature(self):
        """reset_db must not take a DatabaseService parameter.

        A DatabaseService dependency constructs its own get_db session.
        Having that alongside ImmediateDbDep's BEGIN IMMEDIATE session means
        two concurrent sessions: the plain deferred session's first write
        blocks on the write lock held by the immediate session, producing
        OperationalError("database is locked") after busy_timeout=5000ms.
        This structural regression test guards against reintroducing that bug.
        """
        from snore.api.routers.db import reset_db  # noqa: PLC0415

        sig = inspect.signature(reset_db)
        for name, param in sig.parameters.items():
            annotation_str = str(param.annotation)
            assert "DatabaseService" not in annotation_str, (
                f"reset_db parameter '{name}' references DatabaseService — "
                "this would reintroduce the dual-session deadlock"
            )
