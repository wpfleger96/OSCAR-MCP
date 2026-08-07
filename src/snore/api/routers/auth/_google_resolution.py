"""Window-2 account resolution for the Google OAuth callback.

Pure ``(db, cfg, claims) -> SessionTicket`` logic, separated from the HTTP
and flow plumbing in ``routes_google``.  Every helper here runs inside the
callback's write transaction and raises :class:`TxFailure` on any failure,
so the caller rolls back fully and returns the uniform generic 400.
"""

from __future__ import annotations

import logging

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.config import AppConfig
from snore.api.routers.auth._common import SessionTicket
from snore.auth.emails import normalize_email
from snore.auth.factory import ActorContextFactory
from snore.auth.invite import invite_valid_clauses
from snore.database import models

logger = logging.getLogger(__name__)


class TxFailure(Exception):
    """Internal signal — raised inside a transaction context to force rollback.

    Caught immediately outside the ``async with db.begin()`` block and
    translated to the generic OAuth failure response.  Never propagates
    further.
    """


async def ticket_for(
    db: AsyncSession, cfg: AppConfig, user: models.User
) -> SessionTicket:
    """Resolve the user's profile and build a session ticket.

    Profile-resolution failures abort the transaction like every other flow
    failure (generic 400) instead of surfacing as a 500.
    """
    try:
        actor = await ActorContextFactory(db).make(
            user_id=user.id,
            active_profile_id=user.default_profile_id,
            mode=cfg.auth_mode,
        )
    except ValueError as exc:
        raise TxFailure() from exc
    user.last_login_at = datetime.now(UTC)
    return SessionTicket(actor.user_id, actor.profile_id, user.session_version)


async def linked_user_ticket(
    db: AsyncSession, cfg: AppConfig, sub: str
) -> SessionTicket | None:
    """Ticket for the user already linked to ``(google, sub)``.

    Returns None when no identity row exists; rejects disabled or missing
    users outright.
    """
    identity = (
        (
            await db.execute(
                select(models.AuthIdentity).where(
                    models.AuthIdentity.provider == "google",
                    models.AuthIdentity.subject == sub,
                )
            )
        )
        .scalars()
        .first()
    )
    if identity is None:
        return None
    user = await db.get(models.User, identity.user_id)
    if user is None or user.disabled_at is not None:
        raise TxFailure()
    return await ticket_for(db, cfg, user)


async def link_identity_ticket(
    db: AsyncSession, cfg: AppConfig, user: models.User, sub: str, email_raw: str
) -> SessionTicket:
    """Link a new Google identity to an existing user and log them in."""
    if user.disabled_at is not None:
        raise TxFailure()
    db.add(
        models.AuthIdentity(
            user_id=user.id,
            provider="google",
            subject=sub,
            email=email_raw,
        )
    )
    # A deliberate re-link via the invite-signup path clears any previous
    # unlink flag so the auto-link path is restored for future logins.
    user.google_link_disabled = False
    # Audit trail: linking changes who can access the account without a
    # password, so operators need a record of when it happened.
    logger.info(
        "Linked new Google identity to user id=%s (role=%s)", user.id, user.role
    )
    return await ticket_for(db, cfg, user)


async def resolve_login(
    db: AsyncSession, cfg: AppConfig, claims: dict[str, object]
) -> SessionTicket:
    """Login-kind resolution: linked identity, else verified-email auto-link.

    A user with no Google identity (e.g. created via the password invite
    flow) is auto-linked when their canonical email matches the Google
    account's — the same trust link the invite signup path establishes, and
    safe because ``fetch_google_id_token_claims`` requires
    ``email_verified is True``.  Never provisions a new account.

    Admin accounts are excluded: control of a matching mailbox alone must
    not grant admin access.  Admins link Google deliberately via an invite
    addressed to their own email (signup path b).
    """
    sub = str(claims["sub"])
    ticket = await linked_user_ticket(db, cfg, sub)
    if ticket is not None:
        return ticket

    email_raw = str(claims.get("email", ""))
    email_canonical = normalize_email(email_raw)
    if not email_canonical:
        raise TxFailure()
    user = (
        (
            await db.execute(
                select(models.User).where(
                    models.User.canonical_email == email_canonical
                )
            )
        )
        .scalars()
        .first()
    )
    if user is None or user.role == "admin" or user.google_link_disabled:
        raise TxFailure()
    return await link_identity_ticket(db, cfg, user, sub, email_raw)


async def resolve_signup(
    db: AsyncSession,
    cfg: AppConfig,
    claims: dict[str, object],
    *,
    invite_id: int,
    invite_role: str,
    now: datetime,
) -> SessionTicket:
    """Signup-kind resolution.

    Resolution order:
    a. Auth identity (google, sub) already exists → login, leave invite.
    b. User with matching canonical email exists → link identity, consume invite.
    c. Neither → create user + profile + identity, consume invite.
    """
    sub = str(claims["sub"])
    email_raw = str(claims.get("email", ""))
    email_canonical = normalize_email(email_raw)

    # Path a: identity already linked → login (leave invite unconsumed).
    ticket = await linked_user_ticket(db, cfg, sub)
    if ticket is not None:
        return ticket

    # Paths b/c: consume invite FIRST — before any account state mutations.
    # Any failure here rolls back the attempt consume too.
    invite_result = await db.execute(
        sa_update(models.Invite)
        .where(models.Invite.id == invite_id, *invite_valid_clauses(now))
        .values(redeemed_at=now)
    )
    if int(invite_result.rowcount) == 0:  # type: ignore[attr-defined]
        raise TxFailure()

    # Path b: user with matching email → link identity.
    existing_user = (
        (
            await db.execute(
                select(models.User).where(
                    models.User.canonical_email == email_canonical
                )
            )
        )
        .scalars()
        .first()
    )
    if existing_user is not None:
        return await link_identity_ticket(db, cfg, existing_user, sub, email_raw)

    # Path c: create new user + profile + identity.
    new_user = models.User(
        canonical_email=email_canonical,
        role=invite_role,
        session_version=0,
    )
    db.add(new_user)
    await db.flush()

    profile = models.Profile(user_id=new_user.id, name="Default")
    db.add(profile)
    await db.flush()

    new_user.default_profile_id = profile.id
    new_user.last_login_at = datetime.now(UTC)
    db.add(
        models.AuthIdentity(
            user_id=new_user.id,
            provider="google",
            subject=sub,
            email=email_raw,
        )
    )
    return SessionTicket(new_user.id, profile.id, new_user.session_version)
