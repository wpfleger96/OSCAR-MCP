"""Single ActorContext factory.

This is the ONLY place profile ownership is validated.  All code paths —
API middleware, CLI resolver, import job workers, test fixtures — construct
ActorContext through this factory.

Construction rules:
1. User must exist and not be disabled.
2. Profile must exist, belong to the user, and not be tombstoned (deleting_at IS NULL).
3. If active_profile_id is invalid/foreign/corrupt, fall back to user.default_profile_id.
4. If default_profile_id is also invalid, fall back to the user's first owned, live profile.
5. If no live profile is found, raise ValueError — context construction always has a profile.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.actor import ActorContext, AuthMode, Role
from snore.database import models

logger = logging.getLogger(__name__)


class ActorContextFactory:
    """Creates validated ActorContext instances."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def make(
        self,
        user_id: int,
        active_profile_id: int | None,
        mode: AuthMode,
    ) -> ActorContext:
        """Build and validate an ActorContext.

        Args:
            user_id:           DB ID of the authenticated user.
            active_profile_id: Requested profile, from session cookie.
                               May be None, foreign, or corrupt — falls back.
            mode:              Current auth mode.

        Returns:
            Validated ActorContext.

        Raises:
            ValueError: If the user is not found, is disabled, or has no live profile.
        """
        user = await self._db.get(models.User, user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found")
        if user.disabled_at is not None:
            raise ValueError(f"User {user_id} is disabled")

        role = Role(user.role)
        profile_id = await self._resolve_profile(user, active_profile_id)

        return ActorContext(
            user_id=user.id,
            profile_id=profile_id,
            role=role,
            mode=mode,
        )

    async def _resolve_profile(
        self, user: models.User, requested_id: int | None
    ) -> int:
        """Return a valid, live profile_id for this user.

        Resolution order:
        1. requested_id, if it belongs to user and is not tombstoned
        2. user.default_profile_id, if valid and not tombstoned
        3. first live profile owned by user (by id asc)

        Logs a warning on fallback so operators can diagnose stale cookies.
        """
        # Try requested first
        if requested_id is not None:
            profile = await self._get_live_profile(requested_id, user.id)
            if profile is not None:
                return profile.id
            logger.warning(
                "Active profile %s is invalid/foreign/tombstoned for user %s; "
                "falling back to default",
                requested_id,
                user.id,
            )

        # Try default
        if user.default_profile_id is not None:
            profile = await self._get_live_profile(user.default_profile_id, user.id)
            if profile is not None:
                return profile.id
            logger.warning(
                "Default profile %s is invalid/foreign/tombstoned for user %s; "
                "falling back to first owned profile",
                user.default_profile_id,
                user.id,
            )

        # Fall back to first live profile
        stmt = (
            select(models.Profile)
            .where(
                models.Profile.user_id == user.id,
                models.Profile.deleting_at.is_(None),
            )
            .order_by(models.Profile.id)
            .limit(1)
        )
        result = (await self._db.execute(stmt)).scalars().first()
        if result is None:
            raise ValueError(f"User {user.id} has no live profiles")
        return result.id

    async def _get_live_profile(
        self, profile_id: int, user_id: int
    ) -> models.Profile | None:
        """Return a profile only if it exists, belongs to user, and is not tombstoned."""
        stmt = select(models.Profile).where(
            models.Profile.id == profile_id,
            models.Profile.user_id == user_id,
            models.Profile.deleting_at.is_(None),
        )
        return (await self._db.execute(stmt)).scalars().first()

    async def make_local(self, mode: AuthMode) -> ActorContext:
        """Return an ActorContext for local (single-user) mode.

        Resolves the first admin user and their default (or first live) profile.
        If no user exists, auto-provisions a minimal admin user + default profile.

        This is the only entry point that does not require an existing user_id —
        it is safe only in LOCAL mode where there is exactly one operator.
        """
        # Try to find the first existing admin user.
        stmt = (
            select(models.User)
            .where(models.User.disabled_at.is_(None))
            .order_by(models.User.id)
            .limit(1)
        )
        user = (await self._db.execute(stmt)).scalars().first()

        if user is None:
            # Auto-provision: create admin user + default profile on first run.
            user = models.User(canonical_email="local@localhost", role="admin")
            self._db.add(user)
            await self._db.flush()
            profile = models.Profile(user_id=user.id, name="Default")
            self._db.add(profile)
            await self._db.flush()
            user.default_profile_id = profile.id
            logger.info("Local mode: auto-provisioned admin user id=%d", user.id)

        profile_id = await self._resolve_profile(user, user.default_profile_id)
        return ActorContext(
            user_id=user.id,
            profile_id=profile_id,
            role=Role(user.role),
            mode=mode,
        )

    async def make_from_cli(
        self,
        user_ref: str | None,
        profile_ref: str | None,
        mode: AuthMode,
    ) -> ActorContext:
        """Build an ActorContext from CLI --user/--profile overrides.

        Resolution:
        - user_ref=None → make_local() (single-user dev mode, auto-provisions)
        - user_ref provided → look up by canonical_email; raises ValueError if missing
          or disabled
        - profile_ref=None → user's default_profile_id (standard fallback chain)
        - profile_ref provided → resolve by exact name or numeric ID; raises ValueError
          if not found

        Raises ValueError on any resolution failure.  Callers that want
        Click-friendly errors should use resolve_cli_profile_id() instead.
        """
        if user_ref is None:
            actor = await self.make_local(mode)
            if profile_ref is None:
                return actor
            # Narrow to a specific profile within the local user.
            profile_id = await self._resolve_profile_by_ref(actor.user_id, profile_ref)
            return ActorContext(
                user_id=actor.user_id,
                profile_id=profile_id,
                role=actor.role,
                mode=mode,
            )

        # Look up by canonical_email (lowercased, as stored).
        stmt = select(models.User).where(
            models.User.canonical_email == user_ref.lower().strip()
        )
        user = (await self._db.execute(stmt)).scalars().first()
        if user is None:
            raise ValueError(f"User {user_ref!r} not found")
        if user.disabled_at is not None:
            raise ValueError(f"User {user_ref!r} is disabled")

        role = Role(user.role)
        if profile_ref is None:
            profile_id = await self._resolve_profile(user, user.default_profile_id)
        else:
            profile_id = await self._resolve_profile_by_ref(user.id, profile_ref)

        return ActorContext(
            user_id=user.id,
            profile_id=profile_id,
            role=role,
            mode=mode,
        )

    async def _resolve_profile_by_ref(self, user_id: int, profile_ref: str) -> int:
        """Resolve a profile by name or numeric ID for user_id.

        Raises ValueError if the profile is not found or does not belong to the user.
        Never silently falls back to another profile.
        """
        # Try numeric ID first.
        numeric_id: int | None = None
        try:
            numeric_id = int(profile_ref)
        except ValueError:
            pass

        if numeric_id is not None:
            profile = await self._get_live_profile(numeric_id, user_id)
            if profile is None:
                raise ValueError(f"Profile ID {profile_ref!r} not found for this user")
            return profile.id

        # Exact name match (names are UNIQUE per user).
        stmt = select(models.Profile).where(
            models.Profile.user_id == user_id,
            models.Profile.name == profile_ref,
            models.Profile.deleting_at.is_(None),
        )
        profile = (await self._db.execute(stmt)).scalars().first()
        if profile is None:
            raise ValueError(f"Profile {profile_ref!r} not found")
        return profile.id


async def resolve_local_profile_id(db: AsyncSession) -> int:
    """Return the active profile_id for local (single-user) CLI mode.

    Resolves or auto-provisions the first admin user and their default profile.
    Convenience wrapper over ``ActorContextFactory.make_local`` for CLI callers
    that only need the ``profile_id``.
    """
    actor = await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)
    return actor.profile_id


async def resolve_cli_profile_id(
    db: AsyncSession,
    user_ref: str | None,
    profile_ref: str | None,
) -> int:
    """Resolve profile_id for CLI data commands with --user/--profile overrides.

    Wraps ``ActorContextFactory.make_from_cli``; converts ValueError to
    ``click.ClickException`` so Click handles the error cleanly.

    Args:
        db:          Open async DB session.
        user_ref:    Value of --user / SNORE_USER env var (may be None).
        profile_ref: Value of --profile / SNORE_PROFILE env var (may be None).

    Returns:
        Resolved profile_id.

    Raises:
        click.ClickException: If user or profile cannot be resolved.
    """
    import click  # noqa: PLC0415

    try:
        actor = await ActorContextFactory(db).make_from_cli(
            user_ref=user_ref,
            profile_ref=profile_ref,
            mode=AuthMode.LOCAL,
        )
        return actor.profile_id
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
