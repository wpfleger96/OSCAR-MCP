"""Shared pieces of the auth router package: response headers, request-model
field caps, session-cookie issuance, and oauth_attempts housekeeping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from fastapi import Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.config import AppConfig
from snore.auth.session_cookie import set_session_cookie
from snore.database import models

_NO_STORE = {"Cache-Control": "no-store"}


class SessionTicket(NamedTuple):
    """Everything needed to issue a session cookie for a resolved login."""

    user_id: int
    profile_id: int
    session_version: int


def apply_session_cookie(
    response: Response, cfg: AppConfig, ticket: SessionTicket
) -> None:
    """Set the signed session cookie — the single ``is_multiuser`` gate.

    Local mode has no session cookie: the auth middleware auto-resolves the
    local actor and ignores cookies entirely.
    """
    if not cfg.is_multiuser:
        return
    set_session_cookie(
        response,
        secret=cfg.session_secret,
        user_id=ticket.user_id,
        active_profile_id=ticket.profile_id,
        session_version=ticket.session_version,
        secure=cfg.secure_cookie,
    )


def issue_session_redirect(
    cfg: AppConfig, ticket: SessionTicket, url: str = "/dashboard"
) -> RedirectResponse:
    """302 redirect carrying a freshly issued session cookie."""
    response = RedirectResponse(url=url, status_code=302)
    apply_session_cookie(response, cfg, ticket)
    return response


# Conservative character bounds on model fields so Pydantic rejects oversized
# inputs before the byte validator runs.  The auth body ceiling in
# CsrfMiddleware (_AUTH_BODY_LIMIT) is the first resource boundary; these
# model limits are the second.
_EMAIL_MAX_LEN = 254  # RFC 5321 maximum email length
_PASSWORD_MAX_CHARS = (
    4096  # Conservative char cap; byte validator refines to 1024 bytes
)
_TOKEN_MAX_LEN = 256  # Invite tokens are 43-char URL-safe base64; cap with margin


async def purge_expired_oauth_attempts(db: AsyncSession, now: datetime) -> int:
    """Delete stale oauth_attempts rows and return the count removed.

    Retains rows for 1 day after expiry/consumption to preserve replay-detection
    capability.  Caps each call at 1000 rows to bound lock hold time.

    Called from both the startup sweep in app.py and the opportunistic on-path
    cleanup below — single predicate definition.
    """
    retention = now - timedelta(days=1)
    # Collect IDs to delete (SELECT … LIMIT 1000) then delete by ID —
    # portable across dialects, avoids DELETE … LIMIT quirks.
    stale_ids = (
        (
            await db.execute(
                select(models.OauthAttempt.id)
                .where(
                    (models.OauthAttempt.expires_at < retention)
                    | (
                        models.OauthAttempt.consumed_at.is_not(None)
                        & (models.OauthAttempt.consumed_at < retention)
                    )
                )
                .order_by(models.OauthAttempt.id)
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )

    if not stale_ids:
        return 0

    await db.execute(
        delete(models.OauthAttempt).where(models.OauthAttempt.id.in_(stale_ids))
    )
    return len(stale_ids)


async def opportunistic_purge_oauth_attempts(db: AsyncSession) -> None:
    """Delete expired/consumed oauth_attempts rows opportunistically.

    Called on the login and invite-redeem paths to bound table growth between
    restarts.  Failures are silently swallowed — this is best-effort cleanup,
    not a hard requirement.
    """
    try:
        await purge_expired_oauth_attempts(db, datetime.now(UTC))
    except Exception:
        pass  # Best-effort; never block the calling path.
