"""Invite validity — the single definition of what makes an invite redeemable.

An invite is valid iff it is unredeemed, unrevoked, and unexpired.  Every
consumer — lookup, redemption (as a conditional UPDATE guard), Google signup,
and the admin list — must use the helpers here rather than restating the
predicate, so the definition cannot drift between call sites.

Consumption itself stays a conditional UPDATE guarded by these clauses:
because the write is conditioned on ``redeemed_at IS NULL`` it is naturally
idempotent — exactly one request can ever consume a given invite, even under
concurrent replays.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement

from snore.database import models

__all__ = ["InviteRedemptionError", "invite_valid_clauses", "is_invite_valid"]


class InviteRedemptionError(Exception):
    """Raised when an invite cannot be redeemed (expired, revoked, or already used)."""


def invite_valid_clauses(now: datetime) -> tuple[ColumnElement[bool], ...]:
    """SQL clauses selecting valid invites: unredeemed, unrevoked, unexpired."""
    return (
        models.Invite.redeemed_at.is_(None),
        models.Invite.revoked_at.is_(None),
        models.Invite.expires_at > now,
    )


def is_invite_valid(invite: models.Invite | None, now: datetime) -> bool:
    """In-memory counterpart of :func:`invite_valid_clauses`."""
    return (
        invite is not None
        and invite.redeemed_at is None
        and invite.revoked_at is None
        and invite.expires_at > now
    )
