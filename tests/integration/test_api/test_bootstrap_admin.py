"""Integration tests for _startup_ensure_bootstrap_admin.

Tests call the helper directly after initializing a real SQLite database so
that session_scope() works without mocking.  The reset_database_state autouse
fixture (tests/integration/conftest.py) calls cleanup_database() before and
after each test, giving each test a clean engine slot.
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from snore.api.config import AppConfig, parse_origin, set_config
from snore.auth.actor import AuthMode
from snore.database import models

_SESSION_SECRET = "test-secret-at-least-32-chars-long-abcdef"
_BASE_URL = "http://127.0.0.1:8000"
_BOOTSTRAP_EMAIL = "bootstrap-admin@example.com"


def _multiuser_cfg(bootstrap_email: str | None = _BOOTSTRAP_EMAIL) -> AppConfig:
    return AppConfig(
        auth_mode=AuthMode.MULTIUSER,
        session_secret=_SESSION_SECRET,
        public_base_url=_BASE_URL,
        public_origin=parse_origin(_BASE_URL),
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
        bootstrap_admin_email=bootstrap_email,
    )


async def _seed_user(
    db_url: str,
    *,
    role: str,
    disabled: bool = False,
    email: str | None = None,
) -> int:
    """Seed a user row and return its id."""
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as session:
        async with session.begin():
            canonical = (
                email
                if email is not None
                else f"{role}_{uuid.uuid4().hex[:8]}@test.local"
            )
            user = models.User(
                canonical_email=canonical,
                role=role,
            )
            if disabled:
                user.disabled_at = datetime.now(UTC)
            session.add(user)
            await session.flush()
            user_id = user.id
    await engine.dispose()
    return user_id


async def _seed_invite(
    db_url: str, *, email: str, role: str = "admin", expired: bool = False
) -> int:
    """Seed an Invite row and return its id."""
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from snore.auth.invite_tokens import hash_invite_token  # noqa: PLC0415

    raw = uuid.uuid4().hex
    token_hash = hash_invite_token(raw)
    now = datetime.now(UTC)
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(days=7)

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as session:
        async with session.begin():
            invite = models.Invite(
                email=email,
                token_hash=token_hash,
                role=role,
                created_by=None,
                expires_at=expires_at,
            )
            session.add(invite)
            await session.flush()
            invite_id = invite.id
    await engine.dispose()
    return invite_id


async def _count_invites(db_url: str, *, email: str) -> int:
    """Return the number of Invite rows for the given email."""
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(models.Invite).where(models.Invite.email == email)
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    return len(rows)


async def _get_invite(db_url: str, *, email: str) -> models.Invite | None:
    """Return the first Invite row for the given email, or None."""
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as session:
        invite = (
            (
                await session.execute(
                    select(models.Invite).where(models.Invite.email == email)
                )
            )
            .scalars()
            .first()
        )
    await engine.dispose()
    return invite


async def _get_invite_roles(db_url: str, *, email: str) -> list[str]:
    """Return the roles of all Invite rows for the given email."""
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as session:
        roles = list(
            (
                await session.execute(
                    select(models.Invite.role).where(models.Invite.email == email)
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    return roles


class TestStartupEnsureBootstrapAdmin:
    async def test_fresh_db_creates_admin_invite(self, temp_db):
        """Fresh multiuser DB + env set → one admin Invite row created."""
        from snore.api.app import _startup_ensure_bootstrap_admin  # noqa: PLC0415
        from snore.database.session import init_database_from_url  # noqa: PLC0415

        db_url = f"sqlite+aiosqlite:///{temp_db}"
        await init_database_from_url(db_url)
        set_config(_multiuser_cfg())

        await _startup_ensure_bootstrap_admin()

        invite = await _get_invite(db_url, email=_BOOTSTRAP_EMAIL)
        assert invite is not None
        assert invite.role == "admin"
        assert invite.email == _BOOTSTRAP_EMAIL
        assert invite.created_by is None
        assert invite.redeemed_at is None
        assert invite.revoked_at is None

    async def test_active_admin_exists_no_invite_created(self, temp_db):
        """Existing active admin → helper is a no-op; no invite row created."""
        from snore.api.app import _startup_ensure_bootstrap_admin  # noqa: PLC0415
        from snore.database.session import init_database_from_url  # noqa: PLC0415

        db_url = f"sqlite+aiosqlite:///{temp_db}"
        await init_database_from_url(db_url)
        await _seed_user(db_url, role="admin")
        set_config(_multiuser_cfg())

        await _startup_ensure_bootstrap_admin()

        assert await _count_invites(db_url, email=_BOOTSTRAP_EMAIL) == 0

    async def test_valid_pending_invite_no_duplicate(self, temp_db):
        """Valid pending invite already present → no second row created."""
        from snore.api.app import _startup_ensure_bootstrap_admin  # noqa: PLC0415
        from snore.database.session import init_database_from_url  # noqa: PLC0415

        db_url = f"sqlite+aiosqlite:///{temp_db}"
        await init_database_from_url(db_url)
        await _seed_invite(db_url, email=_BOOTSTRAP_EMAIL, role="admin")
        set_config(_multiuser_cfg())

        await _startup_ensure_bootstrap_admin()

        assert await _count_invites(db_url, email=_BOOTSTRAP_EMAIL) == 1

    async def test_no_bootstrap_email_set_is_noop(self, temp_db):
        """bootstrap_admin_email=None → helper returns without touching the DB."""
        from snore.api.app import _startup_ensure_bootstrap_admin  # noqa: PLC0415
        from snore.database.session import init_database_from_url  # noqa: PLC0415

        db_url = f"sqlite+aiosqlite:///{temp_db}"
        await init_database_from_url(db_url)
        set_config(_multiuser_cfg(bootstrap_email=None))

        await _startup_ensure_bootstrap_admin()

        assert await _count_invites(db_url, email=_BOOTSTRAP_EMAIL) == 0

    async def test_existing_user_at_bootstrap_email_blocks_invite(self, temp_db):
        """User row at bootstrap email (any role/state) → no invite created.

        The redemption route rejects addresses that already have an account, so
        minting an invite would create an unredeemable row on every restart after
        expiry.  The user-exists guard catches this and logs a recovery hint.
        """
        from snore.api.app import _startup_ensure_bootstrap_admin  # noqa: PLC0415
        from snore.database.session import init_database_from_url  # noqa: PLC0415

        db_url = f"sqlite+aiosqlite:///{temp_db}"
        await init_database_from_url(db_url)
        # Disabled admin whose email IS the bootstrap email — hits the user-exists guard.
        await _seed_user(db_url, role="admin", disabled=True, email=_BOOTSTRAP_EMAIL)
        set_config(_multiuser_cfg())

        await _startup_ensure_bootstrap_admin()

        assert await _count_invites(db_url, email=_BOOTSTRAP_EMAIL) == 0

    async def test_pending_member_invite_does_not_block(self, temp_db):
        """Pending MEMBER invite for bootstrap email does not block admin invite creation.

        Two valid invites for the same address may coexist; only a pending ADMIN
        invite prevents a duplicate.
        """
        from snore.api.app import _startup_ensure_bootstrap_admin  # noqa: PLC0415
        from snore.database.session import init_database_from_url  # noqa: PLC0415

        db_url = f"sqlite+aiosqlite:///{temp_db}"
        await init_database_from_url(db_url)
        await _seed_invite(db_url, email=_BOOTSTRAP_EMAIL, role="member")
        set_config(_multiuser_cfg())

        await _startup_ensure_bootstrap_admin()

        # Both the seeded member invite and the new admin invite must exist.
        assert await _count_invites(db_url, email=_BOOTSTRAP_EMAIL) == 2
        roles = await _get_invite_roles(db_url, email=_BOOTSTRAP_EMAIL)
        assert sorted(roles) == ["admin", "member"]
