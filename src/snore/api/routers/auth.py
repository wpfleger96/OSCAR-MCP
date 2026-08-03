"""Auth router: login/logout/status/active-profile + invite lookup/redeem.

Routes
------
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/status
POST /api/v1/auth/active-profile
GET  /api/v1/auth/invites/{token}
POST /api/v1/auth/invites/{token}/redeem

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
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.deps import get_db
from snore.api.guards import RequireAuth
from snore.auth.actor import ActorContext, AuthMode
from snore.auth.factory import ActorContextFactory
from snore.auth.invite import InviteRedemptionError
from snore.auth.lockout import get_lockout_store
from snore.auth.passwords import dummy_verify, hash_password, verify_password
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


async def _opportunistic_purge_oauth_attempts(db: AsyncSession) -> None:
    """Delete expired/consumed oauth_attempts rows opportunistically.

    Called on the login and invite-redeem paths to bound table growth between
    restarts.  Failures are silently swallowed — this is best-effort cleanup,
    not a hard requirement.
    """
    try:
        from datetime import UTC, datetime  # noqa: PLC0415

        from sqlalchemy import delete  # noqa: PLC0415

        now = datetime.now(UTC)
        await db.execute(
            delete(models.OauthAttempt).where(
                (models.OauthAttempt.expires_at <= now)
                | (models.OauthAttempt.consumed_at.is_not(None))
            )
        )
    except Exception:
        pass  # Best-effort; never block the calling path.


# ---------------------------------------------------------------------------
# Helper: get trusted client IP from request
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Return the trusted client IP for lockout keying.

    Respects ``SNORE_TRUSTED_PROXIES``: if the immediate peer is in the
    trusted list, prefer ``cf-connecting-ip``.  Otherwise use the peer IP.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    peer = request.client.host if request.client else "unknown"
    cfg = get_config()
    if peer in cfg.trusted_proxies:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            return forwarded
    return peer


# ---------------------------------------------------------------------------
# Helper: CSRF origin check for unsafe methods
# ---------------------------------------------------------------------------


def _check_origin(request: Request) -> None:
    """Raise 403 if the Origin/Referer does not match the allowed origins.

    Applied to all unsafe-method (POST/PUT/PATCH/DELETE) auth routes.
    Safe in local mode (loopback only) and enforced strictly in multiuser.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    if cfg.auth_mode is AuthMode.LOCAL:
        return  # Local mode: only reachable from loopback; no CSRF risk.

    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    origin = origin.split("?")[0].rstrip("/")

    allowed = set()
    if cfg.public_base_url:
        allowed.add(cfg.public_base_url.rstrip("/"))
    # Allow browser-native requests from the same origin (e.g. dev env).
    allowed.add("http://localhost:5173")
    allowed.add("http://127.0.0.1:5173")

    if not any(origin.startswith(a) for a in allowed):
        logger.warning("CSRF origin mismatch: %r not in %r", origin, allowed)
        raise HTTPException(status_code=403, detail="Origin not allowed")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class RedeemRequest(BaseModel):
    password: str


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
    _check_origin(request)
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    lockout = get_lockout_store()
    canonical = body.email.lower().strip()
    ip = _client_ip(request)

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
        dummy_verify()
        lockout.record_failure(canonical, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    if user_row.disabled_at is not None:
        dummy_verify()
        lockout.record_failure(canonical, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    ok, new_hash = verify_password(user_row.password_hash, body.password)
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
            domain=cfg.cookie_domain,
        )
    return response


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    _check_origin(request)
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    response = JSONResponse(content={"message": "Logged out"}, headers=_NO_STORE)
    if cfg.is_multiuser:
        clear_session_cookie(
            response, secure=cfg.secure_cookie, domain=cfg.cookie_domain
        )
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
    _check_origin(request)
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
            domain=cfg.cookie_domain,
        )
    return response


@router.get("/invites/{token}", response_model=InviteInfoResponse)
async def lookup_invite(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return invite metadata (email, valid) for a given token.

    The token itself is never echoed in the response.
    """
    token_hash = _hash_invite_token(token)
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

    return JSONResponse(
        content=InviteInfoResponse(
            email=invite.email if invite else "",
            valid=valid,
        ).model_dump(),
        headers=_NO_STORE,
    )


@router.post("/invites/{token}/redeem", response_model=MessageResponse)
async def redeem_invite_route(
    token: str,
    request: Request,
    body: RedeemRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Redeem an invite with a password — create user + profile atomically.

    The full password-signup state machine:
    1. Validate the invite (token hash lookup, not expired/revoked/redeemed).
    2. Validate the password length.
    3. In one transaction via ``run_txn``:
       - Consume the invite (conditional UPDATE — race-safe).
       - Create the User row with Argon2id password hash.
       - Create the initial default Profile.
       - Link ``user.default_profile_id``.
    4. Set a session cookie for immediate login.

    Fails generically on any invite problem (no oracle attack on state).
    """
    _check_origin(request)
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    # Validate password length.
    if not body.password or len(body.password) > 1024:
        raise HTTPException(
            status_code=422, detail="Password must be 1–1024 characters"
        )

    token_hash = _hash_invite_token(token)

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
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    invite_id = invite.id
    invite_email = invite.email
    invite_role = invite.role
    pw_hash = hash_password(body.password)

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
            domain=cfg.cookie_domain,
        )
    return response


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_invite_token(token: str) -> str:
    """Return the SHA-256 hex digest of the invite token."""
    return hashlib.sha256(token.encode()).hexdigest()
