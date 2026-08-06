"""Google OAuth routes: login, invite signup initiation, and callbacks.

Routes
------
GET  /api/v1/auth/google/login
GET  /api/v1/auth/google/callback
POST /api/v1/auth/invites/google   (invite token in request body)
GET  /api/v1/auth/google/invite-callback

Security controls
-----------------
- All flow state (state, nonce, PKCE verifier) is server-side in the
  ``oauth_attempts`` table; the browser carries only the opaque
  ``snore_pre_auth`` binding cookie.
- Callbacks use two transaction windows so no DB connection is held during
  Google network I/O; window 2 consumes the attempt via a conditional UPDATE
  (replay protection) and rolls back on any failure via ``_TxFailure``.
- Every OAuth failure returns the identical generic 400 — no state oracle.
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.config import AppConfig, get_config
from snore.api.deps import get_db, get_raw_session
from snore.api.routers.auth._common import (
    _NO_STORE,
    _TOKEN_MAX_LEN,
    opportunistic_purge_oauth_attempts,
)
from snore.auth.factory import ActorContextFactory
from snore.auth.google_oauth import OAuthError, fetch_google_id_token_claims
from snore.auth.invite import invite_valid_clauses
from snore.auth.invite_tokens import hash_invite_token
from snore.auth.lockout import get_invite_lockout_store
from snore.auth.session_cookie import set_session_cookie
from snore.database import models

logger = logging.getLogger(__name__)

router = APIRouter()

# Cookie that binds an OAuth flow to the initiating browser session.
_PRE_AUTH_COOKIE = "snore_pre_auth"
# Google's OAuth 2.0 authorization endpoint.
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class GoogleInviteInitRequest(BaseModel):
    token: Annotated[str, StringConstraints(max_length=_TOKEN_MAX_LEN)]


class _TxFailure(Exception):
    """Internal signal — raised inside a transaction context to force rollback.

    Caught immediately outside the ``async with db.begin()`` block and
    translated to ``_oauth_failure_response()``.  Never propagates further.
    """


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


def _google_not_configured_response() -> JSONResponse:
    return JSONResponse(
        content={"detail": "Google login not configured"},
        status_code=503,
        headers=_NO_STORE,
    )


def _ensure_pre_auth_cookie(
    request: Request,
    cfg: AppConfig,
    response: Response,
) -> str:
    """Return the pre-auth cookie value; set a new one on *response* if absent.

    Reuses an existing cookie without rotating it so concurrent tabs share one
    hash and parallel flows both stay valid.
    """
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


@router.get("/google/login")
async def google_login(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Initiate a Google OAuth login flow (login-only; never provisions accounts).

    Issues a ``snore_pre_auth`` browser-binding cookie when absent and inserts
    an ``oauth_attempts`` row, then redirects the browser to Google.
    """
    cfg = get_config()

    if not cfg.is_google_configured:
        return _google_not_configured_response()

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

    await opportunistic_purge_oauth_attempts(db)
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_raw_session)],
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    """Handle Google OAuth callback for login-only flows.

    Validates browser binding, exchanges code, looks up the linked identity,
    and sets a session cookie before redirecting to ``/dashboard``.

    Uses two transaction windows so no DB connection is held during Google I/O:
    Window 1 reads and validates the attempt; Window 2 consumes and resolves.
    """
    cfg = get_config()

    if not cfg.is_google_configured:
        return _google_not_configured_response()

    if error or not state or not code:
        return _oauth_failure_response()

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
            return _oauth_failure_response()

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
        return _oauth_failure_response()

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
        return _oauth_failure_response()

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
        return _oauth_failure_response()


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
    cfg = get_config()

    if not cfg.is_google_configured:
        return _google_not_configured_response()

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
                    *invite_valid_clauses(now),
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
    await opportunistic_purge_oauth_attempts(db)

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
) -> Response:
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
    cfg = get_config()

    if not cfg.is_google_configured:
        return _google_not_configured_response()

    if error or not state or not code:
        return _oauth_failure_response()

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
            return _oauth_failure_response()

        # Capture needed fields before the transaction closes.
        attempt_state = attempt.state
        attempt_pkce_verifier = attempt.pkce_verifier or ""
        attempt_nonce = attempt.nonce or ""
        attempt_browser_hash = attempt.browser_session_hash or ""
        attempt_expected_email = attempt.expected_canonical_email or ""
        invite_id = attempt.invite_id

        # Reject null invite_id — signup attempts must always reference an invite.
        if invite_id is None:
            return _oauth_failure_response()

        # Read the invite with full validity check to capture the role.
        now_invite = datetime.now(UTC)
        invite_row = (
            (
                await db.execute(
                    select(models.Invite).where(
                        models.Invite.id == invite_id,
                        *invite_valid_clauses(now_invite),
                    )
                )
            )
            .scalars()
            .first()
        )
        if invite_row is None:
            return _oauth_failure_response()
        invite_role = invite_row.role
    # Transaction committed and released here.

    # Local ops (no DB): validate browser binding.
    pre_auth_value = request.cookies.get(_PRE_AUTH_COOKIE, "")
    cookie_hash = hashlib.sha256(pre_auth_value.encode()).hexdigest()
    if not hmac.compare_digest(cookie_hash, attempt_browser_hash):
        return _oauth_failure_response()

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
        return _oauth_failure_response()

    # Local: verify the Google account email matches the invite email.
    google_email_canonical = claims.get("email", "").lower().strip()
    if google_email_canonical != attempt_expected_email:
        return _oauth_failure_response()

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
                .where(models.Invite.id == invite_id, *invite_valid_clauses(now))
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
        return _oauth_failure_response()
