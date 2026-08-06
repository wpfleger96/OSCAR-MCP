"""Test-only invite redemption helper.

Production code consumes invites inline in its own transactions (the auth
router's redeem/signup paths).  Tests that exercise the conditional-UPDATE
consumption pattern in isolation use this standalone helper, which preserves
the run_txn retry semantics and the precise error messages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.invite import InviteRedemptionError, invite_valid_clauses
from snore.database import models
from snore.database.txn import run_txn as _default_run_txn

RunTxn = Callable[..., Awaitable[None]]


async def redeem_invite_once(
    invite_id: int, *, run_txn: RunTxn = _default_run_txn
) -> None:
    """Conditionally consume an invite by setting ``redeemed_at``.

    The ``redeemed_at IS NULL`` guard makes the write idempotent — a
    concurrent second claim of the same invite is detected and rejected.

    Raises:
        InviteRedemptionError: If the invite is expired, revoked, or already
            redeemed (including by a concurrent request that won the race).
    """

    async def _do_redeem(db: AsyncSession) -> None:
        now = datetime.now(UTC)
        result = await db.execute(
            update(models.Invite)
            .where(models.Invite.id == invite_id, *invite_valid_clauses(now))
            .values(redeemed_at=now)
        )
        if result.rowcount == 0:
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
