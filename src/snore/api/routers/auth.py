"""Auth router: login/logout/status/active-profile + invite lookup/redeem.

Routes
------
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/status
POST /api/v1/auth/active-profile
POST /api/v1/auth/invites/lookup   (token in request body — never in URL)
POST /api/v1/auth/invites/redeem   (token + password in request body)

All auth/invite responses carry ``Cache-Control: no-store`` to prevent
credential caching by proxies or browsers.

Security controls
-----------------
- Generic error for wrong-email vs wrong-password (timing equalization via
  dummy Argon2id verification on unknown emails).
- Lockout: per (canonical_email, trusted_client_ip) exponential back-off.
  Lockout check applies on login; failure records on wrong password or
  locked user; success clears the record.
- Disabled users are rejected on all paths.
- CSRF: all unsafe methods check the Origin or Referer header against the
  configured public base URL (+ loopback in local mode).  SameSite=Lax is
  the belt; this check is the suspenders.
- invite tokens are never included in logs or error bodies.
- Invite redemption uses ``run_txn`` for idempotent atomic consumption.
"""

from __future__ import annotations

import hashlib
import logging

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.deps import get_db
from snore.api.guards import RequireAuth
from snore.auth.actor import ActorContext
from snore.auth.factory import ActorContextFactory
from snore.auth.invite import InviteRedemptionError
from snore.auth.lockout import get_invite_lockout_store, get_lockout_store
from snore.auth.passwords import (
    dummy_verify_async,
    hash_password_async,
    validate_password_bytes,
    verify_password_async,
)
from snore.auth.session_cookie import (
    clear_session_cookie,
    set_session_cookie,
)
from snore.database import models
from snore.database.txn import run_txn

logger = logging.getLogger(__name__)

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


# ---------------------------------------------------------------------------
# Opportunistic oauth_attempts purge
# ---------------------------------------------------------------------------


async def _purge_expired_oauth_attempts(db: AsyncSession, now: datetime) -> int:
    """Execute the oauth_attempts purge DELETE and return the deleted row count.

    Single source of truth for the purge predicate — called from both the
    startup purge in app.py and the opportunistic on-path cleanup below.
    """
    from sqlalchemy import delete  # noqa: PLC0415

    result = await db.execute(
        delete(models.OauthAttempt).where(
            (models.OauthAttempt.expires_at <= now)
            | (models.OauthAttempt.consumed_at.is_not(None))
        )
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def _opportunistic_purge_oauth_attempts(db: AsyncSession) -> None:
    """Delete expired/consumed oauth_attempts rows opportunistically.

    Called on the login and invite-redeem paths to bound table growth between
    restarts.  Failures are silently swallowed — this is best-effort cleanup,
    not a hard requirement.
    """
    try:
        await _purge_expired_oauth_attempts(db, datetime.now(UTC))
    except Exception:
        pass  # Best-effort; never block the calling path.


# ---------------------------------------------------------------------------
# Helper: get trusted client IP from request
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

# Conservative character bounds on model fields so Pydantic rejects oversized
# inputs before the byte validator runs.  The auth body ceiling in
# CsrfMiddleware (_AUTH_BODY_LIMIT) is the first resource boundary; these
# model limits are the second.
_EMAIL_MAX_LEN = 254  # RFC 5321 maximum email length
_PASSWORD_MAX_CHARS = (
    4096  # Conservative char cap; byte validator refines to 1024 bytes
)
_TOKEN_MAX_LEN = 256  # Invite tokens are 43-char URL-safe base64; cap with margin


class LoginRequest(BaseModel):
    email: Annotated[str, StringConstraints(max_length=_EMAIL_MAX_LEN)]
    password: Annotated[str, StringConstraints(max_length=_PASSWORD_MAX_CHARS)]


class InviteLookupRequest(BaseModel):
    """Invite lookup — token in request body, never in the URL path."""

    token: Annotated[str, StringConstraints(max_length=_TOKEN_MAX_LEN)]


class InviteRedeemRequest(BaseModel):
    """Invite redemption — both token and password in request body."""

    token: Annotated[str, StringConstraints(max_length=_TOKEN_MAX_LEN)]
    password: Annotated[str, StringConstraints(max_length=_PASSWORD_MAX_CHARS)]


class ActiveProfileRequest(BaseModel):
    profile_id: int


class ProfileInfo(BaseModel):
    id: int
    name: str


class UserInfo(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str


class AuthStatusResponse(BaseModel):
    authenticated: bool
    auth_mode: str  # "local" | "multiuser"
    user: UserInfo | None = None
    profiles: list[ProfileInfo] = []
    active_profile_id: int | None = None


class InviteInfoResponse(BaseModel):
    email: str
    valid: bool


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=MessageResponse)
async def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Authenticate with email + password; set session cookie on success."""
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    lockout = get_lockout_store()
    canonical = body.email.lower().strip()
    ip = get_client_ip(request)

    # Validate password byte length before any DB work or KDF.
    try:
        validate_password_bytes(body.password)
    except ValueError:
        await dummy_verify_async()
        raise HTTPException(status_code=401, detail="Authentication failed") from None

    # Check lockout FIRST before any DB work.
    if lockout.is_locked(canonical, ip):
        # Return a generic 401 — don't reveal that it's specifically a lockout.
        raise HTTPException(status_code=401, detail="Authentication failed")

    # Look up user.
    user_row = (
        (
            await db.execute(
                select(models.User).where(models.User.canonical_email == canonical)
            )
        )
        .scalars()
        .first()
    )

    if user_row is None or user_row.password_hash is None:
        # Unknown email or password-less account → dummy verify + generic error.
        await dummy_verify_async()
        lockout.record_failure(canonical, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    if user_row.disabled_at is not None:
        await dummy_verify_async()
        lockout.record_failure(canonical, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    ok, new_hash = await verify_password_async(user_row.password_hash, body.password)
    if not ok:
        lockout.record_failure(canonical, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    # Rehash if needed (transparent upgrade).
    if new_hash is not None:
        user_row.password_hash = new_hash

    lockout.record_success(canonical, ip)

    # Opportunistic cleanup of expired/consumed oauth_attempts rows.
    await _opportunistic_purge_oauth_attempts(db)

    # Resolve profile (use current default).
    factory = ActorContextFactory(db)
    actor = await factory.make(
        user_id=user_row.id,
        active_profile_id=user_row.default_profile_id,
        mode=cfg.auth_mode,
    )

    response = JSONResponse(
        content={"message": "Logged in"},
        headers=_NO_STORE,
    )
    if cfg.is_multiuser:
        set_session_cookie(
            response,
            secret=cfg.session_secret,
            user_id=actor.user_id,
            active_profile_id=actor.profile_id,
            session_version=user_row.session_version,
            secure=cfg.secure_cookie,
        )
    return response


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    response = JSONResponse(content={"message": "Logged out"}, headers=_NO_STORE)
    if cfg.is_multiuser:
        clear_session_cookie(response, secure=cfg.secure_cookie)
    return response


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return authentication state and profile list for the current session."""
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    actor: ActorContext | None = getattr(request.state, "actor", None)

    if actor is None:
        return JSONResponse(
            content=AuthStatusResponse(
                authenticated=False,
                auth_mode=cfg.auth_mode.value,
            ).model_dump(),
            headers=_NO_STORE,
        )

    user = await db.get(models.User, actor.user_id)
    if user is None:
        return JSONResponse(
            content=AuthStatusResponse(
                authenticated=False,
                auth_mode=cfg.auth_mode.value,
            ).model_dump(),
            headers=_NO_STORE,
        )

    profiles_rows = (
        (
            await db.execute(
                select(models.Profile)
                .where(
                    models.Profile.user_id == actor.user_id,
                    models.Profile.deleting_at.is_(None),
                )
                .order_by(models.Profile.id)
            )
        )
        .scalars()
        .all()
    )

    return JSONResponse(
        content=AuthStatusResponse(
            authenticated=True,
            auth_mode=cfg.auth_mode.value,
            user=UserInfo(
                id=user.id,
                email=user.canonical_email,
                display_name=user.display_name,
                role=user.role,
            ),
            profiles=[ProfileInfo(id=p.id, name=p.name) for p in profiles_rows],
            active_profile_id=actor.profile_id,
        ).model_dump(),
        headers=_NO_STORE,
    )


@router.post("/active-profile", response_model=MessageResponse)
async def set_active_profile(
    request: Request,
    body: ActiveProfileRequest,
    actor: RequireAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Switch the active profile; re-validates ownership."""
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    # Validate the requested profile belongs to this user and is not tombstoned.
    profile = (
        (
            await db.execute(
                select(models.Profile).where(
                    models.Profile.id == body.profile_id,
                    models.Profile.user_id == actor.user_id,
                    models.Profile.deleting_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    response = JSONResponse(
        content={"message": "Active profile updated"},
        headers=_NO_STORE,
    )
    if cfg.is_multiuser:
        set_session_cookie(
            response,
            secret=cfg.session_secret,
            user_id=actor.user_id,
            active_profile_id=body.profile_id,
            session_version=user.session_version,
            secure=cfg.secure_cookie,
        )
    return response


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
    token_hash = _hash_invite_token(body.token)

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
    valid = (
        invite is not None
        and invite.redeemed_at is None
        and invite.revoked_at is None
        and invite.expires_at > now
    )

    if not valid:
        lockout.record_failure(token_hash, ip)

    return JSONResponse(
        content=InviteInfoResponse(
            # Only expose the email when the invite is valid — prevents token
            # holders from recovering historical invitee emails (S1).
            email=invite.email if (invite is not None and valid) else "",
            valid=valid,
        ).model_dump(),
        headers=_NO_STORE,
    )


@router.post("/invites/redeem", response_model=MessageResponse)
async def redeem_invite_route(
    request: Request,
    body: InviteRedeemRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Redeem an invite with a password — create user + profile atomically.

    Both token and password are in the request body so neither appears in the
    URL path or access logs.  SNORE_MULTIUSER_PLAN.md:233 (secret hygiene).

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
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    ip = get_client_ip(request)
    lockout = get_invite_lockout_store()
    token_hash = _hash_invite_token(body.token)

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
    if (
        invite is None
        or invite.redeemed_at is not None
        or invite.revoked_at is not None
        or invite.expires_at <= now
    ):
        lockout.record_failure(token_hash, ip)
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    invite_id = invite.id
    invite_email = invite.email
    invite_role = invite.role
    pw_hash = await hash_password_async(body.password)

    # Accumulate the created IDs so the cookie can be set.
    result_holder: dict[str, int] = {}

    async def _do_redeem(txn_db: AsyncSession) -> None:
        from sqlalchemy import update  # noqa: PLC0415

        from snore.database.models import Profile, User  # noqa: PLC0415

        # Consume the invite atomically (idempotent via IS NULL guard).
        res = await txn_db.execute(
            update(models.Invite)
            .where(
                models.Invite.id == invite_id,
                models.Invite.redeemed_at.is_(None),
                models.Invite.revoked_at.is_(None),
                models.Invite.expires_at > now,
            )
            .values(redeemed_at=now)
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            raise InviteRedemptionError("Invite already redeemed or expired")

        # Check for existing user with this email (idempotent re-entry guard).
        existing = (
            (
                await txn_db.execute(
                    select(models.User).where(
                        models.User.canonical_email == invite_email.lower().strip()
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise InviteRedemptionError("Account already exists for this email")

        user = User(
            canonical_email=invite_email.lower().strip(),
            password_hash=pw_hash,
            role=invite_role,
            session_version=0,
        )
        txn_db.add(user)
        await txn_db.flush()

        profile = Profile(user_id=user.id, name="Default")
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
    await _opportunistic_purge_oauth_attempts(db)

    response = JSONResponse(
        content={"message": "Account created"},
        headers=_NO_STORE,
    )
    if cfg.is_multiuser:
        set_session_cookie(
            response,
            secret=cfg.session_secret,
            user_id=result_holder["user_id"],
            active_profile_id=result_holder["profile_id"],
            session_version=0,
            secure=cfg.secure_cookie,
        )
    return response


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_invite_token(token: str) -> str:
    """Return the SHA-256 hex digest of the invite token."""
    return hashlib.sha256(token.encode()).hexdigest()
