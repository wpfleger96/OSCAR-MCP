"""Health ingest token service — machine-auth tokens for the push ingest endpoint."""

import secrets

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.invite_tokens import hash_invite_token
from snore.database import models

__all__ = ["HealthTokenService", "HealthTokenInfo"]


class HealthTokenInfo(BaseModel):
    """Public view of a HealthIngestToken — never exposes the hash or plaintext."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class HealthTokenService:
    """Service for managing per-profile machine-auth ingest tokens.

    Not profile-scoped at construction: token verification is cross-profile
    (the token itself identifies the profile), so methods that are
    profile-specific accept ``profile_id`` as an explicit argument.
    """

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def create_token(
        self, profile_id: int, label: str | None = None
    ) -> tuple[str, HealthTokenInfo]:
        """Generate a new ingest token for ``profile_id``.

        Returns ``(plaintext, info)``.  The plaintext is returned exactly once
        and is never stored — only the SHA-256 hash is persisted.
        """
        plaintext = secrets.token_urlsafe(32)
        token = models.HealthIngestToken(
            profile_id=profile_id,
            token_hash=hash_invite_token(plaintext),
            label=label,
        )
        self.db_session.add(token)
        await self.db_session.flush()
        return plaintext, HealthTokenInfo.model_validate(token)

    async def verify(self, token: str) -> int | None:
        """Verify a plaintext token and return its owning ``profile_id``.

        Returns ``None`` for unknown or revoked tokens — the two failure cases
        are intentionally indistinguishable to avoid oracle attacks.  On
        success, ``last_used_at`` is updated to the current UTC instant.
        """
        token_hash = hash_invite_token(token)
        row = (
            (
                await self.db_session.execute(
                    select(models.HealthIngestToken).where(
                        models.HealthIngestToken.token_hash == token_hash,
                        models.HealthIngestToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        row.last_used_at = datetime.now(UTC)
        await self.db_session.flush()
        return row.profile_id

    async def revoke(self, token_id: int, profile_id: int) -> bool:
        """Set ``revoked_at`` on a token owned by ``profile_id``.

        Returns ``True`` on success.  Returns ``False`` when the token is not
        found, belongs to a different profile, or is already revoked — all
        failure modes return the same value to avoid leaking ownership.
        """
        row = (
            (
                await self.db_session.execute(
                    select(models.HealthIngestToken).where(
                        models.HealthIngestToken.id == token_id,
                        models.HealthIngestToken.profile_id == profile_id,
                        models.HealthIngestToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        await self.db_session.flush()
        return True

    async def list_tokens(self, profile_id: int) -> list[HealthTokenInfo]:
        """Return all tokens for ``profile_id`` ordered by ``created_at``.

        Includes revoked tokens for audit purposes.  Never returns the hash
        or any form of the plaintext.
        """
        rows = (
            (
                await self.db_session.execute(
                    select(models.HealthIngestToken)
                    .where(models.HealthIngestToken.profile_id == profile_id)
                    .order_by(models.HealthIngestToken.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [HealthTokenInfo.model_validate(r) for r in rows]
