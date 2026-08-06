"""Phase 2 plan-pinned integration tests for the auth router and middleware.

Tests (all named exactly as in the plan checklist):
- lockout
- session_version invalidation
- stale-profile cookie → default-profile fallback
- local-mode non-loopback startup refusal
- invite expiry / revoke / replay / double-redeem race
- CSRF origin check
- path-import absence in multiuser (incl. loopback proxy peer)
- dev-auth cookie over loopback HTTP (secure_cookie=False for loopback HTTP base URL)
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.app import create_app
from snore.api.config import load_config, set_config
from snore.api.deps import get_actor, get_db
from snore.auth.actor import ActorContext, AuthMode
from snore.auth.factory import ActorContextFactory
from snore.auth.lockout import LockoutStore, get_lockout_store
from snore.database import models

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _multiuser_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars required for a minimal multiuser config."""
    monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
    monkeypatch.setenv(
        "SNORE_SESSION_SECRET",
        "test-secret-at-least-32-chars-long-abcdef",
    )
    monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "http://127.0.0.1:8000")


def _make_multiuser_client(async_db_session: AsyncSession) -> TestClient:
    """Build a TestClient in multiuser mode with get_db/get_actor overridden."""
    app = create_app()

    async def override_get_db():
        async with async_db_session.begin():
            yield async_db_session

    async def override_get_actor(
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> ActorContext:
        return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_actor] = override_get_actor
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Config / startup tests
# ---------------------------------------------------------------------------


class TestLocalModeNonLoopbackStartupRefusal:
    def test_local_mode_refused_on_nonloopback_bind(self):
        """load_config raises ConfigError when local mode is combined with a
        non-loopback bind address (0.0.0.0)."""
        from snore.api.config import ConfigError

        with pytest.raises(ConfigError, match="not allowed on non-loopback bind"):
            load_config(
                auth_mode_override="local",
                bind_host_override="0.0.0.0",
            )

    def test_local_mode_refused_on_lan_ip(self):
        """LAN IP bind in local mode is rejected."""
        from snore.api.config import ConfigError

        with pytest.raises(ConfigError, match="not allowed on non-loopback bind"):
            load_config(
                auth_mode_override="local",
                bind_host_override="192.168.1.1",
            )

    def test_local_mode_allowed_on_loopback(self):
        """Local mode on 127.0.0.1 succeeds without error."""
        cfg = load_config(
            auth_mode_override="local",
            bind_host_override="127.0.0.1",
        )
        assert cfg.auth_mode is AuthMode.LOCAL

    def test_local_mode_allowed_on_localhost_hostname(self):
        """'localhost' is accepted as a loopback-safe hostname."""
        cfg = load_config(
            auth_mode_override="local",
            bind_host_override="localhost",
        )
        assert cfg.auth_mode is AuthMode.LOCAL


class TestDevAuthCookieOverLoopbackHTTP:
    def test_secure_cookie_false_for_loopback_http_base_url(self, monkeypatch):
        """When SNORE_PUBLIC_BASE_URL is a loopback HTTP URL, secure_cookie is False.

        This is the dev-auth scenario: ``just dev-auth`` runs on plain HTTP
        on 127.0.0.1; the Secure attribute must be off so the cookie actually
        reaches the browser.
        """
        _multiuser_env(monkeypatch)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "http://127.0.0.1:8000")
        cfg = load_config(
            auth_mode_override="multiuser",
            bind_host_override="127.0.0.1",
        )
        assert cfg.secure_cookie is False

    def test_secure_cookie_true_for_https_base_url(self, monkeypatch):
        """HTTPS public base URL forces Secure cookie."""
        _multiuser_env(monkeypatch)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")
        cfg = load_config(
            auth_mode_override="multiuser",
            bind_host_override="127.0.0.1",
        )
        assert cfg.secure_cookie is True


# ---------------------------------------------------------------------------
# Lockout tests
# ---------------------------------------------------------------------------


class TestLockout:
    """Lockout: repeated wrong-password attempts are rejected with generic 401."""

    def test_lockout_after_repeated_failures(self):
        """After enough failures the (email, ip) pair is locked."""
        store = LockoutStore()
        email = f"victim_{uuid.uuid4().hex[:6]}@example.com"
        ip = "1.2.3.4"

        for _ in range(10):
            store.record_failure(email, ip)

        assert store.is_locked(email, ip), "Should be locked after repeated failures"

    def test_lockout_generic_error_returned(self, async_db_session, monkeypatch):
        """The /login endpoint returns a generic 401 when the lockout fires —
        no information about which failure mode triggered it."""
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        store = get_lockout_store()
        email = f"locked_{uuid.uuid4().hex[:6]}@example.com"
        ip = "127.0.0.1"
        try:
            for _ in range(15):
                store.record_failure(email, ip)

            client = _make_multiuser_client(async_db_session)
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "whatever"},
                headers={"origin": "http://127.0.0.1:8000"},
            )
            assert resp.status_code == 401
            assert "Authentication failed" in resp.json()["detail"]
        finally:
            store.record_success(email, ip)

    def test_lockout_cleared_after_success(self):
        """A successful authentication clears the lockout state."""
        store = LockoutStore()
        email = f"user_{uuid.uuid4().hex[:6]}@example.com"
        ip = "10.0.0.1"

        for _ in range(10):
            store.record_failure(email, ip)
        assert store.is_locked(email, ip)

        store.record_success(email, ip)
        assert not store.is_locked(email, ip)

    def test_wrong_ip_not_locked(self):
        """Lockout is per (email, ip) — a different IP is unaffected."""
        store = LockoutStore()
        email = f"shared_{uuid.uuid4().hex[:6]}@example.com"
        ip_bad = "1.2.3.4"
        ip_good = "5.6.7.8"

        for _ in range(15):
            store.record_failure(email, ip_bad)

        assert store.is_locked(email, ip_bad)
        assert not store.is_locked(email, ip_good)


# ---------------------------------------------------------------------------
# Session version invalidation
# ---------------------------------------------------------------------------


class TestSessionVersionInvalidation:
    """session_version bump → old cookie is rejected by AuthMiddleware."""

    @pytest.mark.asyncio
    async def test_bumped_session_version_rejects_old_cookie(
        self, temp_db, monkeypatch
    ):
        """After session_version increments, sending the old cookie to
        GET /api/v1/auth/status must return authenticated=false.

        Falsifiability: reverting the session_version guard in middleware.py
        would cause this test to return authenticated=true instead.
        """
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

        async with session_scope() as db:
            user = models.User(
                canonical_email=f"sv_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(user)
            await db.flush()
            profile = models.Profile(user_id=user.id, name="Default")
            db.add(profile)
            await db.flush()
            user.default_profile_id = profile.id
            user_id = user.id
            profile_id = profile.id

        from snore.auth.session_cookie import encode_session

        old_cookie = encode_session(
            cfg.session_secret,
            user_id=user_id,
            active_profile_id=profile_id,
            session_version=0,
        )

        # Bump version in DB — old cookie now carries a stale session_version.
        async with session_scope() as db:
            u = await db.get(models.User, user_id)
            assert u is not None
            u.session_version = 1

        # Send the stale cookie through the real middleware stack.
        import httpx  # noqa: PLC0415

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            resp = await client.get(
                "/api/v1/auth/status",
                cookies={"snore_session": old_cookie},
            )

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False, (
            "AuthMiddleware must reject a cookie whose session_version no longer "
            "matches the DB — stale-cookie rejection is broken."
        )

        await cleanup_database()


# ---------------------------------------------------------------------------
# Stale-profile cookie tests
# ---------------------------------------------------------------------------


class TestStaleProfileCookie:
    """Stale/deleted/foreign active_profile_id in cookie → factory falls back."""

    @pytest.mark.asyncio
    async def test_tombstoned_profile_in_cookie_falls_back_to_default(
        self, temp_db, monkeypatch
    ):
        """ActorContextFactory falls back to default_profile_id when the cookie's
        active_profile_id refers to a tombstoned (deleting_at set) profile."""
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

        async with session_scope() as db:
            user = models.User(
                canonical_email=f"stale_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(user)
            await db.flush()
            p1 = models.Profile(user_id=user.id, name="Default")
            db.add(p1)
            await db.flush()
            p2 = models.Profile(user_id=user.id, name="Second")
            db.add(p2)
            await db.flush()
            user.default_profile_id = p1.id
            user_id = user.id
            p1_id = p1.id
            p2_id = p2.id

        async with session_scope() as db:
            p2_row = await db.get(models.Profile, p2_id)
            assert p2_row is not None
            p2_row.deleting_at = datetime.now(UTC)

        # make() with the tombstoned profile_id must fall back to p1.
        async with session_scope() as db:
            factory = ActorContextFactory(db)
            actor = await factory.make(
                user_id=user_id,
                active_profile_id=p2_id,
                mode=AuthMode.MULTIUSER,
            )

        assert actor is not None
        assert actor.profile_id == p1_id

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_foreign_profile_in_cookie_falls_back_to_default(
        self, temp_db, monkeypatch
    ):
        """Foreign profile_id (owned by another user) → fall back to default."""
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

        async with session_scope() as db:
            u1 = models.User(
                canonical_email=f"u1_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            u2 = models.User(
                canonical_email=f"u2_{uuid.uuid4().hex[:6]}@test",
                role="member",
                session_version=0,
            )
            db.add(u1)
            db.add(u2)
            await db.flush()
            p1 = models.Profile(user_id=u1.id, name="U1 Default")
            p2 = models.Profile(user_id=u2.id, name="U2 Profile")
            db.add(p1)
            db.add(p2)
            await db.flush()
            u1.default_profile_id = p1.id
            u2.default_profile_id = p2.id
            u1_id = u1.id
            p1_id = p1.id
            p2_id = p2.id

        async with session_scope() as db:
            factory = ActorContextFactory(db)
            actor = await factory.make(
                user_id=u1_id,
                active_profile_id=p2_id,  # foreign profile
                mode=AuthMode.MULTIUSER,
            )

        assert actor is not None
        assert actor.profile_id == p1_id  # fell back to u1's default

        await cleanup_database()


# ---------------------------------------------------------------------------
# CSRF origin check
# ---------------------------------------------------------------------------


class TestCSRFOriginCheck:
    """Origin allowlist: unsafe methods reject wrong/missing origin in multiuser."""

    @pytest.fixture(autouse=True)
    def _setup_multiuser_config(self, monkeypatch):
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

    def test_login_wrong_origin_rejected(self, async_db_session):
        """POST /auth/login with a wrong Origin → 403."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
            headers={"origin": "https://evil.example.com"},
        )
        assert resp.status_code == 403
        assert "Origin not allowed" in resp.json()["detail"]

    def test_login_no_origin_rejected(self, async_db_session):
        """POST /auth/login with no Origin/Referer → 403."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "pw"},
        )
        assert resp.status_code == 403

    def test_login_correct_origin_passes_csrf_check(self, async_db_session):
        """POST /auth/login with the correct configured origin passes the CSRF check
        (may still fail auth — but not with 403)."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "pw"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        # 401 means origin passed; 403 means CSRF blocked it.
        assert resp.status_code != 403

    def test_logout_wrong_origin_rejected(self, async_db_session):
        """POST /auth/logout with wrong Origin → 403."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"origin": "https://attacker.example.com"},
        )
        assert resp.status_code == 403

    def test_logout_correct_origin_succeeds(self, async_db_session):
        """POST /auth/logout with correct Origin → 200."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 200

    def test_redeem_invite_wrong_origin_rejected(self, async_db_session):
        """POST /auth/invites/redeem with wrong Origin → 403."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/auth/invites/redeem",
            json={"token": "sometoken", "password": "password123"},
            headers={"origin": "https://evil.example.com"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Path-import absence in multiuser
# ---------------------------------------------------------------------------


class TestPathImportAbsenceInMultiuser:
    """In multiuser mode, /import/detect and /import/path MUST NOT be reachable."""

    @pytest.fixture(autouse=True)
    def _setup_multiuser_config(self, monkeypatch):
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

    def test_import_detect_not_reachable_in_multiuser(self, async_db_session):
        """/import/detect is not registered in multiuser mode.

        The correct Origin header is supplied so CSRF passes; the only acceptable
        outcomes are 404 (route absent) or 405 (path present but method missing).
        A 403 would mean CSRF blocked the request before routing — that proves
        nothing about structural absence and masks a misconfigured route.
        """
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/import/detect",
            json={"path": "/some/path"},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code in (404, 405), (
            f"Expected 404/405 for structurally absent multiuser route, got {resp.status_code}"
        )

    def test_import_path_not_reachable_in_multiuser(self, async_db_session):
        """/import/path is not registered in multiuser mode."""
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/import/path",
            json={"sources": []},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code in (404, 405), (
            f"Expected 404/405 for structurally absent multiuser route, got {resp.status_code}"
        )

    def test_import_detect_reachable_in_local_mode(self, async_db_session, monkeypatch):
        """/import/detect IS registered in local mode."""
        from snore.api.config import load_config, set_config  # noqa

        cfg = load_config(auth_mode_override="local", bind_host_override="127.0.0.1")
        set_config(cfg)
        client = _make_multiuser_client(async_db_session)
        resp = client.post(
            "/api/v1/import/detect",
            json={"path": "/nonexistent/path"},
            headers={"host": "127.0.0.1"},
        )
        # Any non-404/405 proves the route is registered.
        assert resp.status_code not in (404,), (
            f"/import/detect should be registered in local mode; got {resp.status_code}"
        )

    def test_import_path_not_reachable_even_from_loopback_proxy_peer_in_multiuser(
        self, monkeypatch
    ):
        """Even a loopback-peer client gets 404/405 for /import/path in multiuser.

        In multiuser mode, the route is structurally absent — not just guarded
        by a loopback-peer check — so even a request that looks like it comes
        from a trusted proxy cannot reach it.
        """
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        from starlette.types import ASGIApp, Receive, Scope, Send

        app = create_app()

        class LoopbackMiddleware:
            def __init__(self, wrapped_app: ASGIApp) -> None:
                self.app = wrapped_app

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                if scope["type"] == "http":
                    scope["client"] = ("127.0.0.1", 12345)
                await self.app(scope, receive, send)

        wrapped = LoopbackMiddleware(app)
        client = TestClient(wrapped, raise_server_exceptions=True)
        resp = client.post(
            "/api/v1/import/path",
            json={"sources": []},
            headers={"origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code in (404, 405), (
            f"Expected 404/405 from loopback in multiuser; got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Invite lifecycle: expiry / revoke / replay / double-redeem race
# ---------------------------------------------------------------------------


class TestInviteLifecycle:
    """Invite endpoint rejects all invalid states with a generic non-200 response."""

    @pytest.fixture(autouse=True)
    def _setup_multiuser_config(self, monkeypatch):
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

    @pytest.mark.asyncio
    async def test_expired_invite_lookup_returns_invalid(self, temp_db):
        """GET /invites/{token} for an expired invite returns valid=False."""
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        token = uuid.uuid4().hex
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()

        async with session_scope() as db:
            admin = models.User(
                canonical_email=f"admin_exp_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(admin)
            await db.flush()
            inv = models.Invite(
                email="invitee@test.com",
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
            db.add(inv)
            await db.flush()

        # Build a fresh client against this DB.
        from sqlalchemy.ext.asyncio import (  # noqa
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        async_url = f"sqlite+aiosqlite:///{temp_db}"
        engine = create_async_engine(async_url)
        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as db:

            async def _override_db():
                async with db.begin():
                    yield db

            async def _override_actor(
                db2: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return await ActorContextFactory(db2).make_local(mode=AuthMode.LOCAL)

            app = create_app()
            app.dependency_overrides[get_db] = _override_db
            app.dependency_overrides[get_actor] = _override_actor
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/api/v1/auth/invites/lookup",
                json={"token": token},
                headers={"origin": "http://127.0.0.1:8000"},
            )

        await engine.dispose()

        assert resp.status_code == 200
        assert resp.json()["valid"] is False

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_revoked_invite_redeem_returns_404(self, temp_db):
        """POST /invites/{token}/redeem for a revoked invite → 404."""
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        token = uuid.uuid4().hex
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()

        async with session_scope() as db:
            admin = models.User(
                canonical_email=f"admin_rev_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(admin)
            await db.flush()
            inv = models.Invite(
                email="revoked@test.com",
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                revoked_at=datetime.now(UTC),
            )
            db.add(inv)
            await db.flush()

        from sqlalchemy.ext.asyncio import (  # noqa
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        async_url = f"sqlite+aiosqlite:///{temp_db}"
        engine = create_async_engine(async_url)
        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as db:

            async def _override_db():
                async with db.begin():
                    yield db

            async def _override_actor(
                db2: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return await ActorContextFactory(db2).make_local(mode=AuthMode.LOCAL)

            app = create_app()
            app.dependency_overrides[get_db] = _override_db
            app.dependency_overrides[get_actor] = _override_actor
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/api/v1/auth/invites/redeem",
                json={"token": token, "password": "securepassword"},
                headers={"origin": "http://127.0.0.1:8000"},
            )

        await engine.dispose()

        assert resp.status_code == 404
        assert "not found or expired" in resp.json()["detail"].lower()

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_already_redeemed_invite_replay_returns_404(self, temp_db):
        """Replaying a redeemed invite token → 404 (no information leak)."""
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        token = uuid.uuid4().hex
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()

        async with session_scope() as db:
            admin = models.User(
                canonical_email=f"admin_rep_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(admin)
            await db.flush()
            inv = models.Invite(
                email="redeemed@test.com",
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                redeemed_at=datetime.now(UTC),
            )
            db.add(inv)
            await db.flush()

        from sqlalchemy.ext.asyncio import (  # noqa
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        async_url = f"sqlite+aiosqlite:///{temp_db}"
        engine = create_async_engine(async_url)
        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as db:

            async def _override_db():
                async with db.begin():
                    yield db

            async def _override_actor(
                db2: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return await ActorContextFactory(db2).make_local(mode=AuthMode.LOCAL)

            app = create_app()
            app.dependency_overrides[get_db] = _override_db
            app.dependency_overrides[get_actor] = _override_actor
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/api/v1/auth/invites/redeem",
                json={"token": token, "password": "newpassword"},
                headers={"origin": "http://127.0.0.1:8000"},
            )

        await engine.dispose()

        assert resp.status_code == 404
        assert "not found or expired" in resp.json()["detail"].lower()

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_valid_invite_redeem_succeeds(self, temp_db):
        """A valid, unredeemed, unexpired invite is redeemed successfully."""
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        token = uuid.uuid4().hex
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
        invite_email = f"invitee_{uuid.uuid4().hex[:6]}@test.com"

        async with session_scope() as db:
            admin = models.User(
                canonical_email=f"admin_ok_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(admin)
            await db.flush()
            inv = models.Invite(
                email=invite_email,
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(inv)
            await db.flush()

        from sqlalchemy.ext.asyncio import (  # noqa
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        async_url = f"sqlite+aiosqlite:///{temp_db}"
        engine = create_async_engine(async_url)
        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as db:

            async def _override_db():
                async with db.begin():
                    yield db

            async def _override_actor(
                db2: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return await ActorContextFactory(db2).make_local(mode=AuthMode.LOCAL)

            app = create_app()
            app.dependency_overrides[get_db] = _override_db
            app.dependency_overrides[get_actor] = _override_actor
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/api/v1/auth/invites/redeem",
                json={"token": token, "password": "a-secure-password-123"},
                headers={"origin": "http://127.0.0.1:8000"},
            )

        await engine.dispose()

        assert resp.status_code == 200
        assert resp.json()["message"] == "Account created"

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_invite_double_redeem_race_only_one_wins(self, temp_db, monkeypatch):
        """Serial double-redemption attempt: exactly one succeeds.

        The IS NULL guard in the redeem route's UPDATE statement ensures that
        only one transaction can consume an invite.  We test this at the service
        layer directly (the guard lives in the UPDATE predicate, not in the HTTP
        layer) by calling redeem_invite_route's underlying logic twice against
        the same invite row and confirming that only the first attempt sets
        redeemed_at.
        """
        _multiuser_env(monkeypatch)

        from snore.auth.invite import InviteRedemptionError
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        token = uuid.uuid4().hex
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
        invite_email = f"race_{uuid.uuid4().hex[:6]}@test.com"

        async with session_scope() as db:
            admin = models.User(
                canonical_email=f"admin_race_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(admin)
            await db.flush()
            inv = models.Invite(
                email=invite_email,
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(inv)
            await db.flush()
            invite_id = inv.id

        from tests.helpers.invites import redeem_invite_once

        # First redemption: must succeed.
        await redeem_invite_once(invite_id)

        # Second redemption of the same invite_id: must raise.
        with pytest.raises(InviteRedemptionError, match="already been redeemed"):
            await redeem_invite_once(invite_id)

        # Exactly one redeemed_at timestamp.
        from sqlalchemy import select  # noqa

        async with session_scope() as db:
            refreshed = (
                (
                    await db.execute(
                        select(models.Invite).where(models.Invite.id == invite_id)
                    )
                )
                .scalars()
                .first()
            )
        assert refreshed is not None
        assert refreshed.redeemed_at is not None

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_valid_invite_lookup_returns_email_and_valid_true(self, temp_db):
        """POST /auth/invites/lookup with a valid unexpired invite returns
        valid=true and the invite email.

        Falsifiability: a bug that always returns valid=false would fail here.
        """
        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        await init_database(str(temp_db))

        token = uuid.uuid4().hex
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
        invite_email = f"happy_{uuid.uuid4().hex[:6]}@example.com"

        async with session_scope() as db:
            admin = models.User(
                canonical_email=f"admin_happy_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(admin)
            await db.flush()
            from datetime import timedelta  # noqa: PLC0415

            inv = models.Invite(
                email=invite_email,
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(inv)
            await db.flush()

        from sqlalchemy.ext.asyncio import (  # noqa
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        async_url = f"sqlite+aiosqlite:///{temp_db}"
        engine = create_async_engine(async_url)
        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as db:

            async def _override_db():
                async with db.begin():
                    yield db

            async def _override_actor(
                db2: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return await ActorContextFactory(db2).make_local(mode=AuthMode.LOCAL)

            app = create_app()
            app.dependency_overrides[get_db] = _override_db
            app.dependency_overrides[get_actor] = _override_actor
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/api/v1/auth/invites/lookup",
                json={"token": token},
                headers={"origin": "http://127.0.0.1:8000"},
            )

        await engine.dispose()

        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True, f"Expected valid=true, got: {data}"
        assert data["email"] == invite_email, (
            f"Expected email={invite_email!r}, got {data['email']!r}"
        )

        await cleanup_database()


# ---------------------------------------------------------------------------
# Active-profile switch integration test
# ---------------------------------------------------------------------------


class TestActiveProfileSwitch:
    """POST /auth/active-profile rewrites the session cookie with the new profile."""

    @pytest.mark.asyncio
    async def test_switch_profile_updates_cookie_active_profile_id(
        self, temp_db, monkeypatch
    ):
        """Switching to a second owned profile rewrites the session cookie.

        Asserts that the returned cookie carries the new active_profile_id
        while session_version is preserved.

        Falsifiability: a bug that doesn't update the cookie would fail here
        because decoded active_profile_id would still be profile1_id.
        """
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

        async with session_scope() as db:
            user = models.User(
                canonical_email=f"switch_{uuid.uuid4().hex[:6]}@test",
                role="admin",
                session_version=0,
            )
            db.add(user)
            await db.flush()
            profile1 = models.Profile(user_id=user.id, name="Default")
            db.add(profile1)
            await db.flush()
            profile2 = models.Profile(user_id=user.id, name="Second")
            db.add(profile2)
            await db.flush()
            user.default_profile_id = profile1.id
            user_id = user.id
            profile1_id = profile1.id
            profile2_id = profile2.id
            session_version = user.session_version

        from snore.auth.session_cookie import decode_session, encode_session

        # Encode a valid cookie for profile1.
        cookie_value = encode_session(
            cfg.session_secret,
            user_id=user_id,
            active_profile_id=profile1_id,
            session_version=session_version,
        )

        import http.cookies  # noqa: PLC0415

        import httpx  # noqa: PLC0415

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/active-profile",
                json={"profile_id": profile2_id},
                # Send cookie via header to avoid httpx per-request cookie deprecation.
                headers={
                    "origin": "http://127.0.0.1:8000",
                    "cookie": f"snore_session={cookie_value}",
                },
            )

        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        # Parse Set-Cookie header via http.cookies.SimpleCookie so Python's
        # octal-escaped cookie encoding is correctly unescaped before decode.
        set_cookie_header = resp.headers.get("set-cookie", "")
        c = http.cookies.SimpleCookie()
        c.load(set_cookie_header)
        assert "snore_session" in c, (
            f"Response must set snore_session cookie; Set-Cookie: {set_cookie_header!r}"
        )
        raw_token = c["snore_session"].value

        decoded = decode_session(cfg.session_secret, raw_token)
        assert decoded is not None, (
            f"New cookie must be decodeable with the configured secret; "
            f"raw_token={raw_token!r}"
        )
        _, active_profile_id, new_session_version = decoded
        assert active_profile_id == profile2_id, (
            f"Expected active_profile_id={profile2_id}, got {active_profile_id}"
        )
        assert new_session_version == session_version, (
            "session_version must be preserved across a profile switch"
        )

        await cleanup_database()
