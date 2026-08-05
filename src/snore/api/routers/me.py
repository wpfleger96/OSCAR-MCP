"""Self-service account management for the authenticated user.

Routes
------
GET   /api/v1/auth/me
PATCH /api/v1/auth/me/display-name
POST  /api/v1/auth/me/password
GET   /api/v1/auth/me/preferences
PATCH /api/v1/auth/me/preferences

All responses carry ``Cache-Control: no-store`` to prevent credential
caching by proxies or browsers.

Security controls
-----------------
- Password change requires the existing password when one is set; rejects
  ``current_password`` when no password is set (Google-only accounts).
- Password change bumps ``session_version`` and re-issues the caller's
  session cookie so the change takes effect immediately without logout.
- Preference updates reject unknown fields (extra="forbid") to prevent
  silent key accumulation in the stored JSON blob.
- All mutating endpoints require ``RequireWritable``, which blocks demo
  role actors at the guard layer.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.deps import get_db
from snore.api.guards import RequireAuth, RequireWritable
from snore.auth.passwords import (
    hash_password_async,
    validate_password_bytes,
    verify_password_async,
)
from snore.auth.session_cookie import set_session_cookie
from snore.database import models

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}

_DISPLAY_NAME_MAX = 150
_PASSWORD_MAX_CHARS = (
    4096  # conservative char cap; byte validator refines to 1024 bytes
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    message: str


class MeResponse(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str
    has_password: bool


class DisplayNameRequest(BaseModel):
    display_name: Annotated[str, StringConstraints(max_length=_DISPLAY_NAME_MAX)] | None


class PasswordChangeRequest(BaseModel):
    current_password: (
        Annotated[str, StringConstraints(max_length=_PASSWORD_MAX_CHARS)] | None
    ) = None
    new_password: Annotated[str, StringConstraints(max_length=_PASSWORD_MAX_CHARS)]


class UserPreferences(BaseModel):
    landing_page: Literal["dashboard", "sessions", "days"] = "dashboard"
    date_format: Literal["iso", "locale", "short"] = "iso"


class UserPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    landing_page: Literal["dashboard", "sessions", "days"] | None = None
    date_format: Literal["iso", "locale", "short"] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=MeResponse)
async def get_me(
    actor: RequireAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return the authenticated user's account information."""
    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    return JSONResponse(
        content=MeResponse(
            id=user.id,
            email=user.canonical_email,
            display_name=user.display_name,
            role=user.role,
            has_password=user.password_hash is not None,
        ).model_dump(),
        headers=_NO_STORE,
    )


@router.patch("/display-name", response_model=MessageResponse)
async def update_display_name(
    actor: RequireWritable,
    body: DisplayNameRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Update the authenticated user's display name."""
    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Strip; empty-after-strip → None.
    name = body.display_name
    if name is not None:
        name = name.strip() or None
    user.display_name = name

    return JSONResponse(
        content={"message": "Display name updated"},
        headers=_NO_STORE,
    )


@router.post("/password", response_model=MessageResponse)
async def change_password(
    actor: RequireWritable,
    body: PasswordChangeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Change the authenticated user's password; bumps session_version and re-issues cookie."""
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    # Validate new password byte length before any DB work or KDF.
    try:
        validate_password_bytes(body.new_password)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Password must be 1–1024 bytes encoded"
        ) from None

    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user.password_hash is not None:
        # Existing password — current_password required.
        if body.current_password is None:
            raise HTTPException(status_code=422, detail="Current password required")
        ok, _ = await verify_password_async(user.password_hash, body.current_password)
        if not ok:
            raise HTTPException(status_code=401, detail="Authentication failed")
    else:
        # Google-only account — current_password must be absent/None.
        if body.current_password is not None:
            raise HTTPException(
                status_code=422,
                detail="No current password is set on this account",
            )

    user.password_hash = await hash_password_async(body.new_password)
    user.session_version += 1

    response = JSONResponse(
        content={"message": "Password updated"},
        headers=_NO_STORE,
    )
    if cfg.is_multiuser:
        set_session_cookie(
            response,
            secret=cfg.session_secret,
            user_id=actor.user_id,
            active_profile_id=actor.profile_id,
            session_version=user.session_version,
            secure=cfg.secure_cookie,
        )
    return response


@router.get("/preferences", response_model=UserPreferences)
async def get_preferences(
    actor: RequireAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Return the authenticated user's preferences, filling gaps with defaults."""
    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    prefs = UserPreferences(**(user.preferences or {}))
    return JSONResponse(
        content=prefs.model_dump(),
        headers=_NO_STORE,
    )


@router.patch("/preferences", response_model=UserPreferences)
async def update_preferences(
    actor: RequireWritable,
    body: UserPreferencesUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Merge supplied preferences into stored preferences; return the merged result."""
    user = await db.get(models.User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Load current state (defaults fill gaps, unknown stored keys stripped).
    current = UserPreferences(**(user.preferences or {}))

    # Apply only explicitly supplied (non-None) fields.
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    merged = current.model_copy(update=updates)
    user.preferences = merged.model_dump()

    return JSONResponse(
        content=merged.model_dump(),
        headers=_NO_STORE,
    )
