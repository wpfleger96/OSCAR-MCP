"""Invite routes: lookup and password redemption.

Routes
------
POST /api/v1/auth/invites/lookup   (token in request body — never in URL)
POST /api/v1/auth/invites/redeem   (token + password in request body)

Invite tokens are never included in URLs, logs, or error bodies.  Redemption
uses ``run_txn`` for idempotent atomic consumption, and all invite failures
collapse to a generic 404 (no oracle on invite state).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.config import get_config
from snore.api.constants import NO_STORE
from snore.api.deps import get_db
from snore.api.routers.auth._common import (
    PASSWORD_MAX_CHARS,
    TOKEN_MAX_LEN,
    SessionTicket,
    apply_session_cookie,
    opportunistic_purge_oauth_attempts,
)
from snore.api.schemas import MessageResponse
from snore.auth.emails import normalize_email
from snore.auth.invite import (
    InviteRedemptionError,
    invite_valid_clauses,
    is_invite_valid,
)
from snore.auth.invite_tokens import hash_invite_token
from snore.auth.lockout import get_invite_lockout_store
from snore.auth.passwords import hash_password_async, validate_password_bytes
from snore.database import models
from snore.database.txn import run_txn

router = APIRouter()


class InviteLookupRequest(BaseModel):
    """Invite lookup — token in request body, never in the URL path."""

    token: Annotated[str, StringConstraints(max_length=TOKEN_MAX_LEN)]


class InviteRedeemRequest(BaseModel):
    """Invite redemption — both token and password in request body."""

    token: Annotated[str, StringConstraints(max_length=TOKEN_MAX_LEN)]
    password: Annotated[str, StringConstraints(max_length=PASSWORD_MAX_CHARS)]


class InviteInfoResponse(BaseModel):
    email: str
    valid: bool


@router.post("/invites/lookup", response_model=InviteInfoResponse)
async def lookup_invite(
    request: Request,
    body: InviteLookupRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return invite metadata (email, valid) for a token submitted in the request body.

    The token is never echoed in the response and never appears in the URL path
    so it does not enter access logs.  The invite URL printed by
    ``snore user invite`` carries the token in a URL fragment
    (``/invite#<token>``) so the UI extracts it client-side and POST it here.

    Rate-limited by per-IP lockout to slow down token probing.
    """
    ip = get_client_ip(request)
    lockout = get_invite_lockout_store()
    token_hash = hash_invite_token(body.token)

    # Rate limit per (token_hash, IP): slow down repeated probing of the same
    # token while the RateLimitMiddleware handles cross-token IP enumeration.
    if lockout.is_locked(token_hash, ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    invite = (
        (
            await db.execute(
                select(models.Invite).where(models.Invite.token_hash == token_hash)
            )
        )
        .scalars()
        .first()
    )

    now = datetime.now(UTC)
    valid = is_invite_valid(invite, now)

    if not valid:
        lockout.record_failure(token_hash, ip)

    return JSONResponse(
        content=InviteInfoResponse(
            # Only expose the email when the invite is valid — prevents token
            # holders from recovering historical invitee emails (S1).
            email=invite.email if (invite is not None and valid) else "",
            valid=valid,
        ).model_dump(),
        headers=NO_STORE,
    )


@router.post("/invites/redeem", response_model=MessageResponse)
async def redeem_invite_route(
    request: Request,
    body: InviteRedeemRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Redeem an invite with a password — create user + profile atomically.

    Both token and password are in the request body so neither appears in the
    URL path or access logs (secret hygiene).

    State machine:
    1. Validate the password byte length (shared byte-based validator).
    2. Validate the invite (token hash lookup, not expired/revoked/redeemed).
    3. In one transaction via ``run_txn``:
       - Consume the invite (conditional UPDATE — race-safe).
       - Create the User row with Argon2id password hash.
       - Create the initial default Profile.
       - Link ``user.default_profile_id``.
    4. Set a session cookie for immediate login.

    Fails generically on any invite problem (no oracle attack on state).
    """
    cfg = get_config()
    ip = get_client_ip(request)
    lockout = get_invite_lockout_store()
    token_hash = hash_invite_token(body.token)

    # Rate limit per (token_hash, IP).
    if lockout.is_locked(token_hash, ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    # Validate password byte length using the shared byte-based validator.
    # validate_password_bytes also rejects empty passwords (invariant: 1–1024 bytes).
    try:
        validate_password_bytes(body.password)
    except ValueError:
        lockout.record_failure(token_hash, ip)
        raise HTTPException(
            status_code=422, detail="Password must be 1–1024 bytes encoded"
        ) from None

    # Gather invite state outside the retry loop (read-only).
    invite = (
        (
            await db.execute(
                select(models.Invite).where(models.Invite.token_hash == token_hash)
            )
        )
        .scalars()
        .first()
    )

    now = datetime.now(UTC)
    if invite is None or not is_invite_valid(invite, now):
        lockout.record_failure(token_hash, ip)
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    invite_id = invite.id
    invite_email = invite.email
    invite_role = invite.role
    pw_hash = await hash_password_async(body.password)

    # Accumulate the created IDs so the cookie can be set.
    result_holder: dict[str, int] = {}

    async def _do_redeem(txn_db: AsyncSession) -> None:
        # Consume the invite atomically (idempotent via IS NULL guard).
        res = await txn_db.execute(
            update(models.Invite)
            .where(models.Invite.id == invite_id, *invite_valid_clauses(now))
            .values(redeemed_at=now)
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            raise InviteRedemptionError("Invite already redeemed or expired")

        # Check for existing user with this email (idempotent re-entry guard).
        existing = (
            (
                await txn_db.execute(
                    select(models.User).where(
                        models.User.canonical_email == normalize_email(invite_email)
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise InviteRedemptionError("Account already exists for this email")

        user = models.User(
            canonical_email=normalize_email(invite_email),
            password_hash=pw_hash,
            role=invite_role,
            session_version=0,
        )
        txn_db.add(user)
        await txn_db.flush()

        profile = models.Profile(user_id=user.id, name="Default")
        txn_db.add(profile)
        await txn_db.flush()

        user.default_profile_id = profile.id
        result_holder["user_id"] = user.id
        result_holder["profile_id"] = profile.id

    try:
        await run_txn(_do_redeem)
    except InviteRedemptionError as exc:
        raise HTTPException(
            status_code=404, detail="Invite not found or expired"
        ) from exc

    # Opportunistic cleanup of expired/consumed oauth_attempts rows.
    await opportunistic_purge_oauth_attempts(db)

    response = JSONResponse(
        content={"message": "Account created"},
        headers=NO_STORE,
    )
    apply_session_cookie(
        response,
        cfg,
        SessionTicket(result_holder["user_id"], result_holder["profile_id"], 0),
    )
    return response
