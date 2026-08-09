"""Integration tests for GET /api/v1/admin/mcp/status.

Coverage
--------
- 401 when unauthenticated
- 403 when non-admin (member role)
- 200 admin in local mode → enabled=False, disabled_reason="local mode"
- 200 admin in multiuser without Google creds → disabled_reason="Google OAuth not configured"
- 200 admin with is_mcp_enabled=True → full enabled shape using public_base_url
- linked_google_identities count reflects seeded auth_identities rows
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database.models import AuthIdentity, User
from tests.helpers.api_client import make_test_client

_DUMMY_PROFILE_ID = 999

_SESSION_SECRET = "test-secret-at-least-32-chars-long-zzzzzz"
_PUBLIC_BASE_URL = "http://127.0.0.1:8000"


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
# Auth guard tests
# ---------------------------------------------------------------------------


class TestMcpStatusAuthGuards:
    def test_unauthenticated_returns_401(self, async_db_session: AsyncSession) -> None:
        client = _make_client(async_db_session, unauthenticated=True)
        resp = client.get("/api/v1/admin/mcp/status")
        assert resp.status_code == 401

    def test_member_returns_403(self, async_db_session: AsyncSession) -> None:
        actor = _member_actor(user_id=1)
        client = _make_client(async_db_session, actor=actor)
        resp = client.get("/api/v1/admin/mcp/status")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Disabled shapes
# ---------------------------------------------------------------------------


class TestMcpStatusDisabled:
    def test_local_mode_returns_disabled_with_reason(
        self, async_db_session: AsyncSession
    ) -> None:
        actor = _admin_actor(user_id=1)
        client = _make_client(async_db_session, actor=actor)
        resp = client.get("/api/v1/admin/mcp/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["endpoint_url"] is None
        assert body["transport"] is None
        assert body["auth_provider"] is None
        assert body["disabled_reason"] == "local mode"
        assert isinstance(body["linked_google_identities"], int)

    def test_multiuser_no_google_returns_disabled(
        self,
        async_db_session: AsyncSession,
    ) -> None:
        import pytest  # noqa: PLC0415

        from snore.api.config import reset_config  # noqa: PLC0415

        mp = pytest.MonkeyPatch()
        mp.setenv("SNORE_AUTH_MODE", "multiuser")
        mp.setenv("SNORE_SESSION_SECRET", _SESSION_SECRET)
        mp.setenv("SNORE_PUBLIC_BASE_URL", _PUBLIC_BASE_URL)
        mp.delenv("GOOGLE_CLIENT_ID", raising=False)
        mp.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        reset_config()

        actor = _admin_actor(user_id=1)
        client = _make_client(async_db_session, actor=actor)
        resp = client.get("/api/v1/admin/mcp/status")
        mp.undo()
        reset_config()

        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["disabled_reason"] == "Google OAuth not configured"


# ---------------------------------------------------------------------------
# Enabled shape
# ---------------------------------------------------------------------------


class TestMcpStatusEnabled:
    def test_enabled_shape_when_fully_configured(
        self,
        async_db_session: AsyncSession,
    ) -> None:
        import pytest  # noqa: PLC0415

        from snore.api.config import reset_config  # noqa: PLC0415

        mp = pytest.MonkeyPatch()
        mp.setenv("SNORE_AUTH_MODE", "multiuser")
        mp.setenv("SNORE_SESSION_SECRET", _SESSION_SECRET)
        mp.setenv("SNORE_PUBLIC_BASE_URL", _PUBLIC_BASE_URL)
        mp.setenv("GOOGLE_CLIENT_ID", "client-id-dummy")
        mp.setenv("GOOGLE_CLIENT_SECRET", "client-secret-dummy")
        reset_config()

        actor = _admin_actor(user_id=1)
        client = _make_client(async_db_session, actor=actor)
        resp = client.get("/api/v1/admin/mcp/status")
        mp.undo()
        reset_config()

        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["endpoint_url"] == f"{_PUBLIC_BASE_URL}/mcp"
        assert body["transport"] == "streamable-http"
        assert body["auth_provider"] == "google"
        assert body["disabled_reason"] is None
        assert isinstance(body["linked_google_identities"], int)


# ---------------------------------------------------------------------------
# linked_google_identities count
# ---------------------------------------------------------------------------


class TestMcpStatusGoogleIdentityCount:
    def test_linked_google_identities_reflects_seeded_rows(
        self,
        db_session: object,
        async_db_session: AsyncSession,
    ) -> None:
        from sqlalchemy.orm import Session  # noqa: PLC0415

        db: Session = db_session
        user_a = User(
            canonical_email=f"ga_{uuid.uuid4().hex[:6]}@example.com", role="member"
        )
        user_b = User(
            canonical_email=f"gb_{uuid.uuid4().hex[:6]}@example.com", role="member"
        )
        db.add(user_a)
        db.add(user_b)
        db.flush()

        db.add(AuthIdentity(user_id=user_a.id, provider="google", subject="sub-ga-1"))
        db.add(AuthIdentity(user_id=user_b.id, provider="google", subject="sub-gb-1"))
        # A non-google identity — must not be counted
        db.add(AuthIdentity(user_id=user_a.id, provider="password", subject="pw-ga-1"))
        db.flush()

        actor = _admin_actor(user_id=1)
        client = _make_client(async_db_session, actor=actor)
        resp = client.get("/api/v1/admin/mcp/status")
        assert resp.status_code == 200
        assert resp.json()["linked_google_identities"] == 2
