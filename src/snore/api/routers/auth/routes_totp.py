"""TOTP 2FA self-service enrollment and management for the authenticated user.

Routes (all prefixed /api/v1/auth/me/totp)
-------------------------------------------
GET    /api/v1/auth/me/totp                           — TOTP enrollment status
POST   /api/v1/auth/me/totp/setup                     — Begin enrollment (generate secret)
POST   /api/v1/auth/me/totp/confirm                   — Confirm enrollment with first code
DELETE /api/v1/auth/me/totp                           — Disable TOTP (password + code)
POST   /api/v1/auth/me/totp/recovery-codes/regenerate — Regenerate recovery codes

All responses carry ``Cache-Control: no-store``.  TOTP is only available in
multiuser mode; every endpoint returns 403 in local mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.config import get_config
from snore.api.constants import NO_STORE
from snore.api.deps import get_db
from snore.api.guards import RequireAuth, RequireWritable
from snore.api.routers.auth._common import (
    PASSWORD_MAX_CHARS,
    SessionTicket,
    apply_session_cookie,
)
from snore.api.schemas import MessageResponse
from snore.auth.lockout import get_lockout_store
from snore.auth.passwords import verify_password_async
from snore.auth.session_cookie import clear_session_cookie
from snore.auth.totp import (
    RECOVERY_CODE_COUNT,
    build_provisioning_uri,
    build_qr_data_uri,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    is_recovery_code,
    is_totp_code,
    redeem_recovery_code,
    verify_totp_code,
)
from snore.database import models

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TotpStatusResponse(BaseModel):
    enabled: bool
    enabled_at: datetime | None = None
    recovery_codes_remaining: int | None = None


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    qr_data_uri: str


class TotpConfirmRequest(BaseModel):
    code: Annotated[
        str,
        StringConstraints(min_length=6, max_length=6, pattern=r"^\d{6}$"),
    ]


class TotpConfirmResponse(BaseModel):
    recovery_codes: list[str]


class TotpDisableRequest(BaseModel):
    password: Annotated[str, StringConstraints(max_length=PASSWORD_MAX_CHARS)]
    code: Annotated[str, StringConstraints(max_length=32)]


class TotpRegenerateRequest(BaseModel):
    code: Annotated[str, StringConstraints(max_length=32)]


class TotpRegenerateResponse(BaseModel):
    recovery_codes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_multiuser() -> None:
    """Raise 403 when the server is running in local mode.

    TOTP is meaningless in local mode — the frontend never calls these
    endpoints there.
    """
    if not get_config().is_multiuser:
        raise HTTPException(status_code=403, detail="Not available in local mode")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=TotpStatusResponse)
async def totp_status(
    actor: RequireAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return the caller's TOTP enrollment status and remaining recovery code count."""
    _require_multiuser()

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    recovery_codes_remaining: int | None = None
    if user.totp_enabled_at is not None:
        count_result = await db.execute(
            select(func.count())
            .select_from(models.TotpRecoveryCode)
            .where(
                models.TotpRecoveryCode.user_id == actor.user_id,
                models.TotpRecoveryCode.used_at.is_(None),
            )
        )
        recovery_codes_remaining = count_result.scalar_one()

    return JSONResponse(
        content=TotpStatusResponse(
            enabled=user.totp_enabled_at is not None,
            enabled_at=user.totp_enabled_at,
            recovery_codes_remaining=recovery_codes_remaining,
        ).model_dump(mode="json"),
        headers=NO_STORE,
    )


@router.post("/setup", response_model=TotpSetupResponse)
async def totp_setup(
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Begin TOTP enrollment: generate a new secret and return setup material.

    Returns 409 if TOTP is already enabled.  Calling this again while a
    pending (unconfirmed) setup exists replaces the prior pending secret.
    """
    _require_multiuser()

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user.totp_enabled_at is not None:
        raise HTTPException(
            status_code=409, detail="Two-factor authentication is already enabled"
        )

    secret = generate_totp_secret()
    user.totp_secret = secret
    # Leave totp_enabled_at and totp_last_used_step None until /confirm succeeds.

    otpauth_uri = build_provisioning_uri(secret, user.canonical_email)
    qr_data_uri = build_qr_data_uri(otpauth_uri)

    return JSONResponse(
        content=TotpSetupResponse(
            secret=secret,
            otpauth_uri=otpauth_uri,
            qr_data_uri=qr_data_uri,
        ).model_dump(),
        headers=NO_STORE,
    )


@router.post("/confirm", response_model=TotpConfirmResponse)
async def totp_confirm(
    request: Request,
    body: TotpConfirmRequest,
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Confirm TOTP enrollment with the first successful code.

    Activates TOTP for the account, generates recovery codes, bumps
    ``session_version``, and re-issues the caller's session cookie.  Recovery
    codes are returned exactly once — they cannot be retrieved again.
    """
    _require_multiuser()

    cfg = get_config()
    lockout = get_lockout_store()
    ip = get_client_ip(request)

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user.totp_enabled_at is not None:
        raise HTTPException(
            status_code=409, detail="Two-factor authentication is already enabled"
        )
    if user.totp_secret is None:
        raise HTTPException(status_code=409, detail="No pending setup")

    ok, step = verify_totp_code(user.totp_secret, body.code, None)
    if not ok:
        lockout.record_failure(user.canonical_email, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    lockout.record_success(user.canonical_email, ip)

    now = datetime.now(UTC)
    user.totp_enabled_at = now
    user.totp_last_used_step = step

    # Replace any existing recovery codes (handles re-confirm after a prior
    # partial setup that somehow reached confirm twice).
    await db.execute(
        delete(models.TotpRecoveryCode).where(
            models.TotpRecoveryCode.user_id == user.id
        )
    )

    raw_codes = generate_recovery_codes(RECOVERY_CODE_COUNT)
    for raw in raw_codes:
        db.add(
            models.TotpRecoveryCode(
                user_id=user.id,
                code_hash=hash_recovery_code(raw),
            )
        )

    user.session_version += 1

    response = JSONResponse(
        content=TotpConfirmResponse(recovery_codes=raw_codes).model_dump(),
        headers=NO_STORE,
    )
    apply_session_cookie(
        response,
        cfg,
        SessionTicket(actor.user_id, actor.profile_id, user.session_version),
    )
    return response


@router.delete("", response_model=MessageResponse)
async def totp_disable(
    request: Request,
    body: TotpDisableRequest,
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Disable TOTP after verifying both the current password and a TOTP/recovery code.

    Clears all TOTP state and recovery codes, bumps ``session_version``, and
    clears the session cookie — the user must log in again.
    """
    _require_multiuser()

    cfg = get_config()
    lockout = get_lockout_store()
    ip = get_client_ip(request)

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user.totp_enabled_at is None:
        raise HTTPException(
            status_code=409, detail="Two-factor authentication is not enabled"
        )

    # Verify current password.
    if user.password_hash is None:
        lockout.record_failure(user.canonical_email, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    pw_ok, _ = await verify_password_async(user.password_hash, body.password)
    if not pw_ok:
        lockout.record_failure(user.canonical_email, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    # Verify TOTP or recovery code.
    code = body.code.strip()
    ok: bool
    if is_totp_code(code):
        ok, step = verify_totp_code(user.totp_secret, code, user.totp_last_used_step)  # type: ignore[arg-type]
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

    # Clear all TOTP state.
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_used_step = None

    await db.execute(
        delete(models.TotpRecoveryCode).where(
            models.TotpRecoveryCode.user_id == user.id
        )
    )

    user.session_version += 1

    response = JSONResponse(
        content={"message": "Two-factor authentication disabled"},
        headers=NO_STORE,
    )
    clear_session_cookie(response, secure=cfg.secure_cookie)
    return response


@router.post("/recovery-codes/regenerate", response_model=TotpRegenerateResponse)
async def totp_regenerate_recovery_codes(
    request: Request,
    body: TotpRegenerateRequest,
    actor: RequireWritable,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Regenerate recovery codes after verifying a TOTP code (not a recovery code).

    Returns 409 if TOTP is not enabled.  Only a live 6-digit TOTP code is
    accepted — recovery codes cannot mint new recovery codes.
    """
    _require_multiuser()

    lockout = get_lockout_store()
    ip = get_client_ip(request)

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user.totp_enabled_at is None:
        raise HTTPException(
            status_code=409, detail="Two-factor authentication is not enabled"
        )

    code = body.code.strip()
    if not is_totp_code(code):
        lockout.record_failure(user.canonical_email, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    ok, step = verify_totp_code(user.totp_secret, code, user.totp_last_used_step)  # type: ignore[arg-type]
    if not ok:
        lockout.record_failure(user.canonical_email, ip)
        raise HTTPException(status_code=401, detail="Authentication failed")

    lockout.record_success(user.canonical_email, ip)
    user.totp_last_used_step = step

    await db.execute(
        delete(models.TotpRecoveryCode).where(
            models.TotpRecoveryCode.user_id == user.id
        )
    )

    raw_codes = generate_recovery_codes(RECOVERY_CODE_COUNT)
    for raw in raw_codes:
        db.add(
            models.TotpRecoveryCode(
                user_id=user.id,
                code_hash=hash_recovery_code(raw),
            )
        )

    return JSONResponse(
        content=TotpRegenerateResponse(recovery_codes=raw_codes).model_dump(),
        headers=NO_STORE,
    )
