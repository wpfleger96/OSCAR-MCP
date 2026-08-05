"""Integration tests for Google OAuth routes.

All tests that exercise the token-exchange path mock
``snore.api.routers.auth.fetch_google_id_token_claims`` so no real HTTP
calls are made to Google.

DB pattern: ``temp_db + init_database`` so the module-level SQLAlchemy
engine is set and the real middleware stack (AuthMiddleware,
AuthPathMiddleware, RateLimitMiddleware) runs on every request.
"""

from __future__ import annotations

import hashlib
import uuid

from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import pytest

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.app import create_app
from snore.api.config import load_config, set_config
from snore.api.deps import get_actor, get_db, get_raw_session
from snore.auth.actor import ActorContext, AuthMode
from snore.auth.factory import ActorContextFactory
from snore.database import models

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-at-least-32-chars-long-abcdef"
_BASE_URL = "http://127.0.0.1:8000"
_CLIENT_ID = "test-google-client-id.apps.googleusercontent.com"
_CLIENT_SECRET = "test-google-client-secret"


def _multiuser_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
    monkeypatch.setenv("SNORE_SESSION_SECRET", _SECRET)
    monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", _BASE_URL)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", _CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", _CLIENT_SECRET)


def _fake_claims(
    sub: str = "google-sub-123",
    email: str = "user@example.com",
    nonce: str = "testnonce",
) -> dict:
    return {
        "sub": sub,
        "email": email,
        "email_verified": True,
        "nonce": nonce,
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
    }


async def _make_oauth_attempt(
    db: AsyncSession,
    *,
    kind: str = "login",
    nonce: str = "testnonce",
    browser_session_hash: str,
    invite_id: int | None = None,
    expected_canonical_email: str | None = None,
    offset_seconds: int = 0,  # positive = future, negative = expired
) -> models.OauthAttempt:
    """Insert and return an oauth_attempt row for testing."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=600 + offset_seconds)
    attempt = models.OauthAttempt(
        state=uuid.uuid4().hex,
        kind=kind,
        nonce=nonce,
        pkce_verifier="test-verifier",
        browser_session_hash=browser_session_hash,
        expires_at=expires_at,
        invite_id=invite_id,
        expected_canonical_email=expected_canonical_email,
    )
    db.add(attempt)
    await db.flush()
    return attempt


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Test 1: Login redirect to Google
# ---------------------------------------------------------------------------


class TestGoogleLoginRedirect:
    @pytest.mark.asyncio
    async def test_google_login_redirects_to_google(self, temp_db, monkeypatch):
        """GET /auth/google/login → 302, Location contains accounts.google.com,
        sets snore_pre_auth cookie."""
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        from snore.database.session import cleanup_database, init_database

        await init_database(str(temp_db))

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get("/api/v1/auth/google/login")

        assert resp.status_code == 302, f"Expected 302, got {resp.status_code}"
        location = resp.headers.get("location", "")
        assert "accounts.google.com" in location, (
            f"Expected Location to contain accounts.google.com, got {location!r}"
        )
        assert "snore_pre_auth" in resp.cookies, (
            "Expected snore_pre_auth cookie to be set on first visit"
        )

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_google_login_reuses_existing_pre_auth_cookie(
        self, temp_db, monkeypatch
    ):
        """Second GET with an existing snore_pre_auth cookie → same hash stored,
        no new cookie set in response."""
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

        pre_auth_value = "existing-pre-auth-cookie-value-hex"
        expected_hash = _hash(pre_auth_value)

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/api/v1/auth/google/login",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 302
        # No new cookie should be issued when one already exists.
        assert "snore_pre_auth" not in resp.cookies, (
            "Should not rotate pre_auth cookie when one already exists"
        )

        # Verify the stored hash matches the provided cookie value.
        async with session_scope() as db:
            from sqlalchemy import select as sel

            attempt = (
                (
                    await db.execute(
                        sel(models.OauthAttempt)
                        .order_by(models.OauthAttempt.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        assert attempt is not None
        assert attempt.browser_session_hash == expected_hash, (
            f"Expected hash {expected_hash!r}, got {attempt.browser_session_hash!r}"
        )

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 3 & 4: Callback browser-binding failures
# ---------------------------------------------------------------------------


class TestGoogleCallbackBrowserBinding:
    @pytest.mark.asyncio
    async def test_google_callback_missing_pre_auth_cookie_no_session(
        self, temp_db, monkeypatch
    ):
        """Callback with valid state+code but NO snore_pre_auth cookie → 400,
        no session cookie set.

        Falsifiability: a User + AuthIdentity with the mock subject is inserted so
        that removing the browser-binding guard would produce 302 + snore_session
        instead of 400.
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

        pre_auth_value = uuid.uuid4().hex
        expected_hash = _hash(pre_auth_value)
        google_sub = "google-sub-123"
        nonce = "testnonce"

        async with session_scope() as db:
            attempt = await _make_oauth_attempt(
                db, nonce=nonce, browser_session_hash=expected_hash
            )
            state = attempt.state
            # User + identity: bypassing the cookie guard would find this → 302.
            user = models.User(
                canonical_email="user@example.com", role="member", session_version=0
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
                    subject=google_sub,
                    email="user@example.com",
                )
            )

        # Mock returns valid claims so cookie guard is the only thing blocking 302.
        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(sub=google_sub, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            # No cookie sent — cookie guard must block.
            resp = await client.get(
                f"/api/v1/auth/google/callback?state={state}&code=testcode"
            )

        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
        assert "snore_session" not in resp.cookies

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_google_callback_foreign_pre_auth_cookie_no_session(
        self, temp_db, monkeypatch
    ):
        """Callback with different (foreign) pre_auth value → failure, no session.

        Falsifiability: a User + AuthIdentity is inserted so that removing the
        browser-binding guard would produce 302 instead of 400.
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

        real_pre_auth = uuid.uuid4().hex
        wrong_pre_auth = uuid.uuid4().hex
        assert real_pre_auth != wrong_pre_auth
        google_sub = "google-sub-123"
        nonce = "testnonce"

        async with session_scope() as db:
            attempt = await _make_oauth_attempt(
                db, nonce=nonce, browser_session_hash=_hash(real_pre_auth)
            )
            state = attempt.state
            # User + identity so bypassing the cookie guard would return 302.
            user = models.User(
                canonical_email="user@example.com", role="member", session_version=0
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
                    subject=google_sub,
                    email="user@example.com",
                )
            )

        # Mock returns valid claims so hash mismatch is the only thing blocking 302.
        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(sub=google_sub, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            # Wrong cookie value — hash mismatch guard must block.
            resp = await client.get(
                f"/api/v1/auth/google/callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={wrong_pre_auth}"},
            )

        assert resp.status_code == 400
        assert "snore_session" not in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 5: Replay — consumed state
# ---------------------------------------------------------------------------


class TestGoogleCallbackReplay:
    @pytest.mark.asyncio
    async def test_google_callback_replay_no_session(self, temp_db, monkeypatch):
        """Reusing a state that is already consumed → failure, no session.

        Falsifiability: a User + AuthIdentity is inserted so that removing both
        the SELECT filter and the conditional-consume UPDATE guard would produce
        302 + snore_session instead of 400.
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

        pre_auth_value = uuid.uuid4().hex
        google_sub = "google-sub-123"
        nonce = "testnonce"

        async with session_scope() as db:
            attempt = await _make_oauth_attempt(
                db, nonce=nonce, browser_session_hash=_hash(pre_auth_value)
            )
            # Mark as already consumed — replay scenario.
            attempt.consumed_at = datetime.now(UTC)
            state = attempt.state
            # User + identity so removing both guards would return 302.
            user = models.User(
                canonical_email="user@example.com", role="member", session_version=0
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
                    subject=google_sub,
                    email="user@example.com",
                )
            )

        # Mock returns valid claims so the SELECT+consume guards are what block 302.
        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(sub=google_sub, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 400
        assert "snore_session" not in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 6: Unknown Google subject — no provisioning
# ---------------------------------------------------------------------------


class TestGoogleCallbackUnknownSubject:
    @pytest.mark.asyncio
    async def test_google_callback_unknown_subject_no_provisioning(
        self, temp_db, monkeypatch
    ):
        """Valid flow but no auth_identity exists for the Google sub →
        failure, no user created."""
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

        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            attempt = await _make_oauth_attempt(
                db, nonce=nonce, browser_session_hash=_hash(pre_auth_value)
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(sub="unknown-sub", nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code in (400, 403), (
            f"Expected 400 or 403 for unlinked subject, got {resp.status_code}"
        )
        assert "snore_session" not in resp.cookies

        # No new user should have been created.
        async with session_scope() as db:
            from sqlalchemy import func
            from sqlalchemy import select as sel

            count = (
                await db.execute(sel(func.count()).select_from(models.User))
            ).scalar_one()
        assert count == 0, f"Expected no users created, found {count}"

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 7: Email mismatch in invite callback
# ---------------------------------------------------------------------------


class TestGoogleInviteCallbackEmailMismatch:
    @pytest.mark.asyncio
    async def test_google_invite_callback_email_mismatch_no_session(
        self, temp_db, monkeypatch
    ):
        """Google email doesn't match invite email → failure, no user created."""
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

        invite_email = "invitee@example.com"
        google_email = "someone-else@example.com"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)
            await db.flush()
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(email=google_email, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
        assert "snore_session" not in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 8: Successful signup via invite
# ---------------------------------------------------------------------------


class TestGoogleSignupCreatesUser:
    @pytest.mark.asyncio
    async def test_google_signup_creates_user_and_profile(self, temp_db, monkeypatch):
        """Valid invite + matching Google email → user + profile + identity
        created, session cookie set, redirect to /dashboard."""
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

        invite_email = f"newuser_{uuid.uuid4().hex[:6]}@example.com"
        google_sub = f"gsub_{uuid.uuid4().hex[:8]}"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)
            await db.flush()
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(
                _fake_claims(sub=google_sub, email=invite_email, nonce=nonce)
            ),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 302, (
            f"Expected 302, got {resp.status_code}: {resp.text}"
        )
        assert resp.headers.get("location", "").endswith("/dashboard")
        assert "snore_session" in resp.cookies

        # Verify DB state.
        async with session_scope() as db:
            from sqlalchemy import select as sel

            user = (
                (
                    await db.execute(
                        sel(models.User).where(
                            models.User.canonical_email == invite_email.lower()
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert user is not None, "User was not created"

            profile = (
                (
                    await db.execute(
                        sel(models.Profile).where(models.Profile.user_id == user.id)
                    )
                )
                .scalars()
                .first()
            )
            assert profile is not None, "Profile was not created"

            identity = (
                (
                    await db.execute(
                        sel(models.AuthIdentity).where(
                            models.AuthIdentity.provider == "google",
                            models.AuthIdentity.subject == google_sub,
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert identity is not None, "AuthIdentity was not created"
            assert identity.user_id == user.id

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 9: Existing email account gets identity linked
# ---------------------------------------------------------------------------


class TestGoogleSignupLinksExistingEmail:
    @pytest.mark.asyncio
    async def test_google_signup_existing_email_links_identity(
        self, temp_db, monkeypatch
    ):
        """User with the same canonical email already exists (password account)
        → identity linked, no duplicate user created."""
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

        email = f"existing_{uuid.uuid4().hex[:6]}@example.com"
        google_sub = f"gsub_{uuid.uuid4().hex[:8]}"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        existing_user_id: int

        async with session_scope() as db:
            # Create pre-existing password-based account.
            user = models.User(
                canonical_email=email.lower(),
                password_hash="$argon2id$placeholder",
                role="member",
                session_version=0,
            )
            db.add(user)
            await db.flush()
            profile = models.Profile(user_id=user.id, name="Default")
            db.add(profile)
            await db.flush()
            user.default_profile_id = profile.id
            existing_user_id = user.id

            admin = models.User(
                canonical_email="admin@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)
            await db.flush()
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=email.lower(),
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(
                _fake_claims(sub=google_sub, email=email, nonce=nonce)
            ),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 302
        assert "snore_session" in resp.cookies

        # No duplicate user; identity linked to the existing user.
        async with session_scope() as db:
            from sqlalchemy import func
            from sqlalchemy import select as sel

            user_count = (
                await db.execute(
                    sel(func.count())
                    .select_from(models.User)
                    .where(models.User.canonical_email == email.lower())
                )
            ).scalar_one()
            assert user_count == 1, f"Expected 1 user, found {user_count}"

            identity = (
                (
                    await db.execute(
                        sel(models.AuthIdentity).where(
                            models.AuthIdentity.provider == "google",
                            models.AuthIdentity.subject == google_sub,
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert identity is not None
            assert identity.user_id == existing_user_id

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 10: Two concurrent attempts — consuming first doesn't invalidate second
# ---------------------------------------------------------------------------


class TestTwoConcurrentAttempts:
    @pytest.mark.asyncio
    async def test_two_attempts_same_pre_auth_session_both_independently_valid(
        self, temp_db, monkeypatch
    ):
        """Two oauth_attempts rows sharing one browser_session_hash.
        Consuming the first (simulated) leaves the second independently usable.
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

        pre_auth_value = uuid.uuid4().hex
        browser_hash = _hash(pre_auth_value)
        nonce1 = uuid.uuid4().hex
        nonce2 = uuid.uuid4().hex
        google_sub = f"gsub_{uuid.uuid4().hex[:8]}"

        async with session_scope() as db:
            # Create a user with a linked identity for callback to succeed.
            user = models.User(
                canonical_email="tab-user@example.com",
                role="member",
                session_version=0,
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
                    subject=google_sub,
                    email="tab-user@example.com",
                )
            )

            attempt1 = await _make_oauth_attempt(
                db, nonce=nonce1, browser_session_hash=browser_hash
            )
            attempt2 = await _make_oauth_attempt(
                db, nonce=nonce2, browser_session_hash=browser_hash
            )
            state2 = attempt2.state
            # Simulate first attempt already consumed (tab 1 completed).
            attempt1.consumed_at = datetime.now(UTC)

        # Attempt 2 (tab 2) should still succeed.
        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(
                _fake_claims(sub=google_sub, nonce=kw.get("expected_nonce", nonce2))
            ),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            # Using state2 (not consumed).
            resp = await client.get(
                f"/api/v1/auth/google/callback?state={state2}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 302, (
            f"Expected 302 for second attempt after first consumed; "
            f"got {resp.status_code}: {resp.text}"
        )
        assert "snore_session" in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 11: Disabled user
# ---------------------------------------------------------------------------


class TestGoogleCallbackDisabledUser:
    @pytest.mark.asyncio
    async def test_disabled_user_google_callback_rejected(self, temp_db, monkeypatch):
        """Linked identity exists but user.disabled_at is set → failure, no session."""
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

        google_sub = f"gsub_{uuid.uuid4().hex[:8]}"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            user = models.User(
                canonical_email="disabled@example.com",
                role="member",
                session_version=0,
                disabled_at=datetime.now(UTC),
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
                    subject=google_sub,
                    email="disabled@example.com",
                )
            )
            attempt = await _make_oauth_attempt(
                db, nonce=nonce, browser_session_hash=_hash(pre_auth_value)
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(sub=google_sub, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code in (400, 403), (
            f"Expected 400/403 for disabled user, got {resp.status_code}"
        )
        assert "snore_session" not in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 12: Google not configured → 503
# ---------------------------------------------------------------------------


class TestGoogleNotConfigured:
    def test_google_not_configured_returns_503_login(
        self, async_db_session, monkeypatch
    ):
        """Missing GOOGLE_CLIENT_ID → 503 from /auth/google/login."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv("SNORE_SESSION_SECRET", _SECRET)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", _BASE_URL)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        assert not cfg.is_google_configured

        app = create_app()

        async def _override_db():
            async with async_db_session.begin():
                yield async_db_session

        async def _override_raw_session():
            yield async_db_session

        async def _override_actor(
            db: Annotated[AsyncSession, Depends(get_db)],
        ) -> ActorContext:
            return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_raw_session] = _override_raw_session
        app.dependency_overrides[get_actor] = _override_actor

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/auth/google/login", follow_redirects=False)
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}"

    def test_google_not_configured_returns_503_callback(
        self, async_db_session, monkeypatch
    ):
        """Missing Google credentials → 503 from /auth/google/callback."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv("SNORE_SESSION_SECRET", _SECRET)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", _BASE_URL)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        app = create_app()

        async def _override_db():
            async with async_db_session.begin():
                yield async_db_session

        async def _override_raw_session():
            yield async_db_session

        async def _override_actor(
            db: Annotated[AsyncSession, Depends(get_db)],
        ) -> ActorContext:
            return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_raw_session] = _override_raw_session
        app.dependency_overrides[get_actor] = _override_actor

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get(
            "/api/v1/auth/google/callback?state=x&code=y", follow_redirects=False
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Test 13: Revoked invite during exchange commits nothing
# ---------------------------------------------------------------------------


class TestRevokedInviteDuringExchangeCommitsNothing:
    @pytest.mark.asyncio
    async def test_revoked_invite_during_exchange_commits_nothing(
        self, temp_db, monkeypatch
    ):
        """Invite revoked after Window 1 but before Window 2 → 400,
        zero new users, zero new identities, oauth_attempt unconsumed."""
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

        invite_email = f"revoke_{uuid.uuid4().hex[:6]}@example.com"
        google_sub = f"gsub_{uuid.uuid4().hex[:8]}"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        invite_id_holder: list[int] = []

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="admin",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)
            await db.flush()
            invite_id_holder.append(invite.id)
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        mock_called = False

        async def _mock_exchange_then_revoke(**kw):
            nonlocal mock_called
            mock_called = True
            # Revoke the invite inside the mock — after Window 1 passes but before
            # Window 2 runs.  This is the race the test is designed to protect against.
            async with session_scope() as mdb:
                inv = await mdb.get(models.Invite, invite_id_holder[0])
                assert inv is not None
                inv.revoked_at = datetime.now(UTC)
            return _fake_claims(sub=google_sub, email=invite_email, nonce=nonce)

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            _mock_exchange_then_revoke,
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert mock_called, (
            "fetch_google_id_token_claims was never called — "
            "Window 1 short-circuited; test did not reach the exchange"
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert "snore_session" not in resp.cookies

        # Zero new users, zero new identities, attempt unconsumed.
        async with session_scope() as db:
            from sqlalchemy import func
            from sqlalchemy import select as sel

            user_count = (
                await db.execute(
                    sel(func.count())
                    .select_from(models.User)
                    .where(models.User.canonical_email == invite_email.lower())
                )
            ).scalar_one()
            assert user_count == 0, f"Expected 0 users, found {user_count}"

            identity_count = (
                await db.execute(
                    sel(func.count())
                    .select_from(models.AuthIdentity)
                    .where(models.AuthIdentity.subject == google_sub)
                )
            ).scalar_one()
            assert identity_count == 0, f"Expected 0 identities, found {identity_count}"

            from sqlalchemy import select as sel2

            consumed = (
                (
                    await db.execute(
                        sel2(models.OauthAttempt).where(
                            models.OauthAttempt.state == state
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert consumed is not None, "oauth_attempt row must exist after rollback"
            assert consumed.consumed_at is None, (
                "oauth_attempt should not be consumed after rollback"
            )

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 14: Expired invite during exchange commits nothing
# ---------------------------------------------------------------------------


class TestExpiredInviteDuringExchangeCommitsNothing:
    @pytest.mark.asyncio
    async def test_expired_invite_during_exchange_commits_nothing(
        self, temp_db, monkeypatch
    ):
        """Invite that expires after Window 1 (during Google I/O) → 400,
        zero DB changes, attempt unconsumed."""
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

        invite_email = f"expire_{uuid.uuid4().hex[:6]}@example.com"
        google_sub = f"gsub_{uuid.uuid4().hex[:8]}"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        invite_id_holder: list[int] = []

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin2@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)
            await db.flush()
            invite_id_holder.append(invite.id)
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        mock_called = False

        async def _mock_exchange_then_expire(**kw):
            nonlocal mock_called
            mock_called = True
            # Expire the invite inside the mock — after Window 1 passes but before
            # Window 2 runs.  This is the race the test is designed to protect against.
            async with session_scope() as mdb:
                inv = await mdb.get(models.Invite, invite_id_holder[0])
                assert inv is not None
                inv.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            return _fake_claims(sub=google_sub, email=invite_email, nonce=nonce)

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            _mock_exchange_then_expire,
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert mock_called, (
            "fetch_google_id_token_claims was never called — "
            "Window 1 short-circuited; test did not reach the exchange"
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert "snore_session" not in resp.cookies

        async with session_scope() as db:
            from sqlalchemy import func
            from sqlalchemy import select as sel

            user_count = (
                await db.execute(
                    sel(func.count())
                    .select_from(models.User)
                    .where(models.User.canonical_email == invite_email.lower())
                )
            ).scalar_one()
            assert user_count == 0, f"Expected 0 users, found {user_count}"

            identity_count = (
                await db.execute(
                    sel(func.count())
                    .select_from(models.AuthIdentity)
                    .where(models.AuthIdentity.subject == google_sub)
                )
            ).scalar_one()
            assert identity_count == 0, f"Expected 0 identities, found {identity_count}"

            from sqlalchemy import select as sel2

            consumed = (
                (
                    await db.execute(
                        sel2(models.OauthAttempt).where(
                            models.OauthAttempt.state == state
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert consumed is not None, "oauth_attempt row must exist after rollback"
            assert consumed.consumed_at is None, (
                "oauth_attempt should not be consumed after rollback"
            )

        await cleanup_database()


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


async def _async_return(value: object) -> object:
    """Coroutine helper that immediately returns *value*."""
    return value


# ---------------------------------------------------------------------------
# Test 13: Revoked invite rejected during callback
# ---------------------------------------------------------------------------


class TestGoogleInviteCallbackRevokedInvite:
    @pytest.mark.asyncio
    async def test_google_invite_callback_revoked_invite_rejected(
        self, temp_db, monkeypatch
    ):
        """Valid attempt pointing to a revoked invite → 400, no user created."""
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

        invite_email = f"revoked_{uuid.uuid4().hex[:6]}@example.com"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                revoked_at=datetime.now(UTC),  # revoked
            )
            db.add(invite)
            await db.flush()
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(email=invite_email, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 400, (
            f"Expected 400 for revoked invite, got {resp.status_code}"
        )
        assert "snore_session" not in resp.cookies

        async with session_scope() as db:
            from sqlalchemy import func
            from sqlalchemy import select as sel

            count = (
                await db.execute(
                    sel(func.count())
                    .select_from(models.User)
                    .where(models.User.canonical_email == invite_email.lower())
                )
            ).scalar_one()
        assert count == 0, f"Expected no user created for revoked invite, found {count}"

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 14: Expired invite rejected during callback
# ---------------------------------------------------------------------------


class TestGoogleInviteCallbackExpiredInvite:
    @pytest.mark.asyncio
    async def test_google_invite_callback_expired_invite_rejected(
        self, temp_db, monkeypatch
    ):
        """Valid attempt pointing to an expired invite → 400, no user created."""
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

        invite_email = f"expired_{uuid.uuid4().hex[:6]}@example.com"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin2@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) - timedelta(hours=1),  # expired
            )
            db.add(invite)
            await db.flush()
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(_fake_claims(email=invite_email, nonce=nonce)),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 400, (
            f"Expected 400 for expired invite, got {resp.status_code}"
        )
        assert "snore_session" not in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 15: Admin role preserved via invite
# ---------------------------------------------------------------------------


class TestGoogleInviteCallbackAdminRole:
    @pytest.mark.asyncio
    async def test_google_invite_callback_admin_role_preserved(
        self, temp_db, monkeypatch
    ):
        """Invite with role=admin → created user has role=admin."""
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

        invite_email = f"admin_{uuid.uuid4().hex[:6]}@example.com"
        google_sub = f"gsub_admin_{uuid.uuid4().hex[:8]}"
        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            creator = models.User(
                canonical_email="creator@test.com", role="admin", session_version=0
            )
            db.add(creator)
            await db.flush()
            invite = models.Invite(
                email=invite_email,
                token_hash=hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest(),
                role="admin",  # admin invite
                created_by=creator.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)
            await db.flush()
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=invite.id,
                expected_canonical_email=invite_email.lower(),
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(
                _fake_claims(sub=google_sub, email=invite_email, nonce=nonce)
            ),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 302, (
            f"Expected 302, got {resp.status_code}: {resp.text}"
        )

        async with session_scope() as db:
            from sqlalchemy import select as sel

            user = (
                (
                    await db.execute(
                        sel(models.User).where(
                            models.User.canonical_email == invite_email.lower()
                        )
                    )
                )
                .scalars()
                .first()
            )
        assert user is not None, "User was not created"
        assert user.role == "admin", f"Expected role=admin, got {user.role!r}"

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 16: Null invite_id on signup attempt rejected
# ---------------------------------------------------------------------------


class TestGoogleInviteCallbackNullInviteId:
    @pytest.mark.asyncio
    async def test_google_invite_callback_null_invite_id_rejected(
        self, temp_db, monkeypatch
    ):
        """Signup attempt with invite_id=None → 400, no user created."""
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

        pre_auth_value = uuid.uuid4().hex
        nonce = uuid.uuid4().hex

        async with session_scope() as db:
            attempt = await _make_oauth_attempt(
                db,
                kind="signup",
                nonce=nonce,
                browser_session_hash=_hash(pre_auth_value),
                invite_id=None,  # deliberately null
                expected_canonical_email="nobody@example.com",
            )
            state = attempt.state

        monkeypatch.setattr(
            "snore.api.routers.auth.fetch_google_id_token_claims",
            lambda **kw: _async_return(
                _fake_claims(email="nobody@example.com", nonce=nonce)
            ),
        )

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                f"/api/v1/auth/google/invite-callback?state={state}&code=testcode",
                headers={"cookie": f"snore_pre_auth={pre_auth_value}"},
            )

        assert resp.status_code == 400, (
            f"Expected 400 for null invite_id, got {resp.status_code}"
        )
        assert "snore_session" not in resp.cookies

        await cleanup_database()


# ---------------------------------------------------------------------------
# Test 17: POST /auth/invites/google returns authorization_url
# ---------------------------------------------------------------------------


class TestPostInvitesGoogle:
    @pytest.mark.asyncio
    async def test_post_invites_google_returns_authorization_url(
        self, temp_db, monkeypatch
    ):
        """POST /auth/invites/google with valid invite token → 200, authorization_url."""
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

        import hashlib as _hashlib

        token = uuid.uuid4().hex
        token_hash = _hashlib.sha256(token.encode()).hexdigest()

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email="newuser@example.com",
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.add(invite)

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.post(
                "/api/v1/auth/invites/google",
                json={"token": token},
                headers={"origin": _BASE_URL},
            )

        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "authorization_url" in body, (
            f"authorization_url missing from response: {body}"
        )
        assert "accounts.google.com" in body["authorization_url"]

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_post_invites_google_expired_invite_returns_400(
        self, temp_db, monkeypatch
    ):
        """POST /auth/invites/google with expired invite token → 400."""
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

        import hashlib as _hashlib

        token = uuid.uuid4().hex
        token_hash = _hashlib.sha256(token.encode()).hexdigest()

        async with session_scope() as db:
            admin = models.User(
                canonical_email="admin2@test.com", role="admin", session_version=0
            )
            db.add(admin)
            await db.flush()
            invite = models.Invite(
                email="expired@example.com",
                token_hash=token_hash,
                role="member",
                created_by=admin.id,
                expires_at=datetime.now(UTC) - timedelta(hours=1),  # expired
            )
            db.add(invite)

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.post(
                "/api/v1/auth/invites/google",
                json={"token": token},
                headers={"origin": _BASE_URL},
            )

        assert resp.status_code == 400, (
            f"Expected 400 for expired invite, got {resp.status_code}"
        )

        await cleanup_database()

    @pytest.mark.asyncio
    async def test_post_invites_google_unknown_token_returns_400(
        self, temp_db, monkeypatch
    ):
        """POST /auth/invites/google with unknown token → 400."""
        _multiuser_env(monkeypatch)
        cfg = load_config(
            auth_mode_override="multiuser", bind_host_override="127.0.0.1"
        )
        set_config(cfg)

        from snore.database.session import cleanup_database, init_database

        await init_database(str(temp_db))

        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            follow_redirects=False,
        ) as client:
            resp = await client.post(
                "/api/v1/auth/invites/google",
                json={"token": uuid.uuid4().hex},
                headers={"origin": _BASE_URL},
            )

        assert resp.status_code == 400

        await cleanup_database()
