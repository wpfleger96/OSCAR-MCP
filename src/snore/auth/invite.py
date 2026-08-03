"""Invite redemption — idempotent transaction runner for invite consumption.

The redemption step is a single conditional UPDATE:
    UPDATE invites
    SET redeemed_at = NOW()
    WHERE id = :invite_id
      AND redeemed_at IS NULL
      AND revoked_at IS NULL
      AND expires_at > NOW()

Because the UPDATE is conditioned on ``redeemed_at IS NULL`` it is naturally
idempotent: a replay of the same invite ID has no effect after the first
successful commit.  ``run_txn`` retries this unit on SQLite contention —
exactly one invite row is ever consumed, guaranteed by the ``IS NULL`` check.

This module exposes only the transaction-level helper.  The auth router
(Phase 2) calls ``redeem_invite`` from inside a larger transaction that also
creates the user/identity row.
"""

from __future__ import annotations

import logging

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.txn import run_txn

logger = logging.getLogger(__name__)

__all__ = ["InviteRedemptionError", "redeem_invite"]


class InviteRedemptionError(Exception):
    """Raised when an invite cannot be redeemed (expired, revoked, or already used)."""


async def redeem_invite(invite_id: int) -> None:
    """Conditionally consume an invite by setting ``redeemed_at``.

    Uses ``run_txn`` for automatic retry on SQLite contention.  The
    ``redeemed_at IS NULL`` guard makes the write idempotent — a concurrent
    second claim of the same invite is detected and rejected.

    Args:
        invite_id: Primary key of the ``Invite`` row to redeem.

    Raises:
        InviteRedemptionError: If the invite is expired, revoked, or already
            redeemed (including by a concurrent request that won the race).
    """

    async def _do_redeem(db: AsyncSession) -> None:
        now = datetime.now(UTC)
        # Conditional UPDATE: only touches rows where the invite is still valid.
        result = await db.execute(
            update(models.Invite)
            .where(
                models.Invite.id == invite_id,
                models.Invite.redeemed_at.is_(None),
                models.Invite.revoked_at.is_(None),
                models.Invite.expires_at > now,
            )
            .values(redeemed_at=now)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            # Read the current invite state to give a precise error message.
            invite = (
                (
                    await db.execute(
                        select(models.Invite).where(models.Invite.id == invite_id)
                    )
                )
                .scalars()
                .first()
            )
            if invite is None:
                raise InviteRedemptionError(f"Invite {invite_id} not found")
            if invite.redeemed_at is not None:
                raise InviteRedemptionError("Invite has already been redeemed")
            if invite.revoked_at is not None:
                raise InviteRedemptionError("Invite has been revoked")
            if invite.expires_at <= now:
                raise InviteRedemptionError("Invite has expired")
            raise InviteRedemptionError("Invite cannot be redeemed")

    await run_txn(_do_redeem)
