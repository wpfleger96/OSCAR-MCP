"""Admin user-management router: user listing, patching, disable/enable, and invite lifecycle.

Routes
------
GET    /users
PATCH  /users/{user_id}
POST   /users/{user_id}/disable
POST   /users/{user_id}/enable
POST   /invites
GET    /invites
DELETE /invites/{invite_id}

All routes require admin role.  Registered at prefix /api/v1/admin by app.py.
This prefix is intentionally outside the /api/v1/auth rate-limit scope;
admin session authentication and CSRF are the relevant controls here.

Security controls
-----------------
- All routes guarded by RequireAdmin (401 unauthenticated, 403 non-admin).
- CSRF origin check is handled by the existing AuthPathMiddleware covering /api/v1.
- Invite creation response carries Cache-Control: no-store (raw token in body,
  shown once).
- Last-admin guard prevents demoting the sole active admin.
- Self-disable guard prevents an admin from locking themselves out.
"""

from __future__ import annotations

import secrets

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.constants import NO_STORE
from snore.api.deps import get_db
from snore.api.guards import RequireAdmin
from snore.api.schemas import DISPLAY_NAME_MAX_LEN, MessageResponse
from snore.auth.emails import normalize_email
from snore.auth.invite import invite_valid_clauses
from snore.auth.invite_tokens import hash_invite_token
from snore.database import models

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class UserItem(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str
    disabled: bool
    created_at: datetime


class PatchUserRequest(BaseModel):
    """Partial update for a user record.

    At least one field must be provided.  display_name accepts None to clear
    the stored value.  role must be one of the allowed literals when provided;
    supplying role=null alone is treated the same as an empty body (422).
    """

    display_name: Annotated[
        str | None, StringConstraints(max_length=DISPLAY_NAME_MAX_LEN)
    ] = None
    role: Literal["admin", "member", "demo"] | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> PatchUserRequest:
        has_display_name = "display_name" in self.model_fields_set
        has_role = "role" in self.model_fields_set and self.role is not None
        if not has_display_name and not has_role:
            raise ValueError("At least one of display_name or role must be provided")
        return self


class CreateInviteRequest(BaseModel):
    email: Annotated[str, StringConstraints(max_length=254)]
    role: Literal["admin", "member"] = "member"
    ttl_days: int = Field(default=7, ge=1, le=30)

    @model_validator(mode="after")
    def validate_email(self) -> CreateInviteRequest:
        normalized = normalize_email(self.email)
        if not normalized or "@" not in normalized:
            raise ValueError("Invalid email address")
        return self


class InviteCreatedResponse(BaseModel):
    id: int
    email: str
    role: str
    invite_url: str
    expires_at: datetime


class InviteItem(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    created_by_id: int | None


# ---------------------------------------------------------------------------
# User routes
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserItem])
async def list_users(
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserItem]:
    """Return all users (including disabled) ordered by id."""
    rows = (
        (await db.execute(select(models.User).order_by(models.User.id))).scalars().all()
    )
    return [
        UserItem(
            id=u.id,
            email=u.canonical_email,
            display_name=u.display_name,
            role=u.role,
            disabled=u.disabled_at is not None,
            created_at=u.created_at,
        )
        for u in rows
    ]


@router.patch("/users/{user_id}", response_model=MessageResponse)
async def patch_user(
    user_id: int,
    body: PatchUserRequest,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Update a user's display_name and/or role.

    Role changes bump the target's session_version to invalidate existing cookies.
    Demoting the last active admin is rejected with 409.
    """
    user = await db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    bump_version = False

    if "role" in body.model_fields_set and body.role is not None:
        new_role = body.role
        if new_role != user.role:
            # Last-admin guard (pre-check): fast path for the common case.
            if (
                user.role == "admin"
                and user.disabled_at is None
                and new_role != "admin"
            ):
                other_admin_count = (
                    await db.execute(
                        select(func.count()).where(
                            models.User.role == "admin",
                            models.User.disabled_at.is_(None),
                            models.User.id != user_id,
                        )
                    )
                ).scalar()
                if other_admin_count == 0:
                    raise HTTPException(
                        status_code=409, detail="Cannot demote the last admin"
                    )
            user.role = new_role
            bump_version = True

            # Post-write check: guards against TOCTOU race where a concurrent
            # demotion completes between our pre-check and this write.
            admin_count_after = (
                await db.execute(
                    select(func.count()).where(
                        models.User.role == "admin",
                        models.User.disabled_at.is_(None),
                    )
                )
            ).scalar()
            if admin_count_after == 0:
                raise HTTPException(
                    status_code=409, detail="Cannot demote the last admin"
                )

    if "display_name" in body.model_fields_set:
        dn = body.display_name
        if dn is not None:
            dn = dn.strip() or None
        user.display_name = dn

    if bump_version:
        user.session_version += 1

    return MessageResponse(message="User updated")


@router.post("/users/{user_id}/disable", response_model=MessageResponse)
async def disable_user(
    user_id: int,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Disable a user account, invalidating all existing sessions.

    Idempotent when the user is already disabled.  Rejects self-disable with 409.
    """
    user = await db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if actor.user_id == user_id:
        raise HTTPException(status_code=409, detail="Cannot disable your own account")

    if user.disabled_at is not None:
        return MessageResponse(message="User is already disabled")

    user.disabled_at = datetime.now(UTC)
    user.session_version += 1
    return MessageResponse(message="User disabled")


@router.post("/users/{user_id}/enable", response_model=MessageResponse)
async def enable_user(
    user_id: int,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Re-enable a previously disabled user account.

    Idempotent when the user is already active.  Note: enabling does not
    restore old sessions — disable bumped session_version, so the user
    must log in again to obtain a fresh cookie.
    """
    user = await db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user.disabled_at is None:
        return MessageResponse(message="User is already enabled")

    user.disabled_at = None
    return MessageResponse(message="User enabled")


# ---------------------------------------------------------------------------
# Invite routes
# ---------------------------------------------------------------------------


@router.post("/invites", status_code=201, response_model=InviteCreatedResponse)
async def create_invite(
    body: CreateInviteRequest,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Create an invite link for a new user.

    Returns 201 with Cache-Control: no-store because the raw token appears in
    the response body and must not be cached by proxies or browsers.  The token
    is embedded in the URL fragment (never the path) so it never enters server
    access logs.  It is shown once — it is not stored.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    canonical = normalize_email(body.email)
    raw = secrets.token_urlsafe(32)
    token_hash = hash_invite_token(raw)
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=body.ttl_days)

    invite = models.Invite(
        email=canonical,
        token_hash=token_hash,
        role=body.role,
        created_by=actor.user_id,
        expires_at=expires_at,
    )
    db.add(invite)
    await db.flush()

    # Build the invite URL; fall back to a root-relative path when
    # public_base_url is absent (local mode).
    base = cfg.public_base_url.rstrip("/") if cfg.public_base_url else ""
    invite_url = f"{base}/invite#{raw}" if base else f"/invite#{raw}"

    content = InviteCreatedResponse(
        id=invite.id,
        email=canonical,
        role=body.role,
        invite_url=invite_url,
        expires_at=expires_at,
    ).model_dump(mode="json")

    return JSONResponse(content=content, status_code=201, headers=NO_STORE)


@router.get("/invites", response_model=list[InviteItem])
async def list_invites(
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[InviteItem]:
    """Return pending invites (not redeemed, not revoked, not expired)."""
    now = datetime.now(UTC)
    rows = (
        (await db.execute(select(models.Invite).where(*invite_valid_clauses(now))))
        .scalars()
        .all()
    )
    return [
        InviteItem(
            id=inv.id,
            email=inv.email,
            role=inv.role,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
            created_by_id=inv.created_by,
        )
        for inv in rows
    ]


@router.delete("/invites/{invite_id}", response_model=MessageResponse)
async def revoke_invite(
    invite_id: int,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Revoke a pending invite.

    Returns 409 when the invite is already redeemed, revoked, or expired.
    """
    invite = await db.get(models.Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")

    now = datetime.now(UTC)
    if (
        invite.redeemed_at is not None
        or invite.revoked_at is not None
        or invite.expires_at <= now
    ):
        raise HTTPException(status_code=409, detail="Invite is not pending")

    invite.revoked_at = now
    return MessageResponse(message="Invite revoked")
