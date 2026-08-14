"""Session routes: password login, logout, demo login, status, profile switch.

Routes
------
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/demo-login
GET  /api/v1/auth/status
POST /api/v1/auth/active-profile

Security controls
-----------------
- Generic error for wrong-email vs wrong-password (timing equalization via
  dummy Argon2id verification on unknown emails).
- Lockout: per (canonical_email, trusted_client_ip) exponential back-off.
  Lockout check applies on login; failure records on wrong password or
  locked user; success clears the record.
- Disabled users are rejected on all paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.config import get_config
from snore.api.constants import NO_STORE
from snore.api.deps import get_db
from snore.api.guards import RequireAuth
from snore.api.routers.auth._common import (
    EMAIL_MAX_LEN,
    PASSWORD_MAX_CHARS,
    SessionTicket,
    apply_session_cookie,
    opportunistic_purge_oauth_attempts,
)
from snore.api.schemas import MessageResponse
from snore.auth.actor import ActorContext
from snore.auth.emails import normalize_email
from snore.auth.factory import ActorContextFactory
from snore.auth.lockout import get_lockout_store
from snore.auth.passwords import (
    dummy_verify_async,
    validate_password_bytes,
    verify_password_async,
)
from snore.auth.session_cookie import clear_session_cookie
from snore.auth.totp import (
    decode_totp_pending_token,
    encode_totp_pending_token,
    is_recovery_code,
    is_totp_code,
    redeem_recovery_code,
    verify_totp_code,
)
from snore.database import models

router = APIRouter()


class LoginRequest(BaseModel):
    email: Annotated[str, StringConstraints(max_length=EMAIL_MAX_LEN)]
    password: Annotated[str, StringConstraints(max_length=PASSWORD_MAX_CHARS)]


class LoginResponse(BaseModel):
    message: str | None = None
    totp_required: bool = False
    pending_token: str | None = None


class TotpChallengeRequest(BaseModel):
    pending_token: Annotated[str, StringConstraints(max_length=512)]
    code: Annotated[str, StringConstraints(max_length=32)]


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
    demo_available: bool = False
    totp_enrollment_required: bool = False


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Authenticate with email + password; set session cookie on success."""
    cfg = get_config()
    lockout = get_lockout_store()
    canonical = normalize_email(body.email)
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

    # TOTP 2FA branch: if the user has active TOTP enrollment, return a
    # time-limited pending token for the second factor instead of issuing a
    # full session cookie.  last_login_at is updated only when full auth
    # completes (POST /login/totp).
    if user_row.totp_secret is not None and user_row.totp_enabled_at is not None:
        return JSONResponse(
            content={
                "totp_required": True,
                "pending_token": encode_totp_pending_token(
                    cfg.session_secret, user_row.id
                ),
            },
            headers=NO_STORE,
        )

    user_row.last_login_at = datetime.now(UTC)

    # Opportunistic cleanup of expired/consumed oauth_attempts rows.
    await opportunistic_purge_oauth_attempts(db)

    # Resolve profile (use current default).
    factory = ActorContextFactory(db)
    actor = await factory.make(
        user_id=user_row.id,
        active_profile_id=user_row.default_profile_id,
        mode=cfg.auth_mode,
    )

    response = JSONResponse(
        content={"message": "Logged in"},
        headers=NO_STORE,
    )
    apply_session_cookie(
        response,
        cfg,
        SessionTicket(actor.user_id, actor.profile_id, user_row.session_version),
    )
    return response


@router.post("/login/totp", response_model=MessageResponse)
async def login_totp(
    request: Request,
    body: TotpChallengeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Complete a TOTP second-factor challenge issued by POST /login.

    The ``pending_token`` must be the value returned by a successful password
    verification within the last 5 minutes.  Accepts a 6-digit TOTP code or a
    10-char lowercase hex recovery code.  All failures return the same generic
    401 to prevent oracle attacks.
    """
    cfg = get_config()
    lockout = get_lockout_store()
    ip = get_client_ip(request)

    user_id = decode_totp_pending_token(cfg.session_secret, body.pending_token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication failed")

    user = await db.get(models.User, user_id)
    if (
        user is None
        or user.disabled_at is not None
        or user.totp_secret is None
        or user.totp_enabled_at is None
    ):
        raise HTTPException(status_code=401, detail="Authentication failed")

    if lockout.is_locked(user.canonical_email, ip):
        raise HTTPException(status_code=401, detail="Authentication failed")

    code = body.code.strip()
    ok: bool
    if is_totp_code(code):
        ok, step = verify_totp_code(user.totp_secret, code, user.totp_last_used_step)
        if ok:
            user.totp_last_used_step = step
    elif is_recovery_code(code.lower()):
        ok = await redeem_recovery_code(db, user.id, code.lower())
    else:
        ok = False

    if not ok:
        lockout.record_failure(user.canonical_email, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    lockout.record_success(user.canonical_email, ip)
    user.last_login_at = datetime.now(UTC)

    factory = ActorContextFactory(db)
    actor = await factory.make(
        user_id=user.id,
        active_profile_id=user.default_profile_id,
        mode=cfg.auth_mode,
    )

    response = JSONResponse(
        content={"message": "Logged in"},
        headers=NO_STORE,
    )
    apply_session_cookie(
        response,
        cfg,
        SessionTicket(actor.user_id, actor.profile_id, user.session_version),
    )
    return response


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    cfg = get_config()
    response = JSONResponse(content={"message": "Logged out"}, headers=NO_STORE)
    if cfg.is_multiuser:
        clear_session_cookie(response, secure=cfg.secure_cookie)
    return response


@router.post("/demo-login", response_model=MessageResponse)
async def demo_login(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Sign in as the demo account (read-only, no password required).

    Looks up the single active demo user and issues a session cookie.
    Returns 404 (generic) when no demo account is configured so callers
    cannot distinguish "demo user disabled" from "demo user absent".

    Only meaningful in multiuser mode — returns 404 in local mode (which has
    no session cookie and no per-role access control).
    """
    cfg = get_config()

    # Demo login is a multiuser concept; local mode has no session cookie.
    if not cfg.is_multiuser:
        raise HTTPException(status_code=404, detail="Demo not available")

    demo_user = (
        (
            await db.execute(
                select(models.User).where(
                    models.User.role == "demo",
                    models.User.disabled_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )

    if demo_user is None:
        raise HTTPException(status_code=404, detail="Demo not available")

    factory = ActorContextFactory(db)
    actor = await factory.make(
        user_id=demo_user.id,
        active_profile_id=demo_user.default_profile_id,
        mode=cfg.auth_mode,
    )
    demo_user.last_login_at = datetime.now(UTC)

    response = JSONResponse(
        content={"message": "Logged in as demo"},
        headers=NO_STORE,
    )
    apply_session_cookie(
        response,
        cfg,
        SessionTicket(actor.user_id, actor.profile_id, demo_user.session_version),
    )
    return response


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return authentication state and profile list for the current session."""
    from snore.services.demo_service import DemoService  # noqa: PLC0415

    cfg = get_config()

    demo_available = False
    if cfg.is_multiuser:
        # Cache True permanently in process: a True result never reverts within a
        # process (a stale True after a DB-file swap only degrades to a harmless 404
        # on demo-login). We never cache False so a demo user created later by the
        # scrub-demo CLI is picked up without restart.
        if getattr(request.app.state, "demo_available", False):
            demo_available = True
        else:
            demo_available = await DemoService(db).demo_user_exists()
            if demo_available:
                request.app.state.demo_available = True

    actor: ActorContext | None = getattr(request.state, "actor", None)

    if actor is None:
        return JSONResponse(
            content=AuthStatusResponse(
                authenticated=False,
                auth_mode=cfg.auth_mode.value,
                demo_available=demo_available,
            ).model_dump(),
            headers=NO_STORE,
        )

    user = await db.get(models.User, actor.user_id)
    if user is None:
        return JSONResponse(
            content=AuthStatusResponse(
                authenticated=False,
                auth_mode=cfg.auth_mode.value,
                demo_available=demo_available,
            ).model_dump(),
            headers=NO_STORE,
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
            demo_available=demo_available,
            totp_enrollment_required=actor.enrollment_required,
        ).model_dump(),
        headers=NO_STORE,
    )


@router.post("/active-profile", response_model=MessageResponse)
async def set_active_profile(
    request: Request,
    body: ActiveProfileRequest,
    actor: RequireAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Switch the active profile; re-validates ownership."""
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
        headers=NO_STORE,
    )
    apply_session_cookie(
        response,
        cfg,
        SessionTicket(actor.user_id, body.profile_id, user.session_version),
    )
    return response
