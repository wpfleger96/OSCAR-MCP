"""Integration tests for the MCP Google-binding admin endpoints.

Coverage
--------
TestGoogleBindingsAuthGuards  – 401/403 on all three endpoints
TestListGoogleBindings        – GET  /api/v1/admin/mcp/google-bindings
TestResetGoogleBinding        – DELETE /api/v1/admin/mcp/google-bindings/{user_id}
TestResetAllGoogleBindings    – DELETE /api/v1/admin/mcp/google-bindings
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client

_DUMMY_PROFILE_ID = 999
_PW_HASH = "$argon2id$v=19$m=65536,t=3,p=4$placeholder$placeholder"


# ---------------------------------------------------------------------------
# Helpers – actor builders
# ---------------------------------------------------------------------------


def _admin_actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=_DUMMY_PROFILE_ID,
        role=Role.ADMIN,
        mode=AuthMode.LOCAL,
    )


def _member_actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=_DUMMY_PROFILE_ID,
        role=Role.MEMBER,
        mode=AuthMode.LOCAL,
    )


# ---------------------------------------------------------------------------
# Helpers – client factory
# ---------------------------------------------------------------------------


def _make_client(
    async_db_session: AsyncSession,
    *,
    actor: ActorContext | None = None,
    unauthenticated: bool = False,
) -> TestClient:
    return make_test_client(
        async_db_session, actor=actor, unauthenticated=unauthenticated
    )


# ---------------------------------------------------------------------------
# Helpers – data seeders
# ---------------------------------------------------------------------------


def _seed_user(
    db_session: Session,
    *,
    role: str = "member",
    password_hash: str | None = None,
) -> models.User:
    user = models.User(
        canonical_email=f"u_{uuid.uuid4().hex[:8]}@test.local",
        role=role,
        password_hash=password_hash,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seed_google_identity(
    db_session: Session,
    user_id: int,
    *,
    google_email: str | None = None,
) -> models.AuthIdentity:
    identity = models.AuthIdentity(
        user_id=user_id,
        provider="google",
        subject=uuid.uuid4().hex,
        email=google_email,
    )
    db_session.add(identity)
    db_session.flush()
    return identity


# ---------------------------------------------------------------------------
# TestGoogleBindingsAuthGuards
# ---------------------------------------------------------------------------


class TestGoogleBindingsAuthGuards:
    def test_list_unauthenticated_returns_401(
        self, async_db_session: AsyncSession
    ) -> None:
        client = _make_client(async_db_session, unauthenticated=True)
        assert client.get("/api/v1/admin/mcp/google-bindings").status_code == 401

    def test_list_member_returns_403(self, async_db_session: AsyncSession) -> None:
        client = _make_client(async_db_session, actor=_member_actor(user_id=1))
        assert client.get("/api/v1/admin/mcp/google-bindings").status_code == 403

    def test_reset_one_unauthenticated_returns_401(
        self, async_db_session: AsyncSession
    ) -> None:
        client = _make_client(async_db_session, unauthenticated=True)
        assert client.delete("/api/v1/admin/mcp/google-bindings/1").status_code == 401

    def test_reset_one_member_returns_403(self, async_db_session: AsyncSession) -> None:
        client = _make_client(async_db_session, actor=_member_actor(user_id=1))
        assert client.delete("/api/v1/admin/mcp/google-bindings/1").status_code == 403

    def test_reset_all_unauthenticated_returns_401(
        self, async_db_session: AsyncSession
    ) -> None:
        client = _make_client(async_db_session, unauthenticated=True)
        assert client.delete("/api/v1/admin/mcp/google-bindings").status_code == 401

    def test_reset_all_member_returns_403(self, async_db_session: AsyncSession) -> None:
        client = _make_client(async_db_session, actor=_member_actor(user_id=1))
        assert client.delete("/api/v1/admin/mcp/google-bindings").status_code == 403


# ---------------------------------------------------------------------------
# TestListGoogleBindings
# ---------------------------------------------------------------------------


class TestListGoogleBindings:
    def test_returns_seeded_bindings_with_correct_fields(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        member_with_pw = _seed_user(db_session, password_hash=_PW_HASH)
        member_no_pw = _seed_user(db_session)

        _seed_google_identity(
            db_session, member_with_pw.id, google_email="alice@gmail.com"
        )
        _seed_google_identity(db_session, member_no_pw.id, google_email="bob@gmail.com")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.get("/api/v1/admin/mcp/google-bindings")

        assert resp.status_code == 200
        items = resp.json()
        by_uid = {item["user_id"]: item for item in items}

        assert member_with_pw.id in by_uid
        pw_item = by_uid[member_with_pw.id]
        assert pw_item["user_email"] == member_with_pw.canonical_email
        assert pw_item["google_email"] == "alice@gmail.com"
        assert pw_item["has_password"] is True

        assert member_no_pw.id in by_uid
        no_pw_item = by_uid[member_no_pw.id]
        assert no_pw_item["user_email"] == member_no_pw.canonical_email
        assert no_pw_item["google_email"] == "bob@gmail.com"
        assert no_pw_item["has_password"] is False

    def test_empty_when_no_google_bindings(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        _seed_user(
            db_session, password_hash=_PW_HASH
        )  # password user, no google binding

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.get("/api/v1/admin/mcp/google-bindings")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# TestResetGoogleBinding
# ---------------------------------------------------------------------------


class TestResetGoogleBinding:
    def test_success_removes_binding_bumps_session_version(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        target = _seed_user(db_session, password_hash=_PW_HASH)
        _seed_google_identity(db_session, target.id)
        original_version = target.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{target.id}")

        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.session_version == original_version + 1
        assert updated.google_link_disabled is False

        remaining = (
            db_session.query(models.AuthIdentity)
            .filter_by(user_id=target.id, provider="google")
            .all()
        )
        assert remaining == []

    def test_success_leaves_other_users_bindings_untouched(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        target = _seed_user(db_session, password_hash=_PW_HASH)
        other = _seed_user(db_session, password_hash=_PW_HASH)
        _seed_google_identity(db_session, target.id)
        _seed_google_identity(db_session, other.id)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{target.id}")

        assert resp.status_code == 200

        db_session.expunge_all()
        other_bindings = (
            db_session.query(models.AuthIdentity)
            .filter_by(user_id=other.id, provider="google")
            .all()
        )
        assert len(other_bindings) == 1

    def test_no_binding_returns_404(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        target = _seed_user(db_session, password_hash=_PW_HASH)  # no google binding

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{target.id}")

        assert resp.status_code == 404

    def test_nonexistent_user_returns_404(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete("/api/v1/admin/mcp/google-bindings/99999")

        assert resp.status_code == 404

    def test_reset_removes_all_identities_for_user(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        target = _seed_user(db_session, password_hash=_PW_HASH)
        _seed_google_identity(db_session, target.id)
        _seed_google_identity(db_session, target.id)
        original_version = target.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{target.id}")

        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.session_version == original_version + 1
        remaining = (
            db_session.query(models.AuthIdentity)
            .filter_by(user_id=target.id, provider="google")
            .all()
        )
        assert remaining == []

    def test_reset_preserves_google_link_disabled_true(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        target = _seed_user(db_session, password_hash=_PW_HASH)
        target.google_link_disabled = True
        db_session.flush()
        _seed_google_identity(db_session, target.id)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{target.id}")

        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.google_link_disabled is True

    def test_admin_self_reset_invalidates_own_session(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        # Admin resets their OWN Google binding — allowed by design.
        # Stale-session 401 is not assertable here: the test client injects an
        # ActorContext directly and bypasses real JWT/session auth entirely.
        # We assert the two observable effects: identity rows gone and
        # session_version bumped by exactly 1.
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        _seed_google_identity(db_session, admin.id)
        original_version = admin.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{admin.id}")

        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, admin.id)
        assert updated.session_version == original_version + 1

        remaining = (
            db_session.query(models.AuthIdentity)
            .filter_by(user_id=admin.id, provider="google")
            .all()
        )
        assert remaining == []

    def test_passwordless_user_returns_409_binding_survives(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        target = _seed_user(db_session)  # no password
        _seed_google_identity(db_session, target.id)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/mcp/google-bindings/{target.id}")

        assert resp.status_code == 409

        # Binding must still be present
        db_session.expunge_all()
        binding = (
            db_session.query(models.AuthIdentity)
            .filter_by(user_id=target.id, provider="google")
            .first()
        )
        assert binding is not None


# ---------------------------------------------------------------------------
# TestResetAllGoogleBindings
# ---------------------------------------------------------------------------


class TestResetAllGoogleBindings:
    def test_mixed_population_correct_counts_and_survivors(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        """2 users with password+binding, 1 password-less with binding, 1 with password but no binding.

        Expected: reset=2, skipped=1; password-less binding survives; session_version
        bumped only for the 2 reset users.
        """
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)

        pw1 = _seed_user(db_session, password_hash=_PW_HASH)
        pw2 = _seed_user(db_session, password_hash=_PW_HASH)
        no_pw = _seed_user(db_session)  # password-less with binding
        pw_no_binding = _seed_user(db_session, password_hash=_PW_HASH)  # no binding

        _seed_google_identity(db_session, pw1.id)
        _seed_google_identity(db_session, pw2.id)
        _seed_google_identity(db_session, no_pw.id)
        # pw_no_binding intentionally has no google identity

        pw1_version = pw1.session_version
        pw2_version = pw2.session_version
        no_pw_version = no_pw.session_version
        pw_no_binding_version = pw_no_binding.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete("/api/v1/admin/mcp/google-bindings")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reset"] == 2
        assert body["skipped"] == 1

        db_session.expunge_all()

        # pw1 and pw2: bindings gone, session_version bumped
        for uid, orig in [(pw1.id, pw1_version), (pw2.id, pw2_version)]:
            u = db_session.get(models.User, uid)
            assert u.session_version == orig + 1
            remaining = (
                db_session.query(models.AuthIdentity)
                .filter_by(user_id=uid, provider="google")
                .all()
            )
            assert remaining == []

        # password-less: binding survives, session_version unchanged
        no_pw_u = db_session.get(models.User, no_pw.id)
        assert no_pw_u.session_version == no_pw_version
        surviving = (
            db_session.query(models.AuthIdentity)
            .filter_by(user_id=no_pw.id, provider="google")
            .first()
        )
        assert surviving is not None

        # pw_no_binding: session_version unchanged (no binding to reset)
        pw_nb_u = db_session.get(models.User, pw_no_binding.id)
        assert pw_nb_u.session_version == pw_no_binding_version

    def test_reset_all_counts_users_not_bindings(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        """One user with two Google identities plus one with one — reset == 2, not 3."""
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        multi = _seed_user(db_session, password_hash=_PW_HASH)
        single = _seed_user(db_session, password_hash=_PW_HASH)

        _seed_google_identity(db_session, multi.id)
        _seed_google_identity(db_session, multi.id)
        _seed_google_identity(db_session, single.id)

        multi_version = multi.session_version
        single_version = single.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete("/api/v1/admin/mcp/google-bindings")

        assert resp.status_code == 200
        assert resp.json()["reset"] == 2

        db_session.expunge_all()
        for uid, orig in [(multi.id, multi_version), (single.id, single_version)]:
            u = db_session.get(models.User, uid)
            assert u.session_version == orig + 1
            remaining = (
                db_session.query(models.AuthIdentity)
                .filter_by(user_id=uid, provider="google")
                .all()
            )
            assert remaining == []

    def test_all_passwordless_returns_zero_reset(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)
        u = _seed_user(db_session)
        _seed_google_identity(db_session, u.id)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete("/api/v1/admin/mcp/google-bindings")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reset"] == 0
        assert body["skipped"] == 1

    def test_no_bindings_returns_zero_counts(
        self, async_db_session: AsyncSession, db_session: Session
    ) -> None:
        admin = _seed_user(db_session, role="admin", password_hash=_PW_HASH)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete("/api/v1/admin/mcp/google-bindings")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reset"] == 0
        assert body["skipped"] == 0
