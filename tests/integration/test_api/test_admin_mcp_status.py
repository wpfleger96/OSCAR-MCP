"""Integration tests for GET /api/v1/admin/mcp/status.

Coverage
--------
TestMcpStatusAuth    – 401 unauthenticated, 403 non-admin
TestMcpStatusShape   – disabled reasons per config state; enabled shape;
                       linked-google-identities count

The endpoint is read-only: it reflects the global AppConfig plus one count
query, so the enabled/disabled states are driven via ``set_config()`` after
the client is built (the route reads ``get_config()`` per request).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from snore.api.config import AppConfig, parse_origin, set_config
from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client

_PUBLIC_BASE_URL = "https://snore.example.com"
_MCP_BASE_URL = "https://snore.example.com"
_DUMMY_PROFILE_ID = 999  # admin routes never read profile_id


# ---------------------------------------------------------------------------
# Helpers
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


def _seed_user(db_session: Session, *, role: str = "member") -> models.User:
    user = models.User(
        canonical_email=f"{role}_{uuid.uuid4().hex[:8]}@test.local", role=role
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seed_identity(
    db_session: Session, user_id: int, *, provider: str = "google"
) -> None:
    db_session.add(
        models.AuthIdentity(
            user_id=user_id, provider=provider, subject=uuid.uuid4().hex
        )
    )
    db_session.flush()


def _multiuser_config(
    *, mcp_base_url: str = "", google_configured: bool = False
) -> AppConfig:
    """A fully-populated multiuser AppConfig for driving the status endpoint."""
    return AppConfig(
        auth_mode=AuthMode.MULTIUSER,
        session_secret="test-secret-at-least-32-chars-long-abcdef",
        public_base_url=_PUBLIC_BASE_URL,
        public_origin=parse_origin(_PUBLIC_BASE_URL),
        bind_host="127.0.0.1",
        trusted_proxies=frozenset(),
        dev_origins=frozenset(),
        cors_origins=["http://localhost:5173"],
        google_client_id="dummy-client-id" if google_configured else "",
        google_client_secret="dummy-client-secret" if google_configured else "",
        oauth_attempt_ttl_seconds=600,
        pre_auth_cookie_ttl_seconds=600,
        max_upload_bytes=512 * 1024 * 1024,
        max_file_bytes=256 * 1024 * 1024,
        max_upload_files=10000,
        max_jobs_per_user=3,
        max_jobs_global=10,
        analysis_max_workers=4,
        mcp_base_url=mcp_base_url,
    )


# ---------------------------------------------------------------------------
# TestMcpStatusAuth
# ---------------------------------------------------------------------------


class TestMcpStatusAuth:
    def test_unauthenticated_gets_401(self, async_db_session, db_session):
        client = _make_client(async_db_session, unauthenticated=True)
        resp = client.get("/api/v1/admin/mcp/status")
        assert resp.status_code == 401

    def test_member_gets_403(self, async_db_session, db_session):
        member = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, actor=_member_actor(member.id))
        resp = client.get("/api/v1/admin/mcp/status")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# TestMcpStatusShape
# ---------------------------------------------------------------------------


class TestMcpStatusShape:
    def test_default_env_reports_local_mode_disabled(
        self, async_db_session, db_session
    ):
        """Default test config is local mode → disabled with 'local mode' reason."""
        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))

        resp = client.get("/api/v1/admin/mcp/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "enabled": False,
            "endpoint_url": None,
            "transport": None,
            "auth_provider": None,
            "disabled_reason": "local mode",
            "linked_google_identities": 0,
        }

    def test_multiuser_without_base_url_reports_unset(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        set_config(_multiuser_config(google_configured=True))

        data = client.get("/api/v1/admin/mcp/status").json()

        assert data["enabled"] is False
        assert data["disabled_reason"] == "SNORE_MCP_BASE_URL not set"

    def test_multiuser_without_google_reports_unconfigured(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        set_config(_multiuser_config(mcp_base_url=_MCP_BASE_URL))

        data = client.get("/api/v1/admin/mcp/status").json()

        assert data["enabled"] is False
        assert data["disabled_reason"] == "Google OAuth not configured"

    def test_enabled_shape_and_identity_count(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        member_a = _seed_user(db_session)
        member_b = _seed_user(db_session)
        _seed_identity(db_session, member_a.id)
        _seed_identity(db_session, member_b.id)
        # Non-google identities must not count.
        _seed_identity(db_session, admin.id, provider="password")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        set_config(
            _multiuser_config(mcp_base_url=_MCP_BASE_URL, google_configured=True)
        )

        resp = client.get("/api/v1/admin/mcp/status")

        assert resp.status_code == 200
        assert resp.json() == {
            "enabled": True,
            "endpoint_url": f"{_MCP_BASE_URL}/mcp",
            "transport": "streamable-http",
            "auth_provider": "google",
            "disabled_reason": None,
            "linked_google_identities": 2,
        }
