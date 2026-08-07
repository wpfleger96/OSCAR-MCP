"""Integration tests for the self-service account management router.

Endpoints under test:
    GET   /api/v1/auth/me
    PATCH /api/v1/auth/me/display-name
    POST  /api/v1/auth/me/password
    GET   /api/v1/auth/me/preferences
    PATCH /api/v1/auth/me/preferences

Most tests use a sync TestClient with the actor hard-wired via a dependency
override in local mode (no CSRF, no rate limiting, no cookie handling needed).
Tests that require the full multiuser middleware stack (unauthenticated 401,
session-version invalidation) run as async tests via httpx.AsyncClient.
"""

from __future__ import annotations

import http.cookies
import uuid

import httpx
import pytest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from snore.api.app import create_app
from snore.api.config import load_config, set_config
from snore.auth.actor import ActorContext, AuthMode, Role
from snore.auth.lockout import get_lockout_store
from snore.auth.passwords import hash_password, verify_password
from snore.auth.session_cookie import encode_session
from snore.database import models
from tests.helpers.api_client import make_test_client
from tests.integration.test_api.conftest import _multiuser_env  # noqa: PLC0415

# ---------------------------------------------------------------------------
# Seeding / client helpers
# ---------------------------------------------------------------------------


def _seed_user(
    db_session: Session,
    *,
    role: str = "member",
    password: str | None = None,
    display_name: str | None = None,
    preferences: dict | None = None,
) -> tuple[models.User, models.Profile]:
    """Insert a User + Profile via the sync AUTOCOMMIT session; return both."""
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    user = models.User(
        canonical_email=email,
        role=role,
        password_hash=hash_password(password) if password else None,
        display_name=display_name,
        preferences=preferences if preferences is not None else {},
        session_version=0,
    )
    db_session.add(user)
    db_session.flush()

    profile = models.Profile(user_id=user.id, name="Default")
    db_session.add(profile)
    db_session.flush()

    user.default_profile_id = profile.id
    db_session.flush()

    return user, profile


def _make_client(
    async_db_session: AsyncSession, user_id: int, profile_id: int, role: str
) -> TestClient:
    """Build a TestClient wired to a fixed ActorContext for a specific user."""
    actor = ActorContext(
        user_id=user_id,
        profile_id=profile_id,
        role=Role(role),
        mode=AuthMode.LOCAL,
    )
    return make_test_client(async_db_session, actor=actor)


# ---------------------------------------------------------------------------
# TestGetMe
# ---------------------------------------------------------------------------


class TestGetMe:
    def test_authenticated_member_gets_me(self, async_db_session, db_session):
        """Authenticated member receives all expected fields; has_password reflects stored hash."""
        user, profile = _seed_user(db_session, role="member", password="secret123")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == user.id
        assert data["email"] == user.canonical_email
        assert data["display_name"] is None
        assert data["role"] == "member"
        assert data["has_password"] is True

    def test_google_only_user_has_password_false(self, async_db_session, db_session):
        """Google-only account (password_hash=None) reports has_password: false."""
        user, profile = _seed_user(db_session, role="member", password=None)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        assert resp.json()["has_password"] is False

    def test_google_linked_false_when_no_identity(self, async_db_session, db_session):
        """User with no auth_identities row reports google_linked: false."""
        user, profile = _seed_user(db_session, role="member", password="pw")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        assert resp.json()["google_linked"] is False

    def test_google_linked_true_when_identity_exists(
        self, async_db_session, db_session
    ):
        """User with a google auth_identities row reports google_linked: true."""
        user, profile = _seed_user(db_session, role="member", password="pw")
        db_session.add(
            models.AuthIdentity(
                user_id=user.id,
                provider="google",
                subject="google-sub-test-123",
                email=user.canonical_email,
            )
        )
        db_session.flush()
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        assert resp.json()["google_linked"] is True

    def test_unauthenticated_gets_401(self, async_db_session, monkeypatch):
        """Request with no session cookie returns 401 in multiuser mode."""
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        # No actor override: AuthMiddleware runs and finds no session cookie → 401.
        client = make_test_client(async_db_session, no_actor_override=True)

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 401

    def test_demo_actor_can_read(self, async_db_session, db_session):
        """Demo role is allowed on GET /me (require_auth, not require_writable)."""
        user, profile = _seed_user(db_session, role="demo")
        client = _make_client(async_db_session, user.id, profile.id, "demo")

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        assert resp.json()["role"] == "demo"


# ---------------------------------------------------------------------------
# TestDisplayName
# ---------------------------------------------------------------------------


class TestDisplayName:
    def test_update_display_name_succeeds_and_persists(
        self, async_db_session, db_session
    ):
        """PATCH /display-name updates the field; a subsequent GET /me reflects it."""
        user, profile = _seed_user(db_session, role="member", password="pw")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        patch_resp = client.patch(
            "/api/v1/auth/me/display-name",
            json={"display_name": "My Cool Name"},
        )
        assert patch_resp.status_code == 200

        get_resp = client.get("/api/v1/auth/me")
        assert get_resp.status_code == 200
        assert get_resp.json()["display_name"] == "My Cool Name"

    def test_empty_display_name_clears_to_none(self, async_db_session, db_session):
        """Whitespace-only display_name strips to None; explicit null also clears to None."""
        user, profile = _seed_user(db_session, role="member", display_name="Old Name")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        # Whitespace-only: "   ".strip() == "" → falsy → None.
        resp_ws = client.patch(
            "/api/v1/auth/me/display-name",
            json={"display_name": "   "},
        )
        assert resp_ws.status_code == 200
        assert client.get("/api/v1/auth/me").json()["display_name"] is None

        # Explicit null → None.
        resp_null = client.patch(
            "/api/v1/auth/me/display-name",
            json={"display_name": None},
        )
        assert resp_null.status_code == 200
        assert client.get("/api/v1/auth/me").json()["display_name"] is None

    def test_display_name_too_long_422(self, async_db_session, db_session):
        """display_name exceeding 150 characters is rejected with 422."""
        user, profile = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.patch(
            "/api/v1/auth/me/display-name",
            json={"display_name": "x" * 151},
        )
        assert resp.status_code == 422

    def test_demo_cannot_update_display_name(self, async_db_session, db_session):
        """Demo role hits 403 on PATCH /display-name (require_writable guard)."""
        user, profile = _seed_user(db_session, role="demo")
        client = _make_client(async_db_session, user.id, profile.id, "demo")

        resp = client.patch(
            "/api/v1/auth/me/display-name",
            json={"display_name": "Sneaky"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# TestChangePassword
# ---------------------------------------------------------------------------


class TestChangePassword:
    def test_change_password_happy_path(self, async_db_session, db_session):
        """Correct current_password → 200; new password validates against stored hash."""
        old_pw = "old-correct-password"
        new_pw = "new-password-456"
        user, profile = _seed_user(db_session, role="member", password=old_pw)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"current_password": old_pw, "new_password": new_pw},
        )
        assert resp.status_code == 200

        # Verify new hash is stored and old password is invalidated.
        db_session.refresh(user)
        ok_new, _ = verify_password(user.password_hash, new_pw)
        assert ok_new, "New password must validate against stored hash"
        ok_old, _ = verify_password(user.password_hash, old_pw)
        assert not ok_old, "Old password must be rejected after change"

    @pytest.mark.asyncio
    async def test_session_version_semantics(self, temp_db, monkeypatch):
        """After password change the old cookie is rejected (401); the re-issued cookie works (200)."""
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        pw = "test-password-123"
        async with session_scope() as db:
            user = models.User(
                canonical_email=f"sv_{uuid.uuid4().hex[:6]}@test",
                role="member",
                session_version=0,
                password_hash=hash_password(pw),
            )
            db.add(user)
            await db.flush()
            profile = models.Profile(user_id=user.id, name="Default")
            db.add(profile)
            await db.flush()
            user.default_profile_id = profile.id
            user_id = user.id
            profile_id = profile.id

        old_cookie = encode_session(
            cfg.session_secret,
            user_id=user_id,
            active_profile_id=profile_id,
            session_version=0,
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            # Change password with the old cookie (session_version=0).
            change_resp = await client.post(
                "/api/v1/auth/me/password",
                json={"current_password": pw, "new_password": "new-pw-789"},
                headers={
                    "origin": "http://127.0.0.1:8000",
                    "cookie": f"snore_session={old_cookie}",
                },
            )
            assert change_resp.status_code == 200, change_resp.text

            # Extract the re-issued cookie (version 1) from Set-Cookie.
            set_cookie_header = change_resp.headers.get("set-cookie", "")
            c = http.cookies.SimpleCookie()
            c.load(set_cookie_header)
            assert "snore_session" in c, (
                f"Password change must re-issue the session cookie; "
                f"Set-Cookie: {set_cookie_header!r}"
            )
            new_cookie = c["snore_session"].value

            # Old cookie (version 0) must be rejected after the version bump.
            old_resp = await client.get(
                "/api/v1/auth/me",
                headers={"cookie": f"snore_session={old_cookie}"},
            )
            assert old_resp.status_code == 401, (
                "Stale session cookie must be rejected after session_version increment"
            )

            # New cookie (version 1) must be accepted.
            new_resp = await client.get(
                "/api/v1/auth/me",
                headers={"cookie": f"snore_session={new_cookie}"},
            )
            assert new_resp.status_code == 200, (
                "Re-issued session cookie must grant access"
            )

        await cleanup_database()

    def test_wrong_current_password_401(self, async_db_session, db_session):
        """Wrong current_password → 401 Authentication failed."""
        user, profile = _seed_user(db_session, role="member", password="correct-pw")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "wrong-pw", "new_password": "new-pw-123"},
        )
        assert resp.status_code == 401

    def test_missing_current_password_422(self, async_db_session, db_session):
        """Omitting current_password when the user has one set returns 422."""
        user, profile = _seed_user(db_session, role="member", password="exists")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"new_password": "new-pw-456"},
        )
        assert resp.status_code == 422

    def test_google_only_sets_password(self, async_db_session, db_session):
        """Google-only account (no password) can set a password without current_password;
        has_password flips to true on the next GET /me."""
        user, profile = _seed_user(db_session, role="member", password=None)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"new_password": "brand-new-password"},
        )
        assert resp.status_code == 200

        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["has_password"] is True

    def test_google_only_with_current_rejects_422(self, async_db_session, db_session):
        """Providing current_password on a Google-only account (no password set) → 422."""
        user, profile = _seed_user(db_session, role="member", password=None)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "oops", "new_password": "new-pw"},
        )
        assert resp.status_code == 422

    def test_empty_new_password_422(self, async_db_session, db_session):
        """Empty new_password is rejected with 422 (validate_password_bytes: 0 bytes)."""
        user, profile = _seed_user(db_session, role="member", password=None)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"new_password": ""},
        )
        assert resp.status_code == 422

    def test_oversized_new_password_422(self, async_db_session, db_session):
        """new_password exceeding 1024 UTF-8 bytes is rejected with 422."""
        user, profile = _seed_user(db_session, role="member", password=None)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        # 1025 ASCII bytes exceeds the 1024-byte KDF ceiling.
        resp = client.post(
            "/api/v1/auth/me/password",
            json={"new_password": "a" * 1025},
        )
        assert resp.status_code == 422

    def test_demo_cannot_change_password(self, async_db_session, db_session):
        """Demo role hits 403 on POST /password (require_writable guard)."""
        user, profile = _seed_user(db_session, role="demo")
        client = _make_client(async_db_session, user.id, profile.id, "demo")

        resp = client.post(
            "/api/v1/auth/me/password",
            json={"new_password": "whatever"},
        )
        assert resp.status_code == 403

    def test_repeated_wrong_password_triggers_lockout(
        self, async_db_session, db_session
    ):
        """Repeated wrong current_password attempts are recorded in the lockout store;
        once locked, the endpoint returns 401 before checking the password."""
        user, profile = _seed_user(
            db_session, role="member", password="correct-password"
        )
        store = get_lockout_store()
        canonical = user.canonical_email
        # TestClient uses "testclient" as the client hostname; get_client_ip returns it.
        tc_ip = "testclient"

        try:
            # Pre-seed the lockout store to simulate repeated failures.
            for _ in range(15):
                store.record_failure(canonical, tc_ip)

            client = _make_client(async_db_session, user.id, profile.id, "member")
            resp = client.post(
                "/api/v1/auth/me/password",
                json={"current_password": "wrong-pw", "new_password": "new-pw-123"},
            )
            assert resp.status_code == 401
            assert "Authentication failed" in resp.json()["detail"]
        finally:
            # Always clear so this test does not pollute the shared store.
            store.record_success(canonical, tc_ip)

    def test_wrong_password_records_failure_in_lockout_store(
        self, async_db_session, db_session
    ):
        """A single wrong current_password call records a failure — the (email, ip)
        pair becomes locked immediately (BASE_LOCKOUT_SECONDS applies on first failure)."""
        user, profile = _seed_user(db_session, role="member", password="correct-pw")
        store = get_lockout_store()
        canonical = user.canonical_email
        tc_ip = "testclient"

        try:
            client = _make_client(async_db_session, user.id, profile.id, "member")
            resp = client.post(
                "/api/v1/auth/me/password",
                json={"current_password": "wrong-pw", "new_password": "new-pw-123"},
            )
            assert resp.status_code == 401
            # The handler must have called record_failure; one failure locks immediately.
            assert store.is_locked(canonical, tc_ip)
        finally:
            store.record_success(canonical, tc_ip)

    def test_successful_password_change_clears_lockout_state(
        self, async_db_session, db_session
    ):
        """A successful password change calls record_success, leaving no active lockout entry."""
        old_pw = "old-password-abc"
        user, profile = _seed_user(db_session, role="member", password=old_pw)
        store = get_lockout_store()
        canonical = user.canonical_email
        tc_ip = "testclient"

        try:
            client = _make_client(async_db_session, user.id, profile.id, "member")
            resp = client.post(
                "/api/v1/auth/me/password",
                json={"current_password": old_pw, "new_password": "new-pw-xyz"},
            )
            assert resp.status_code == 200

            # record_success must have been called — no active lockout entry.
            assert not store.is_locked(canonical, tc_ip)
        finally:
            store.record_success(canonical, tc_ip)


# ---------------------------------------------------------------------------
# TestPreferences
# ---------------------------------------------------------------------------


class TestPreferences:
    def test_get_preferences_returns_defaults(self, async_db_session, db_session):
        """Fresh user with no stored preferences receives default values for all fields."""
        user, profile = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.get("/api/v1/auth/me/preferences")

        assert resp.status_code == 200
        assert resp.json() == {"landing_page": "dashboard", "date_format": "iso"}

    def test_patch_preferences_merges_fields(self, async_db_session, db_session):
        """Patching one field leaves the other at its default; re-GET confirms the merge."""
        user, profile = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        patch_resp = client.patch(
            "/api/v1/auth/me/preferences",
            json={"landing_page": "sessions"},
        )
        assert patch_resp.status_code == 200

        get_resp = client.get("/api/v1/auth/me/preferences")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["landing_page"] == "sessions"
        assert data["date_format"] == "iso"  # default unchanged

    def test_patch_unknown_key_422(self, async_db_session, db_session):
        """Unknown field in PATCH body returns 422 (UserPreferencesUpdate extra='forbid')."""
        user, profile = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.patch(
            "/api/v1/auth/me/preferences",
            json={"unknown_key": "value"},
        )
        assert resp.status_code == 422

    def test_patch_invalid_enum_value_422(self, async_db_session, db_session):
        """Invalid enum value for a preferences field returns 422."""
        user, profile = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.patch(
            "/api/v1/auth/me/preferences",
            json={"landing_page": "not_a_valid_page"},
        )
        assert resp.status_code == 422

    def test_demo_get_preferences_200(self, async_db_session, db_session):
        """Demo role can GET preferences (require_auth only, not require_writable)."""
        user, profile = _seed_user(db_session, role="demo")
        client = _make_client(async_db_session, user.id, profile.id, "demo")

        resp = client.get("/api/v1/auth/me/preferences")
        assert resp.status_code == 200

    def test_demo_patch_preferences_403(self, async_db_session, db_session):
        """Demo role hits 403 on PATCH /preferences (require_writable guard)."""
        user, profile = _seed_user(db_session, role="demo")
        client = _make_client(async_db_session, user.id, profile.id, "demo")

        resp = client.patch(
            "/api/v1/auth/me/preferences",
            json={"landing_page": "sessions"},
        )
        assert resp.status_code == 403

    def test_patch_all_null_body_is_noop_returns_current(
        self, async_db_session, db_session
    ):
        """Empty PATCH body returns 200 with the current (default) preferences unchanged."""
        user, profile = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.patch("/api/v1/auth/me/preferences", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"landing_page": "dashboard", "date_format": "iso"}


# ---------------------------------------------------------------------------
# TestUnlinkGoogle
# ---------------------------------------------------------------------------


def _seed_google_identity(
    db_session: Session,
    user_id: int,
    *,
    subject: str = "google-sub-unlink-test",
) -> models.AuthIdentity:
    """Add a google auth_identity row for the given user."""
    identity = models.AuthIdentity(
        user_id=user_id,
        provider="google",
        subject=subject,
        email=None,
    )
    db_session.add(identity)
    db_session.flush()
    return identity


class TestUnlinkGoogle:
    def test_unlink_google_happy_path(self, async_db_session, db_session):
        """User with password + Google identity: DELETE succeeds, identity deleted,
        session_version incremented."""
        user, profile = _seed_user(
            db_session, role="member", password="pw-before-unlink"
        )
        _seed_google_identity(db_session, user.id)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.delete("/api/v1/auth/me/identities/google")

        assert resp.status_code == 200
        assert resp.json()["message"] == "Google account unlinked"

        # Identity rows must be gone.
        db_session.expire_all()
        from sqlalchemy import func
        from sqlalchemy import select as sel

        identity_count = db_session.execute(
            sel(func.count())
            .select_from(models.AuthIdentity)
            .where(
                models.AuthIdentity.user_id == user.id,
                models.AuthIdentity.provider == "google",
            )
        ).scalar_one()
        assert identity_count == 0, "Google identity must be deleted"

        # session_version must have been bumped.
        db_session.refresh(user)
        assert user.session_version == 1, "session_version must be incremented"

    def test_unlink_google_409_no_password(self, async_db_session, db_session):
        """User without a password gets 409 (lockout prevention)."""
        user, profile = _seed_user(db_session, role="member", password=None)
        _seed_google_identity(db_session, user.id)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.delete("/api/v1/auth/me/identities/google")

        assert resp.status_code == 409
        assert "password" in resp.json()["detail"].lower()

    def test_unlink_google_404_no_identity(self, async_db_session, db_session):
        """User with password but no Google identity gets 404."""
        user, profile = _seed_user(db_session, role="member", password="some-pw")
        client = _make_client(async_db_session, user.id, profile.id, "member")

        resp = client.delete("/api/v1/auth/me/identities/google")

        assert resp.status_code == 404

    def test_demo_cannot_unlink(self, async_db_session, db_session):
        """Demo role hits 403 on DELETE /identities/google (require_writable guard)."""
        user, profile = _seed_user(db_session, role="demo", password="pw")
        _seed_google_identity(db_session, user.id)
        client = _make_client(async_db_session, user.id, profile.id, "demo")

        resp = client.delete("/api/v1/auth/me/identities/google")

        assert resp.status_code == 403

    def test_get_me_shows_not_linked_after_unlink(self, async_db_session, db_session):
        """GET /me after unlink returns google_linked: false."""
        user, profile = _seed_user(db_session, role="member", password="pw2")
        _seed_google_identity(db_session, user.id)
        client = _make_client(async_db_session, user.id, profile.id, "member")

        unlink_resp = client.delete("/api/v1/auth/me/identities/google")
        assert unlink_resp.status_code == 200

        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["google_linked"] is False

    @pytest.mark.asyncio
    async def test_unlink_clears_cookie_and_old_cookie_is_rejected(
        self, temp_db, monkeypatch
    ):
        """Full multiuser stack: after unlink the response clears the cookie;
        the old session cookie is subsequently rejected with 401."""
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        from snore.auth.passwords import hash_password
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        pw = "multiuser-unlink-test-pw"
        async with session_scope() as db:
            user = models.User(
                canonical_email=f"unlink_{uuid.uuid4().hex[:6]}@test",
                role="member",
                session_version=0,
                password_hash=hash_password(pw),
            )
            db.add(user)
            await db.flush()
            profile = models.Profile(user_id=user.id, name="Default")
            db.add(profile)
            await db.flush()
            user.default_profile_id = profile.id
            db.add(
                models.AuthIdentity(
                    user_id=user.id,
                    provider="google",
                    subject=f"gsub_{uuid.uuid4().hex[:8]}",
                    email=None,
                )
            )
            user_id = user.id
            profile_id = profile.id

        old_cookie = encode_session(
            cfg.session_secret,
            user_id=user_id,
            active_profile_id=profile_id,
            session_version=0,
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            # Unlink Google with the old cookie.
            unlink_resp = await client.delete(
                "/api/v1/auth/me/identities/google",
                headers={
                    "origin": "http://127.0.0.1:8000",
                    "cookie": f"snore_session={old_cookie}",
                },
            )
            assert unlink_resp.status_code == 200, unlink_resp.text

            # Set-Cookie header must clear the session cookie (max-age=0).
            set_cookie_header = unlink_resp.headers.get("set-cookie", "")
            c = http.cookies.SimpleCookie()
            c.load(set_cookie_header)
            assert "snore_session" in c, (
                f"Unlink must issue a clear-cookie Set-Cookie; got: {set_cookie_header!r}"
            )
            assert c["snore_session"]["max-age"] == "0", (
                "Clear-cookie must set Max-Age=0"
            )

            # Old cookie (session_version=0) must now be rejected.
            old_resp = await client.get(
                "/api/v1/auth/me",
                headers={"cookie": f"snore_session={old_cookie}"},
            )
            assert old_resp.status_code == 401, (
                "Stale session cookie must be rejected after session_version increment"
            )

        await cleanup_database()
