"""Unit tests for ActorContextFactory.make_from_cli multi-user ambiguity guard.

Verifies that the guard:
- fires when user_ref=None and ≥2 non-disabled users exist
- passes silently for 0 or 1 active users
- ignores disabled users in the count
- propagates through resolve_local_profile_id
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.actor import AuthMode, Role
from snore.auth.factory import ActorContextFactory, resolve_local_profile_id
from snore.database import models

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(
    session: AsyncSession, email: str | None = None, role: str = "admin"
) -> models.User:
    """Create a minimal non-disabled User and flush."""
    user = models.User(
        canonical_email=email or f"user_{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_profile(session: AsyncSession, user_id: int) -> models.Profile:
    """Create a Profile for user_id, set it as default, and flush."""
    profile = models.Profile(user_id=user_id, name="Default")
    session.add(profile)
    await session.flush()
    user = await session.get(models.User, user_id)
    user.default_profile_id = profile.id
    await session.flush()
    return profile


async def _seed_user(
    session: AsyncSession, email: str | None = None
) -> tuple[models.User, models.Profile]:
    """Create a User + Profile pair and return both."""
    user = await _make_user(session, email)
    profile = await _make_profile(session, user.id)
    return user, profile


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMakeFromCliAmbiguityGuard:
    """make_from_cli multi-user safety guard."""

    async def test_zero_users_auto_provisions(self, async_db_session):
        """Empty DB: make_from_cli auto-provisions user + profile."""
        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_from_cli(
            user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
        )
        assert actor.user_id is not None
        assert actor.profile_id is not None

    async def test_single_user_no_ref_succeeds(self, async_db_session):
        """Exactly one active user: make_from_cli resolves without a user_ref."""
        user, _ = await _seed_user(async_db_session, "alice@example.com")
        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_from_cli(
            user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
        )
        assert actor.user_id == user.id

    async def test_multiple_users_no_ref_raises(self, async_db_session):
        """Two active users and no user_ref → ValueError naming the count."""
        await _seed_user(async_db_session, "alice@example.com")
        await _seed_user(async_db_session, "bob@example.com")

        factory = ActorContextFactory(async_db_session)
        with pytest.raises(ValueError, match="Multiple users found"):
            await factory.make_from_cli(
                user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
            )

    async def test_multiple_users_with_ref_succeeds(self, async_db_session):
        """Two active users + explicit user_ref → resolves to the named user."""
        user_a, _ = await _seed_user(async_db_session, "alice@example.com")
        await _seed_user(async_db_session, "bob@example.com")

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_from_cli(
            user_ref="alice@example.com", profile_ref=None, mode=AuthMode.LOCAL
        )
        assert actor.user_id == user_a.id

    async def test_disabled_user_not_counted(self, async_db_session):
        """A disabled user does not trigger the ambiguity guard."""
        user_a, _ = await _seed_user(async_db_session, "alice@example.com")
        # Second user is disabled — must not count toward the guard.
        disabled = models.User(
            canonical_email="disabled@example.com",
            role="admin",
            disabled_at=datetime.now(UTC),
        )
        async_db_session.add(disabled)
        await async_db_session.flush()

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_from_cli(
            user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
        )
        assert actor.user_id == user_a.id

    async def test_error_message_includes_count(self, async_db_session):
        """ValueError message reports the actual user count."""
        for email in ("a@x.com", "b@x.com", "c@x.com"):
            await _seed_user(async_db_session, email)

        factory = ActorContextFactory(async_db_session)
        with pytest.raises(ValueError, match="3"):
            await factory.make_from_cli(
                user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
            )

    async def test_demo_user_not_counted_toward_cli_ambiguity(self, async_db_session):
        """Demo + admin + member = 3 non-disabled users, but only 2 non-demo → still ambiguous."""
        demo = await _make_user(async_db_session, "demo@snore.local", role="demo")
        await _make_profile(async_db_session, demo.id)
        admin, _ = await _seed_user(async_db_session, "admin@example.com")
        admin.role = "admin"
        await async_db_session.flush()
        member, _ = await _seed_user(async_db_session, "member@example.com")
        member.role = "member"
        await async_db_session.flush()

        factory = ActorContextFactory(async_db_session)
        with pytest.raises(ValueError, match="Multiple users found"):
            await factory.make_from_cli(
                user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
            )

    async def test_demo_plus_single_admin_resolves_admin(self, async_db_session):
        """Demo user + one admin: demo is excluded from the count, admin is resolved."""
        demo = await _make_user(async_db_session, "demo@snore.local", role="demo")
        await _make_profile(async_db_session, demo.id)
        admin, profile = await _seed_user(async_db_session, "admin@example.com")
        admin.role = "admin"
        await async_db_session.flush()

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_from_cli(
            user_ref=None, profile_ref=None, mode=AuthMode.LOCAL
        )

        assert actor.user_id == admin.id
        assert actor.role is Role.ADMIN
        assert actor.profile_id == profile.id


class TestResolveLocalProfileIdGuard:
    """resolve_local_profile_id propagates the multi-user guard."""

    async def test_single_user_returns_profile_id(self, async_db_session):
        """Single active user: returns a valid integer profile_id."""
        user, profile = await _seed_user(async_db_session, "solo@example.com")
        pid = await resolve_local_profile_id(async_db_session)
        assert pid == profile.id

    async def test_multiple_users_raises(self, async_db_session):
        """Multiple active users → ValueError (no silent default user)."""
        await _seed_user(async_db_session, "alice@example.com")
        await _seed_user(async_db_session, "bob@example.com")
        with pytest.raises(ValueError, match="Multiple users found"):
            await resolve_local_profile_id(async_db_session)


class TestMakeLocalAdminResolution:
    """make_local() must resolve the first live admin, never a demo/member user."""

    async def test_demo_at_lowest_id_skipped_admin_resolved(self, async_db_session):
        """Demo user seeded first (lowest id) is skipped; admin is returned."""
        # Seed demo user first so it gets the lower id (reproduces the observed bug
        # where multiuser mode seeds a demo user at id=1 before local mode runs).
        demo = await _make_user(async_db_session, "demo@snore.local", role="demo")
        await _make_profile(async_db_session, demo.id)

        admin = await _make_user(async_db_session, "admin@example.com", role="admin")
        await _make_profile(async_db_session, admin.id)

        assert demo.id < admin.id

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_local(mode=AuthMode.LOCAL)

        assert actor.user_id == admin.id
        assert actor.role is Role.ADMIN
        assert actor.can_write is True

    async def test_only_demo_users_provisions_admin(self, async_db_session):
        """Only demo users in DB → make_local auto-provisions a fresh admin."""
        demo = await _make_user(async_db_session, "demo@snore.local", role="demo")
        await _make_profile(async_db_session, demo.id)

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_local(mode=AuthMode.LOCAL)

        assert actor.user_id != demo.id
        assert actor.role is Role.ADMIN
        assert actor.can_write is True

    async def test_member_only_db_provisions_admin(self, async_db_session):
        """Only member-role users in DB → make_local auto-provisions a fresh admin."""
        member = await _make_user(async_db_session, "member@example.com", role="member")
        await _make_profile(async_db_session, member.id)

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_local(mode=AuthMode.LOCAL)

        assert actor.user_id != member.id
        assert actor.role is Role.ADMIN

    async def test_disabled_admin_skipped_live_admin_resolved(self, async_db_session):
        """Disabled admin is skipped; the second live admin is resolved."""
        disabled_admin = await _make_user(
            async_db_session, "disabled@example.com", role="admin"
        )
        disabled_admin.disabled_at = datetime.now(UTC)
        await async_db_session.flush()

        live_admin = await _make_user(
            async_db_session, "live@example.com", role="admin"
        )
        await _make_profile(async_db_session, live_admin.id)

        factory = ActorContextFactory(async_db_session)
        actor = await factory.make_local(mode=AuthMode.LOCAL)

        assert actor.user_id == live_admin.id
        assert actor.role is Role.ADMIN
