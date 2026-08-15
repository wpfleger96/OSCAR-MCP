"""Admin user-management router: user listing, patching, disable/enable, invite lifecycle, and MCP status.

Routes
------
GET    /users
PATCH  /users/{user_id}
POST   /users/{user_id}/disable
POST   /users/{user_id}/enable
POST   /invites
GET    /invites
DELETE /invites/{invite_id}
GET    /mcp/status
GET    /mcp/google-bindings
DELETE /mcp/google-bindings
DELETE /mcp/google-bindings/{user_id}

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

import logging
import secrets

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.constants import NO_STORE
from snore.api.deps import get_db
from snore.api.guards import RequireAdmin
from snore.api.schemas import DISPLAY_NAME_MAX_LEN, MessageResponse
from snore.auth.emails import normalize_email
from snore.auth.invite import invite_valid_clauses
from snore.auth.invite_tokens import hash_invite_token
from snore.auth.lockout import get_lockout_store
from snore.auth.totp import is_totp_code, verify_totp_code
from snore.database import models
from snore.database.models import AuthIdentity

router = APIRouter()
logger = logging.getLogger(__name__)


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
    has_password: bool
    auth_providers: list[str]
    last_login_at: datetime | None
    totp_enabled: bool


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


class TotpResetRequest(BaseModel):
    """Optional request body for POST /users/{user_id}/totp/reset.

    When the calling admin has TOTP enabled, ``code`` is required and must be
    a valid 6-digit TOTP code for the admin's own second factor.
    """

    code: Annotated[str, StringConstraints(max_length=16)] | None = None


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
    # Registration is invite-only, so the table is bounded by deliberate admin
    # action and pagination is deliberately omitted.
    rows = (
        (await db.execute(select(models.User).order_by(models.User.id))).scalars().all()
    )

    # One query for all auth identities — no per-user queries.
    user_ids = [u.id for u in rows]
    providers_by_user: dict[int, list[str]] = {}
    if user_ids:
        identity_rows = (
            await db.execute(
                select(
                    models.AuthIdentity.user_id,
                    models.AuthIdentity.provider,
                ).where(models.AuthIdentity.user_id.in_(user_ids))
            )
        ).all()
        for uid, provider in identity_rows:
            bucket = providers_by_user.setdefault(uid, [])
            if provider not in bucket:
                bucket.append(provider)

    return [
        UserItem(
            id=u.id,
            email=u.canonical_email,
            display_name=u.display_name,
            role=u.role,
            disabled=u.disabled_at is not None,
            created_at=u.created_at,
            has_password=u.password_hash is not None,
            auth_providers=providers_by_user.get(u.id, []),
            last_login_at=u.last_login_at,
            totp_enabled=u.totp_enabled_at is not None,
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


@router.post("/users/{user_id}/totp/reset", response_model=MessageResponse)
async def reset_user_totp(
    request: Request,
    user_id: int,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: TotpResetRequest | None = None,
) -> JSONResponse:
    """Reset TOTP enrollment for a user, forcing re-enrollment on next login.

    Clears totp_secret, totp_enabled_at, and totp_last_used_step; deletes all
    recovery codes; bumps session_version to invalidate existing sessions.

    If the calling admin has TOTP enrolled, a valid 6-digit TOTP code for the
    admin's own second factor is required in ``{"code": "..."}``; the lockout
    pre-check and replay guard both apply.  Admins without TOTP enrolled may
    call the endpoint without a request body.

    Self-reset is allowed — it forces re-enrollment rather than lockout.
    """
    # Verify the calling admin's own second factor when they have TOTP enabled.
    admin_user = await db.get(models.User, actor.user_id)
    if admin_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if admin_user.totp_enabled_at is not None:
        ip = get_client_ip(request)
        lockout = get_lockout_store()

        if lockout.is_locked(admin_user.canonical_email, ip):
            raise HTTPException(status_code=403, detail="Authentication failed")

        submitted_code = body.code if body else None
        if not submitted_code or not is_totp_code(submitted_code):
            lockout.record_failure(admin_user.canonical_email, ip)
            raise HTTPException(status_code=403, detail="Authentication failed")

        ok, step = verify_totp_code(
            admin_user.totp_secret,  # type: ignore[arg-type]
            submitted_code,
            admin_user.totp_last_used_step,
        )
        if not ok:
            lockout.record_failure(admin_user.canonical_email, ip)
            raise HTTPException(status_code=403, detail="Authentication failed")

        admin_user.totp_last_used_step = step
        lockout.record_success(admin_user.canonical_email, ip)

    user = await db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_used_step = None
    user.session_version += 1

    await db.execute(
        delete(models.TotpRecoveryCode).where(
            models.TotpRecoveryCode.user_id == user_id
        )
    )

    logger.info("Admin id=%s reset TOTP for user id=%s", actor.user_id, user_id)

    return JSONResponse(
        content={"message": "TOTP reset"},
        headers=NO_STORE,
    )


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


# ---------------------------------------------------------------------------
# MCP status route
# ---------------------------------------------------------------------------


class McpStatus(BaseModel):
    enabled: bool
    endpoint_url: str | None  # f"{cfg.public_base_url.rstrip('/')}/mcp" when enabled
    transport: str | None  # "streamable-http" when enabled
    auth_provider: str | None  # "google" when enabled
    disabled_reason: str | None  # human-readable reason when not enabled
    linked_google_identities: (
        int  # count of auth_identities rows where provider="google"
    )


@router.get("/mcp/status", response_model=McpStatus)
async def get_mcp_status(
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> McpStatus:
    """Return the current MCP server status.

    Reports whether the embedded MCP streamable-HTTP server is active and, when
    disabled, the reason.  Also returns the count of Google-linked identities so
    admins can understand how many users can authenticate via MCP OAuth.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    linked_count: int = (
        await db.scalar(select(func.count()).where(AuthIdentity.provider == "google"))
    ) or 0

    if cfg.is_mcp_enabled:
        return McpStatus(
            enabled=True,
            endpoint_url=f"{cfg.public_base_url.rstrip('/')}/mcp",
            transport="streamable-http",
            auth_provider="google",
            disabled_reason=None,
            linked_google_identities=linked_count,
        )

    reason = "local mode" if not cfg.is_multiuser else "Google OAuth not configured"
    return McpStatus(
        enabled=False,
        endpoint_url=None,
        transport=None,
        auth_provider=None,
        disabled_reason=reason,
        linked_google_identities=linked_count,
    )


# ---------------------------------------------------------------------------
# MCP Google-binding routes
# ---------------------------------------------------------------------------


class GoogleBindingItem(BaseModel):
    user_id: int
    user_email: str
    display_name: str | None
    google_email: str | None
    linked_at: datetime
    has_password: bool


class ResetAllBindingsResponse(BaseModel):
    reset: int
    skipped: int


@router.get("/mcp/google-bindings", response_model=list[GoogleBindingItem])
async def list_google_bindings(
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[GoogleBindingItem]:
    """Return all Google-linked auth_identities with the owning user's details.

    One item per auth_identities row — a user with two Google identities appears
    twice.  Ordered by user email then linked_at for stable display.
    """
    rows = (
        await db.execute(
            select(
                AuthIdentity.user_id,
                models.User.canonical_email,
                models.User.display_name,
                models.User.password_hash.is_not(None).label("has_password"),
                AuthIdentity.email,
                AuthIdentity.created_at,
            )
            .join(models.User, AuthIdentity.user_id == models.User.id)
            .where(AuthIdentity.provider == "google")
            .order_by(models.User.canonical_email, AuthIdentity.created_at)
        )
    ).all()

    return [
        GoogleBindingItem(
            user_id=uid,
            user_email=user_email,
            display_name=display_name,
            google_email=google_email,
            linked_at=created_at,
            has_password=has_password,
        )
        for uid, user_email, display_name, has_password, google_email, created_at in rows
    ]


@router.delete("/mcp/google-bindings", response_model=ResetAllBindingsResponse)
async def reset_all_google_bindings(
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResetAllBindingsResponse:
    """Delete Google bindings for all users who have a password.

    Password-less users' bindings survive — removing their only sign-in method
    would lock them out.  The auth_identities row deletion immediately revokes MCP
    access (the MCP server resolves every request by row lookup); bumping
    session_version invalidates web session cookies.  google_link_disabled is
    intentionally left untouched; members re-link automatically at next sign-in.
    """
    rows = (
        await db.execute(
            select(
                models.User.id,
                models.User.password_hash.is_not(None).label("has_password"),
            )
            .join(
                AuthIdentity,
                (AuthIdentity.user_id == models.User.id)
                & (AuthIdentity.provider == "google"),
            )
            .distinct()
        )
    ).all()

    to_reset = [uid for uid, has_password in rows if has_password]
    skipped = sum(1 for _, has_password in rows if not has_password)

    if to_reset:
        await db.execute(
            delete(AuthIdentity).where(
                AuthIdentity.user_id.in_(to_reset),
                AuthIdentity.provider == "google",
            )
        )
        await db.execute(
            sa_update(models.User)
            .where(models.User.id.in_(to_reset))
            .values(session_version=models.User.session_version + 1)
        )

    logger.info(
        "Admin id=%s reset all Google bindings: reset=%d skipped=%d",
        actor.user_id,
        len(to_reset),
        skipped,
    )

    return ResetAllBindingsResponse(reset=len(to_reset), skipped=skipped)


@router.delete("/mcp/google-bindings/{user_id}", response_model=MessageResponse)
async def reset_google_binding(
    user_id: int,
    actor: RequireAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """Delete all Google bindings for a single user and invalidate their sessions.

    404 when the user does not exist or has no Google binding.
    409 when the user has no password — resetting would leave them with no
    sign-in method.  The auth_identities row deletion immediately revokes MCP
    access (the MCP server resolves every request by row lookup); bumping
    session_version invalidates web session cookies.  google_link_disabled is
    intentionally left untouched so a fresh binding can form at next Google
    sign-in (or via the account-page Connect flow for admins).
    """
    user = await db.get(models.User, user_id)
    has_binding = bool(
        await db.scalar(
            select(func.count()).where(
                AuthIdentity.user_id == user_id,
                AuthIdentity.provider == "google",
            )
        )
    )

    # Binding check precedes password check so a missing binding yields 404, not 409.
    if user is None or not has_binding:
        raise HTTPException(status_code=404, detail="No Google binding found for user")

    if user.password_hash is None:
        raise HTTPException(
            status_code=409,
            detail="User has no password; resetting would remove their only sign-in method",
        )

    result = await db.execute(
        delete(AuthIdentity).where(
            AuthIdentity.user_id == user_id,
            AuthIdentity.provider == "google",
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="No Google binding found for user")
    await db.execute(
        sa_update(models.User)
        .where(models.User.id == user_id)
        .values(session_version=models.User.session_version + 1)
    )

    logger.info(
        "Admin id=%s reset Google binding for user id=%s",
        actor.user_id,
        user_id,
    )

    return MessageResponse(message="Google binding reset")
