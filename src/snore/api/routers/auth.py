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

import base64
import hashlib
import hmac
import logging
import secrets

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.deps import get_db, get_raw_session
from snore.api.guards import RequireAuth
from snore.api.schemas import MessageResponse
from snore.auth.actor import ActorContext
from snore.auth.factory import ActorContextFactory
from snore.auth.google_oauth import OAuthError, fetch_google_id_token_claims
from snore.auth.invite import InviteRedemptionError
from snore.auth.invite_tokens import hash_invite_token
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
    """Delete stale oauth_attempts rows and return the count removed.

    Retains rows for 1 day after expiry/consumption to preserve replay-detection
    capability.  Caps each call at 1000 rows to bound lock hold time.

    Called from both the startup sweep in app.py and the opportunistic on-path
    cleanup below — single predicate definition.
    """
    from sqlalchemy import delete  # noqa: PLC0415

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
# Google OAuth helpers
# ---------------------------------------------------------------------------

# Cookie that binds an OAuth flow to the initiating browser session.
_PRE_AUTH_COOKIE = "snore_pre_auth"
# Google's OAuth 2.0 authorization endpoint.
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, S256_challenge)`` for PKCE (RFC 7636 §4.2)."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _oauth_failure_response() -> JSONResponse:
    """Generic 400 returned on any OAuth flow failure.  Never reveals reason."""
    return JSONResponse(
        content={"detail": "Authentication failed"},
        status_code=400,
        headers=_NO_STORE,
    )


def _ensure_pre_auth_cookie(
    request: Request,
    cfg: object,
    response: RedirectResponse,
) -> str:
    """Return the pre-auth cookie value; set a new one on *response* if absent.

    Reuses an existing cookie without rotating it so concurrent tabs share one
    hash and parallel flows both stay valid.
    """
    from snore.api.config import AppConfig  # noqa: PLC0415

    assert isinstance(cfg, AppConfig)
    existing = request.cookies.get(_PRE_AUTH_COOKIE)
    if existing:
        return existing
    value = secrets.token_hex(32)
    response.set_cookie(
        _PRE_AUTH_COOKIE,
        value,
        max_age=cfg.pre_auth_cookie_ttl_seconds,
        path="/",
        secure=cfg.secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return value


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


class GoogleInviteInitRequest(BaseModel):
    token: Annotated[str, StringConstraints(max_length=_TOKEN_MAX_LEN)]


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


class _TxFailure(Exception):
    """Internal signal — raised inside a transaction context to force rollback.

    Caught immediately outside the ``async with db.begin()`` block and
    translated to ``_oauth_failure_response()``.  Never propagates further.
    """


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
    from snore.api.config import get_config  # noqa: PLC0415

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

    response = JSONResponse(
        content={"message": "Logged in as demo"},
        headers=_NO_STORE,
    )
    if cfg.is_multiuser:
        set_session_cookie(
            response,
            secret=cfg.session_secret,
            user_id=actor.user_id,
            active_profile_id=actor.profile_id,
            session_version=demo_user.session_version,
            secure=cfg.secure_cookie,
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


# ---------------------------------------------------------------------------
# Google OAuth routes
# ---------------------------------------------------------------------------


@router.get("/google/login")
async def google_login(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Initiate a Google OAuth login flow (login-only; never provisions accounts).

    Issues a ``snore_pre_auth`` browser-binding cookie when absent and inserts
    an ``oauth_attempts`` row, then redirects the browser to Google.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    if not cfg.is_google_configured:
        from fastapi.responses import JSONResponse as _JR  # noqa: PLC0415

        return _JR(  # type: ignore[return-value]
            content={"detail": "Google login not configured"},
            status_code=503,
            headers=_NO_STORE,
        )

    state = secrets.token_hex(32)
    nonce = secrets.token_hex(16)
    verifier, challenge = _pkce_pair()
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=cfg.oauth_attempt_ttl_seconds)
    redirect_uri = cfg.public_base_url.rstrip("/") + "/api/v1/auth/google/callback"

    # Build the redirect response first so we can set the cookie on it.
    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = _GOOGLE_AUTH_URL + "?" + urlencode(params)
    response: RedirectResponse = RedirectResponse(url=auth_url, status_code=302)

    pre_auth_value = _ensure_pre_auth_cookie(request, cfg, response)
    browser_session_hash = hashlib.sha256(pre_auth_value.encode()).hexdigest()

    db.add(
        models.OauthAttempt(
            state=state,
            kind="login",
            nonce=nonce,
            pkce_verifier=verifier,
            browser_session_hash=browser_session_hash,
            expires_at=expires_at,
        )
    )
    await db.flush()

    await _opportunistic_purge_oauth_attempts(db)
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_raw_session)],
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Handle Google OAuth callback for login-only flows.

    Validates browser binding, exchanges code, looks up the linked identity,
    and sets a session cookie before redirecting to ``/dashboard``.

    Uses two transaction windows so no DB connection is held during Google I/O:
    Window 1 reads and validates the attempt; Window 2 consumes and resolves.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    if not cfg.is_google_configured:
        from fastapi.responses import JSONResponse as _JR  # noqa: PLC0415

        return _JR(  # type: ignore[return-value]
            content={"detail": "Google login not configured"},
            status_code=503,
            headers=_NO_STORE,
        )

    if error or not state or not code:
        return _oauth_failure_response()  # type: ignore[return-value]

    # Window 1: short read — identify and validate attempt.
    async with db.begin():
        attempt = (
            (
                await db.execute(
                    select(models.OauthAttempt).where(
                        models.OauthAttempt.state == state,
                        models.OauthAttempt.kind == "login",
                        models.OauthAttempt.consumed_at.is_(None),
                        models.OauthAttempt.expires_at > datetime.now(UTC),
                    )
                )
            )
            .scalars()
            .first()
        )
        if attempt is None:
            return _oauth_failure_response()  # type: ignore[return-value]

        # Capture what we need before the transaction closes.
        attempt_state = attempt.state
        attempt_pkce_verifier = attempt.pkce_verifier or ""
        attempt_nonce = attempt.nonce or ""
        attempt_browser_hash = attempt.browser_session_hash or ""
    # Transaction committed and released here.

    # Local ops (no DB): validate browser binding (constant-time comparison).
    pre_auth_value = request.cookies.get(_PRE_AUTH_COOKIE, "")
    cookie_hash = hashlib.sha256(pre_auth_value.encode()).hexdigest()
    if not hmac.compare_digest(cookie_hash, attempt_browser_hash):
        return _oauth_failure_response()  # type: ignore[return-value]

    # Network I/O (no transaction held): exchange authorization code.
    redirect_uri = cfg.public_base_url.rstrip("/") + "/api/v1/auth/google/callback"
    try:
        claims = await fetch_google_id_token_claims(
            code=code,
            code_verifier=attempt_pkce_verifier,
            redirect_uri=redirect_uri,
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            expected_nonce=attempt_nonce,
        )
    except OAuthError as e:
        logger.warning("OAuth error in google_callback: %s", e)
        return _oauth_failure_response()  # type: ignore[return-value]

    # Window 2: fresh transaction — consume + resolve.
    # Failures raise _TxFailure inside the context so SQLAlchemy rolls back
    # rather than committing partial state on a normal return.
    try:
        async with db.begin():
            now = datetime.now(UTC)
            # Conditional consume — guards against replay and post-I/O expiry.
            consume_result = await db.execute(
                sa_update(models.OauthAttempt)
                .where(
                    models.OauthAttempt.state == attempt_state,
                    models.OauthAttempt.kind == "login",
                    models.OauthAttempt.consumed_at.is_(None),
                    models.OauthAttempt.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if int(consume_result.rowcount) == 0:  # type: ignore[attr-defined]
                raise _TxFailure()

            # Look up the linked Google identity — do NOT provision on this path.
            sub = claims["sub"]
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
                raise _TxFailure()

            user = await db.get(models.User, identity.user_id)
            if user is None or user.disabled_at is not None:
                raise _TxFailure()

            # Build session and redirect.
            factory = ActorContextFactory(db)
            actor = await factory.make(
                user_id=user.id,
                active_profile_id=user.default_profile_id,
                mode=cfg.auth_mode,
            )
            resp: RedirectResponse = RedirectResponse(url="/dashboard", status_code=302)
            set_session_cookie(
                resp,
                secret=cfg.session_secret,
                user_id=actor.user_id,
                active_profile_id=actor.profile_id,
                session_version=user.session_version,
                secure=cfg.secure_cookie,
            )
            return resp
    except _TxFailure:
        return _oauth_failure_response()  # type: ignore[return-value]


@router.post("/invites/google")
async def google_invite_initiate(
    request: Request,
    body: GoogleInviteInitRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Initiate a Google OAuth signup flow for an invite token.

    Token is in the request body — never in the URL — so it never appears
    in Uvicorn access logs.  Returns JSON with an ``authorization_url`` that
    the client should redirect to.

    Rate-limited per (token_hash, client IP) to slow down token probing.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    if not cfg.is_google_configured:
        return JSONResponse(
            content={"detail": "Google login not configured"},
            status_code=503,
            headers=_NO_STORE,
        )

    token = body.token.strip()
    if not token:
        return JSONResponse(
            content={"detail": "Invalid invite"},
            status_code=400,
            headers=_NO_STORE,
        )

    ip = get_client_ip(request)
    lockout = get_invite_lockout_store()
    token_hash = hash_invite_token(token)

    if lockout.is_locked(token_hash, ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    now = datetime.now(UTC)
    invite = (
        (
            await db.execute(
                select(models.Invite).where(
                    models.Invite.token_hash == token_hash,
                    models.Invite.redeemed_at.is_(None),
                    models.Invite.revoked_at.is_(None),
                    models.Invite.expires_at > now,
                )
            )
        )
        .scalars()
        .first()
    )

    if invite is None:
        lockout.record_failure(token_hash, ip)
        return JSONResponse(
            content={"detail": "Invalid invite"},
            status_code=400,
            headers=_NO_STORE,
        )

    state = secrets.token_hex(32)
    nonce = secrets.token_hex(16)
    verifier, challenge = _pkce_pair()
    expires_at = now + timedelta(seconds=cfg.oauth_attempt_ttl_seconds)
    redirect_uri = (
        cfg.public_base_url.rstrip("/") + "/api/v1/auth/google/invite-callback"
    )

    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": invite.email,
    }
    auth_url = _GOOGLE_AUTH_URL + "?" + urlencode(params)

    # Handle pre-auth cookie: reuse existing or generate a new one.
    existing_cookie = request.cookies.get(_PRE_AUTH_COOKIE)
    pre_auth_value = existing_cookie if existing_cookie else secrets.token_hex(32)
    browser_session_hash = hashlib.sha256(pre_auth_value.encode()).hexdigest()

    db.add(
        models.OauthAttempt(
            state=state,
            kind="signup",
            invite_id=invite.id,
            expected_canonical_email=invite.email.lower().strip(),
            nonce=nonce,
            pkce_verifier=verifier,
            browser_session_hash=browser_session_hash,
            expires_at=expires_at,
        )
    )
    await db.flush()
    await _opportunistic_purge_oauth_attempts(db)

    response = JSONResponse(
        content={"authorization_url": auth_url},
        headers=_NO_STORE,
    )
    if not existing_cookie:
        response.set_cookie(
            _PRE_AUTH_COOKIE,
            pre_auth_value,
            max_age=cfg.pre_auth_cookie_ttl_seconds,
            path="/",
            secure=cfg.secure_cookie,
            httponly=True,
            samesite="lax",
        )
    return response


@router.get("/google/invite-callback")
async def google_invite_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_raw_session)],
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Handle Google OAuth callback for invite-based signup flows.

    Validates browser binding, exchanges code, checks email matches invite,
    and resolves the account (link existing | create new), then redirects
    to ``/dashboard``.

    Uses two transaction windows so no DB connection is held during Google I/O:
    Window 1 reads the attempt and invite (capturing role); Window 2 consumes
    and resolves.

    Resolution order:
    a. Auth identity (provider=google, sub) already exists → login, leave invite.
    b. User with matching canonical email exists → link identity, consume invite.
    c. Neither → create user + profile + identity, consume invite.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()

    if not cfg.is_google_configured:
        from fastapi.responses import JSONResponse as _JR  # noqa: PLC0415

        return _JR(  # type: ignore[return-value]
            content={"detail": "Google login not configured"},
            status_code=503,
            headers=_NO_STORE,
        )

    if error or not state or not code:
        return _oauth_failure_response()  # type: ignore[return-value]

    # Window 1: short read — identify attempt and fetch invite role.
    async with db.begin():
        attempt = (
            (
                await db.execute(
                    select(models.OauthAttempt).where(
                        models.OauthAttempt.state == state,
                        models.OauthAttempt.kind == "signup",
                        models.OauthAttempt.consumed_at.is_(None),
                        models.OauthAttempt.expires_at > datetime.now(UTC),
                    )
                )
            )
            .scalars()
            .first()
        )
        if attempt is None:
            return _oauth_failure_response()  # type: ignore[return-value]

        # Capture needed fields before the transaction closes.
        attempt_state = attempt.state
        attempt_pkce_verifier = attempt.pkce_verifier or ""
        attempt_nonce = attempt.nonce or ""
        attempt_browser_hash = attempt.browser_session_hash or ""
        attempt_expected_email = attempt.expected_canonical_email or ""
        invite_id = attempt.invite_id

        # Reject null invite_id — signup attempts must always reference an invite.
        if invite_id is None:
            return _oauth_failure_response()  # type: ignore[return-value]

        # Read the invite with full validity check to capture the role.
        now_invite = datetime.now(UTC)
        invite_row = (
            (
                await db.execute(
                    select(models.Invite).where(
                        models.Invite.id == invite_id,
                        models.Invite.redeemed_at.is_(None),
                        models.Invite.revoked_at.is_(None),
                        models.Invite.expires_at > now_invite,
                    )
                )
            )
            .scalars()
            .first()
        )
        if invite_row is None:
            return _oauth_failure_response()  # type: ignore[return-value]
        invite_role = invite_row.role
    # Transaction committed and released here.

    # Local ops (no DB): validate browser binding.
    pre_auth_value = request.cookies.get(_PRE_AUTH_COOKIE, "")
    cookie_hash = hashlib.sha256(pre_auth_value.encode()).hexdigest()
    if not hmac.compare_digest(cookie_hash, attempt_browser_hash):
        return _oauth_failure_response()  # type: ignore[return-value]

    # Network I/O (no transaction held): exchange code and validate ID token.
    redirect_uri = (
        cfg.public_base_url.rstrip("/") + "/api/v1/auth/google/invite-callback"
    )
    try:
        claims = await fetch_google_id_token_claims(
            code=code,
            code_verifier=attempt_pkce_verifier,
            redirect_uri=redirect_uri,
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            expected_nonce=attempt_nonce,
        )
    except OAuthError as e:
        logger.warning("OAuth error in google_invite_callback: %s", e)
        return _oauth_failure_response()  # type: ignore[return-value]

    # Local: verify the Google account email matches the invite email.
    google_email_canonical = claims.get("email", "").lower().strip()
    if google_email_canonical != attempt_expected_email:
        return _oauth_failure_response()  # type: ignore[return-value]

    sub = claims["sub"]
    google_email_raw = claims.get("email", "")

    # Window 2: fresh transaction — consume attempt + resolve account.
    # Failures raise _TxFailure so the context manager rolls back rather than
    # committing partial state on a normal return.
    try:
        async with db.begin():
            now = datetime.now(UTC)

            # Step 1: Consume attempt — guards against replay and post-I/O expiry.
            consume_result = await db.execute(
                sa_update(models.OauthAttempt)
                .where(
                    models.OauthAttempt.state == attempt_state,
                    models.OauthAttempt.kind == "signup",
                    models.OauthAttempt.consumed_at.is_(None),
                    models.OauthAttempt.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if int(consume_result.rowcount) == 0:  # type: ignore[attr-defined]
                raise _TxFailure()

            # Path a: identity already linked → login (leave invite unconsumed).
            existing_identity = (
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
            if existing_identity is not None:
                user = await db.get(models.User, existing_identity.user_id)
                if user is None or user.disabled_at is not None:
                    raise _TxFailure()
                factory = ActorContextFactory(db)
                try:
                    actor = await factory.make(
                        user_id=user.id,
                        active_profile_id=user.default_profile_id,
                        mode=cfg.auth_mode,
                    )
                except ValueError as exc:
                    raise _TxFailure() from exc
                resp: RedirectResponse = RedirectResponse(
                    url="/dashboard", status_code=302
                )
                set_session_cookie(
                    resp,
                    secret=cfg.session_secret,
                    user_id=actor.user_id,
                    active_profile_id=actor.profile_id,
                    session_version=user.session_version,
                    secure=cfg.secure_cookie,
                )
                return resp

            # Paths B/C: consume invite FIRST — before any account state mutations.
            # Any failure here rolls back the attempt consume too.
            invite_result = await db.execute(
                sa_update(models.Invite)
                .where(
                    models.Invite.id == invite_id,
                    models.Invite.redeemed_at.is_(None),
                    models.Invite.revoked_at.is_(None),
                    models.Invite.expires_at > now,
                )
                .values(redeemed_at=now)
            )
            if int(invite_result.rowcount) == 0:  # type: ignore[attr-defined]
                raise _TxFailure()

            # Path b: user with matching email → link identity.
            existing_user = (
                (
                    await db.execute(
                        select(models.User).where(
                            models.User.canonical_email == google_email_canonical
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing_user is not None:
                if existing_user.disabled_at is not None:
                    raise _TxFailure()
                db.add(
                    models.AuthIdentity(
                        user_id=existing_user.id,
                        provider="google",
                        subject=sub,
                        email=google_email_raw,
                    )
                )
                factory = ActorContextFactory(db)
                try:
                    actor = await factory.make(
                        user_id=existing_user.id,
                        active_profile_id=existing_user.default_profile_id,
                        mode=cfg.auth_mode,
                    )
                except ValueError as exc:
                    raise _TxFailure() from exc
                resp = RedirectResponse(url="/dashboard", status_code=302)
                set_session_cookie(
                    resp,
                    secret=cfg.session_secret,
                    user_id=actor.user_id,
                    active_profile_id=actor.profile_id,
                    session_version=existing_user.session_version,
                    secure=cfg.secure_cookie,
                )
                return resp

            # Path c: create new user + profile + identity.
            new_user = models.User(
                canonical_email=google_email_canonical,
                role=invite_role,
                session_version=0,
            )
            db.add(new_user)
            await db.flush()

            profile = models.Profile(user_id=new_user.id, name="Default")
            db.add(profile)
            await db.flush()

            new_user.default_profile_id = profile.id
            db.add(
                models.AuthIdentity(
                    user_id=new_user.id,
                    provider="google",
                    subject=sub,
                    email=google_email_raw,
                )
            )

            resp = RedirectResponse(url="/dashboard", status_code=302)
            set_session_cookie(
                resp,
                secret=cfg.session_secret,
                user_id=new_user.id,
                active_profile_id=profile.id,
                session_version=0,
                secure=cfg.secure_cookie,
            )
            return resp
    except _TxFailure:
        return _oauth_failure_response()  # type: ignore[return-value]
