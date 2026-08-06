"""Integration tests for the admin router (/api/v1/admin).

Coverage
--------
TestListUsers   – GET  /api/v1/admin/users
TestPatchUser   – PATCH /api/v1/admin/users/{id}
TestDisableEnable – POST /api/v1/admin/users/{id}/disable|enable
TestInvites     – POST/GET/DELETE /api/v1/admin/invites[/{id}]

All routes require admin role.  Tests exercise 401 (unauthenticated),
403 (member/demo), and the happy-path semantics described in admin.py.
"""

from __future__ import annotations

import hashlib
import uuid

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from snore.api.config import AppConfig, parse_origin, set_config
from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models
from tests.helpers.api_client import make_test_client

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_PUBLIC_BASE_URL = "https://snore.example.com"
_DUMMY_PROFILE_ID = 999  # admin routes never read profile_id


# ---------------------------------------------------------------------------
# Helpers – client factory
# ---------------------------------------------------------------------------


def _make_client(
    async_db_session: AsyncSession,
    *,
    actor: ActorContext | None = None,
    unauthenticated: bool = False,
) -> TestClient:
    """Return a TestClient wired to *async_db_session*.

    - Default: auto-provision local admin.
    - actor=<ActorContext>: return that actor for every request.
    - unauthenticated=True: raise 401, simulating an absent session cookie.
    """
    return make_test_client(
        async_db_session, actor=actor, unauthenticated=unauthenticated
    )


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


def _demo_actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        profile_id=_DUMMY_PROFILE_ID,
        role=Role.DEMO,
        mode=AuthMode.LOCAL,
    )


# ---------------------------------------------------------------------------
# Helpers – data seeders (AUTOCOMMIT sync session → immediately visible)
# ---------------------------------------------------------------------------


def _seed_user(
    db_session: Session,
    *,
    role: str = "member",
    disabled: bool = False,
    display_name: str | None = None,
) -> models.User:
    user = models.User(
        canonical_email=f"{role}_{uuid.uuid4().hex[:8]}@test.local",
        role=role,
        display_name=display_name,
    )
    if disabled:
        user.disabled_at = datetime.now(UTC)
    db_session.add(user)
    db_session.flush()
    return user


def _seed_invite(
    db_session: Session,
    *,
    created_by_id: int,
    role: str = "member",
    ttl_days: int = 7,
    redeemed: bool = False,
    revoked: bool = False,
    expired: bool = False,
) -> tuple[models.Invite, str]:
    """Seed an Invite and return (invite, raw_token)."""
    raw = uuid.uuid4().hex
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    now = datetime.now(UTC)
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(days=ttl_days)
    invite = models.Invite(
        email=f"invitee_{uuid.uuid4().hex[:6]}@test.local",
        token_hash=token_hash,
        role=role,
        created_by=created_by_id,
        expires_at=expires_at,
        redeemed_at=now if redeemed else None,
        revoked_at=now if revoked else None,
    )
    db_session.add(invite)
    db_session.flush()
    return invite, raw


# ---------------------------------------------------------------------------
# Helpers – config
# ---------------------------------------------------------------------------


def _set_config_with_public_url(base_url: str = _PUBLIC_BASE_URL) -> None:
    """Override the global config with a LOCAL-mode config that has a public base URL.

    Used by invite tests that assert the URL includes the configured base.
    The reset_auth_config autouse fixture (conftest.py) clears this after each test.
    """
    set_config(
        AppConfig(
            auth_mode=AuthMode.LOCAL,
            session_secret="",
            public_base_url=base_url,
            public_origin=parse_origin(base_url),
            bind_host="127.0.0.1",
            trusted_proxies=frozenset(),
            dev_origins=frozenset(),
            cors_origins=["http://localhost:5173"],
            google_client_id="",
            google_client_secret="",
            oauth_attempt_ttl_seconds=600,
            pre_auth_cookie_ttl_seconds=600,
            max_upload_bytes=512 * 1024 * 1024,
            max_file_bytes=256 * 1024 * 1024,
            max_upload_files=10000,
            max_jobs_per_user=3,
            max_jobs_global=10,
            analysis_max_workers=4,
        )
    )


# ---------------------------------------------------------------------------
# TestListUsers
# ---------------------------------------------------------------------------


class TestListUsers:
    def test_admin_sees_all_users_including_disabled(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        member = _seed_user(db_session, role="member")
        disabled_member = _seed_user(db_session, role="member", disabled=True)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.get("/api/v1/admin/users")

        assert resp.status_code == 200
        data = resp.json()
        ids_returned = {u["id"] for u in data}
        assert admin.id in ids_returned
        assert member.id in ids_returned
        assert disabled_member.id in ids_returned

        # Disabled user carries disabled=True
        disabled_items = [u for u in data if u["id"] == disabled_member.id]
        assert len(disabled_items) == 1
        assert disabled_items[0]["disabled"] is True

        # Active users carry disabled=False
        admin_items = [u for u in data if u["id"] == admin.id]
        assert admin_items[0]["disabled"] is False

    def test_member_gets_403(self, async_db_session, db_session):
        member = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, actor=_member_actor(member.id))
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 403

    def test_demo_gets_403(self, async_db_session, db_session):
        demo = _seed_user(db_session, role="demo")
        client = _make_client(async_db_session, actor=_demo_actor(demo.id))
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 403

    def test_unauthenticated_gets_401(self, async_db_session, db_session):
        client = _make_client(async_db_session, unauthenticated=True)
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TestPatchUser
# ---------------------------------------------------------------------------


class TestPatchUser:
    def test_admin_updates_display_name_persisted(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        target = _seed_user(db_session, role="member", display_name="Old Name")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={"display_name": "New Name"},
        )
        assert resp.status_code == 200

        # Verify persisted: clear identity map so next get() hits the DB
        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.display_name == "New Name"

    def test_role_change_member_to_admin_bumps_session_version(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        member = _seed_user(db_session, role="member")
        original_version = member.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{member.id}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, member.id)
        assert updated.role == "admin"
        assert updated.session_version == original_version + 1

    def test_demoting_last_active_admin_returns_409(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")  # sole active admin

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{admin.id}",
            json={"role": "member"},
        )
        assert resp.status_code == 409

    def test_admin_demoting_self_with_another_active_admin_returns_200(
        self, async_db_session, db_session
    ):
        admin1 = _seed_user(db_session, role="admin")
        _seed_user(db_session, role="admin")  # second active admin → guard passes

        client = _make_client(async_db_session, actor=_admin_actor(admin1.id))
        resp = client.patch(
            f"/api/v1/admin/users/{admin1.id}",
            json={"role": "member"},
        )
        assert resp.status_code == 200

    def test_empty_body_returns_422(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        target = _seed_user(db_session, role="member")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={},
        )
        assert resp.status_code == 422

    def test_unknown_user_id_returns_404(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            "/api/v1/admin/users/99999",
            json={"display_name": "Ghost"},
        )
        assert resp.status_code == 404

    def test_role_null_only_returns_422(self, async_db_session, db_session):
        """Sending {"role": null} alone (no other fields) must return 422."""
        admin = _seed_user(db_session, role="admin")
        target = _seed_user(db_session, role="member")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={"role": None},
        )
        assert resp.status_code == 422

    def test_same_role_assignment_is_noop_no_version_bump(
        self, async_db_session, db_session
    ):
        """Assigning the same role the user already has returns 200 without bumping session_version."""
        admin = _seed_user(db_session, role="admin")
        target = _seed_user(db_session, role="member")
        original_version = target.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={"role": "member"},
        )
        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.session_version == original_version

    def test_whitespace_display_name_clears_to_none(self, async_db_session, db_session):
        """A whitespace-only display_name strips to None in the persisted row."""
        admin = _seed_user(db_session, role="admin")
        target = _seed_user(db_session, role="member", display_name="Original Name")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            json={"display_name": "   "},
        )
        assert resp.status_code == 200

        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.display_name is None


# ---------------------------------------------------------------------------
# TestDisableEnable
# ---------------------------------------------------------------------------


class TestDisableEnable:
    def test_disable_sets_disabled_at_and_bumps_session_version(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        target = _seed_user(db_session, role="member")
        original_version = target.session_version

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(f"/api/v1/admin/users/{target.id}/disable")

        assert resp.status_code == 200
        db_session.expunge_all()
        updated = db_session.get(models.User, target.id)
        assert updated.disabled_at is not None
        assert updated.session_version == original_version + 1

    def test_self_disable_returns_409(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(f"/api/v1/admin/users/{admin.id}/disable")
        assert resp.status_code == 409

    def test_disable_already_disabled_returns_200_idempotent(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        already_disabled = _seed_user(db_session, role="member", disabled=True)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(f"/api/v1/admin/users/{already_disabled.id}/disable")
        assert resp.status_code == 200

    def test_enable_clears_disabled_at_returns_200(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        disabled = _seed_user(db_session, role="member", disabled=True)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(f"/api/v1/admin/users/{disabled.id}/enable")

        assert resp.status_code == 200
        db_session.expunge_all()
        updated = db_session.get(models.User, disabled.id)
        assert updated.disabled_at is None

    def test_enable_already_enabled_returns_200_idempotent(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        active = _seed_user(db_session, role="member")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(f"/api/v1/admin/users/{active.id}/enable")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TestInvites
# ---------------------------------------------------------------------------


class TestInvites:
    def test_create_returns_201_with_invite_url_and_no_store_header(
        self, async_db_session, db_session
    ):
        _set_config_with_public_url()

        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(
            "/api/v1/admin/invites",
            json={"email": "newuser@example.com", "role": "member", "ttl_days": 7},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "/invite#" in data["invite_url"]
        assert data["invite_url"].startswith(_PUBLIC_BASE_URL)
        assert resp.headers.get("cache-control") == "no-store"

    def test_created_invite_row_token_hash_and_created_by(
        self, async_db_session, db_session
    ):
        _set_config_with_public_url()

        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))

        before = datetime.now(UTC)
        resp = client.post(
            "/api/v1/admin/invites",
            json={"email": "verify@example.com", "role": "member", "ttl_days": 3},
        )
        after = datetime.now(UTC)

        assert resp.status_code == 201
        data = resp.json()

        # Raw token is the fragment after '#'
        raw_token = data["invite_url"].split("#", 1)[1]
        expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        invite_id = data["id"]
        db_session.expunge_all()
        invite_row = db_session.get(models.Invite, invite_id)
        assert invite_row.token_hash == expected_hash
        assert invite_row.created_by == admin.id

        # expires_at should be now + 3 days (±10 s)
        expires_at = invite_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        assert before + timedelta(days=3) - timedelta(seconds=10) <= expires_at
        assert expires_at <= after + timedelta(days=3) + timedelta(seconds=10)

    def test_create_with_demo_role_returns_422(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(
            "/api/v1/admin/invites",
            json={"email": "demo@example.com", "role": "demo"},
        )
        assert resp.status_code == 422

    def test_list_shows_only_pending_invites(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")

        redeemed_inv, _ = _seed_invite(
            db_session, created_by_id=admin.id, redeemed=True
        )
        revoked_inv, _ = _seed_invite(db_session, created_by_id=admin.id, revoked=True)
        expired_inv, _ = _seed_invite(db_session, created_by_id=admin.id, expired=True)
        pending_inv, _ = _seed_invite(db_session, created_by_id=admin.id)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.get("/api/v1/admin/invites")

        assert resp.status_code == 200
        data = resp.json()
        returned_ids = {inv["id"] for inv in data}
        assert pending_inv.id in returned_ids
        assert redeemed_inv.id not in returned_ids
        assert revoked_inv.id not in returned_ids
        assert expired_inv.id not in returned_ids

    def test_revoke_pending_invite_returns_200_and_sets_revoked_at(
        self, async_db_session, db_session
    ):
        admin = _seed_user(db_session, role="admin")
        invite, _ = _seed_invite(db_session, created_by_id=admin.id)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/invites/{invite.id}")

        assert resp.status_code == 200
        db_session.expunge_all()
        updated = db_session.get(models.Invite, invite.id)
        assert updated.revoked_at is not None

    def test_revoke_redeemed_invite_returns_409(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        invite, _ = _seed_invite(db_session, created_by_id=admin.id, redeemed=True)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/invites/{invite.id}")
        assert resp.status_code == 409

    def test_revoke_unknown_invite_id_returns_404(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete("/api/v1/admin/invites/99999")
        assert resp.status_code == 404

    def test_member_cannot_create_invite(self, async_db_session, db_session):
        member = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, actor=_member_actor(member.id))
        resp = client.post(
            "/api/v1/admin/invites",
            json={"email": "someone@example.com", "role": "member"},
        )
        assert resp.status_code == 403

    def test_member_cannot_list_invites(self, async_db_session, db_session):
        member = _seed_user(db_session, role="member")
        client = _make_client(async_db_session, actor=_member_actor(member.id))
        resp = client.get("/api/v1/admin/invites")
        assert resp.status_code == 403

    def test_member_cannot_revoke_invite(self, async_db_session, db_session):
        admin = _seed_user(db_session, role="admin")
        invite, _ = _seed_invite(db_session, created_by_id=admin.id)
        member = _seed_user(db_session, role="member")

        client = _make_client(async_db_session, actor=_member_actor(member.id))
        resp = client.delete(f"/api/v1/admin/invites/{invite.id}")
        assert resp.status_code == 403

    def test_revoke_expired_invite_returns_409(self, async_db_session, db_session):
        """Revoking an already-expired invite returns 409 (not pending)."""
        admin = _seed_user(db_session, role="admin")
        invite, _ = _seed_invite(db_session, created_by_id=admin.id, expired=True)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/invites/{invite.id}")
        assert resp.status_code == 409

    def test_revoke_already_revoked_returns_409(self, async_db_session, db_session):
        """Revoking an already-revoked invite returns 409 (not pending)."""
        admin = _seed_user(db_session, role="admin")
        invite, _ = _seed_invite(db_session, created_by_id=admin.id, revoked=True)

        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.delete(f"/api/v1/admin/invites/{invite.id}")
        assert resp.status_code == 409

    def test_create_invite_normalizes_email(self, async_db_session, db_session):
        """Email is strip+lowercased before storage; response email reflects normalized form."""
        _set_config_with_public_url()

        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))
        resp = client.post(
            "/api/v1/admin/invites",
            json={"email": "  Mixed@Case.COM  ", "role": "member"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "mixed@case.com"

        db_session.expunge_all()
        invite_row = db_session.get(models.Invite, data["id"])
        assert invite_row.email == "mixed@case.com"

    def test_ttl_days_out_of_bounds_returns_422(self, async_db_session, db_session):
        """ttl_days=0 and ttl_days=31 are both outside [1, 30] and must return 422."""
        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))

        for ttl in (0, 31):
            resp = client.post(
                "/api/v1/admin/invites",
                json={"email": "test@example.com", "role": "member", "ttl_days": ttl},
            )
            assert resp.status_code == 422, f"ttl_days={ttl} should return 422"

    def test_create_invite_blank_email_returns_422(self, async_db_session, db_session):
        """Spaces-only or no-at-sign email is rejected with 422."""
        admin = _seed_user(db_session, role="admin")
        client = _make_client(async_db_session, actor=_admin_actor(admin.id))

        for bad_email in ("   ", "no-at-sign"):
            resp = client.post(
                "/api/v1/admin/invites",
                json={"email": bad_email, "role": "member"},
            )
            assert resp.status_code == 422, f"email={bad_email!r} should return 422"
