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
