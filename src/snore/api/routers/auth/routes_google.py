"""Google OAuth routes: login and invite-signup initiation, single callback.

Routes
------
GET  /api/v1/auth/google/login
POST /api/v1/auth/invites/google   (invite token in request body)
GET  /api/v1/auth/google/callback  (single callback for both flow kinds)

Both flows authorize with the same redirect URI; the ``oauth_attempts`` row
created at initiation carries ``kind`` ("login" | "signup"), and the callback
dispatches on it after validating the attempt.

Security controls
-----------------
- All flow state (state, nonce, PKCE verifier) is server-side in the
  ``oauth_attempts`` table; the browser carries only the opaque
  ``snore_pre_auth`` binding cookie.
- The callback uses two transaction windows so no DB connection is held during
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

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.client_ip import get_client_ip
from snore.api.config import AppConfig, get_config
from snore.api.deps import get_db, get_raw_session
from snore.api.routers.auth._common import (
    _NO_STORE,
    _TOKEN_MAX_LEN,
    SessionTicket,
    issue_session_redirect,
    opportunistic_purge_oauth_attempts,
)
from snore.auth.factory import ActorContextFactory
from snore.auth.google_oauth import OAuthError, fetch_google_id_token_claims
from snore.auth.invite import invite_valid_clauses
from snore.auth.invite_tokens import hash_invite_token
from snore.auth.lockout import get_invite_lockout_store
from snore.database import models

logger = logging.getLogger(__name__)

router = APIRouter()

# Cookie that binds an OAuth flow to the initiating browser session.
_PRE_AUTH_COOKIE = "snore_pre_auth"
# Google's OAuth 2.0 authorization endpoint.
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# The single OAuth redirect URI path, shared by login and signup flows.
# ``{SNORE_PUBLIC_BASE_URL}{_CALLBACK_PATH}`` must be registered as an
# authorized redirect URI in the Google Cloud Console.
_CALLBACK_PATH = "/api/v1/auth/google/callback"


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


def _google_gate(cfg: AppConfig) -> JSONResponse | None:
    """503 unless Google flows are available: multiuser mode + credentials.

    Local mode has no session cookies or per-user accounts, so a Google login
    would be meaningless; the message stays identical to the unconfigured case
    to avoid a configuration oracle.
    """
    if cfg.is_multiuser and cfg.is_google_configured:
        return None
    return JSONResponse(
        content={"detail": "Google login not configured"},
        status_code=503,
        headers=_NO_STORE,
    )


@dataclass(frozen=True)
class _GoogleFlowStart:
    auth_url: str
    pre_auth_value: str
    new_cookie: bool  # False ⇒ an existing snore_pre_auth cookie was reused


async def _begin_google_flow(
    db: AsyncSession,
    cfg: AppConfig,
    request: Request,
    *,
    kind: str,
    invite_id: int | None = None,
    expected_canonical_email: str | None = None,
    login_hint: str | None = None,
) -> _GoogleFlowStart:
    """Create an ``oauth_attempts`` row and build the Google authorization URL.

    Owns everything the two initiation routes share: state/nonce generation,
    PKCE S256, browser binding, and opportunistic purge.  An existing pre-auth
    cookie is reused without rotation so concurrent tabs share one hash and
    parallel flows both stay valid; the caller sets the cookie on its response
    when ``new_cookie`` is True.
    """
    state = secrets.token_hex(32)
    nonce = secrets.token_hex(16)
    verifier, challenge = _pkce_pair()
    now = datetime.now(UTC)

    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": cfg.public_base_url.rstrip("/") + _CALLBACK_PATH,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if login_hint is not None:
        params["login_hint"] = login_hint

    existing = request.cookies.get(_PRE_AUTH_COOKIE)
    pre_auth_value = existing if existing else secrets.token_hex(32)

    db.add(
        models.OauthAttempt(
            state=state,
            kind=kind,
            invite_id=invite_id,
            expected_canonical_email=expected_canonical_email,
            nonce=nonce,
            pkce_verifier=verifier,
            browser_session_hash=hashlib.sha256(pre_auth_value.encode()).hexdigest(),
            expires_at=now + timedelta(seconds=cfg.oauth_attempt_ttl_seconds),
        )
    )
    await db.flush()
    await opportunistic_purge_oauth_attempts(db)

    return _GoogleFlowStart(
        auth_url=_GOOGLE_AUTH_URL + "?" + urlencode(params),
        pre_auth_value=pre_auth_value,
        new_cookie=existing is None,
    )


def _set_pre_auth_cookie(response: Response, cfg: AppConfig, value: str) -> None:
    response.set_cookie(
        _PRE_AUTH_COOKIE,
        value,
        max_age=cfg.pre_auth_cookie_ttl_seconds,
        path="/",
        secure=cfg.secure_cookie,
        httponly=True,
        samesite="lax",
    )


# ---------------------------------------------------------------------------
# Window-2 account resolution (all helpers run inside the callback's write
# transaction and raise _TxFailure on any failure — generic 400, full rollback)
# ---------------------------------------------------------------------------


async def _ticket_for(
    db: AsyncSession, cfg: AppConfig, user: models.User
) -> SessionTicket:
    """Resolve the user's profile and build a session ticket.

    Profile-resolution failures abort the transaction like every other flow
    failure (generic 400) instead of surfacing as a 500.
    """
    try:
        actor = await ActorContextFactory(db).make(
            user_id=user.id,
            active_profile_id=user.default_profile_id,
            mode=cfg.auth_mode,
        )
    except ValueError as exc:
        raise _TxFailure() from exc
    return SessionTicket(actor.user_id, actor.profile_id, user.session_version)


async def _linked_user_ticket(
    db: AsyncSession, cfg: AppConfig, sub: str
) -> SessionTicket | None:
    """Ticket for the user already linked to ``(google, sub)``.

    Returns None when no identity row exists; rejects disabled or missing
    users outright.
    """
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
        return None
    user = await db.get(models.User, identity.user_id)
    if user is None or user.disabled_at is not None:
        raise _TxFailure()
    return await _ticket_for(db, cfg, user)


async def _link_identity_ticket(
    db: AsyncSession, cfg: AppConfig, user: models.User, sub: str, email_raw: str
) -> SessionTicket:
    """Link a new Google identity to an existing user and log them in."""
    if user.disabled_at is not None:
        raise _TxFailure()
    db.add(
        models.AuthIdentity(
            user_id=user.id,
            provider="google",
            subject=sub,
            email=email_raw,
        )
    )
    # Audit trail: linking changes who can access the account without a
    # password, so operators need a record of when it happened.
    logger.info(
        "Linked new Google identity to user id=%s (role=%s)", user.id, user.role
    )
    return await _ticket_for(db, cfg, user)


async def _resolve_login(
    db: AsyncSession, cfg: AppConfig, claims: dict[str, object]
) -> SessionTicket:
    """Login-kind resolution: linked identity, else verified-email auto-link.

    A user with no Google identity (e.g. created via the password invite
    flow) is auto-linked when their canonical email matches the Google
    account's — the same trust link the invite signup path establishes, and
    safe because ``fetch_google_id_token_claims`` requires
    ``email_verified is True``.  Never provisions a new account.

    Admin accounts are excluded: control of a matching mailbox alone must
    not grant admin access.  Admins link Google deliberately via an invite
    addressed to their own email (signup path b).
    """
    sub = str(claims["sub"])
    ticket = await _linked_user_ticket(db, cfg, sub)
    if ticket is not None:
        return ticket

    email_raw = str(claims.get("email", ""))
    email_canonical = email_raw.lower().strip()
    if not email_canonical:
        raise _TxFailure()
    user = (
        (
            await db.execute(
                select(models.User).where(
                    models.User.canonical_email == email_canonical
                )
            )
        )
        .scalars()
        .first()
    )
    if user is None or user.role == "admin":
        raise _TxFailure()
    return await _link_identity_ticket(db, cfg, user, sub, email_raw)


async def _resolve_signup(
    db: AsyncSession,
    cfg: AppConfig,
    claims: dict[str, object],
    *,
    invite_id: int,
    invite_role: str,
    now: datetime,
) -> SessionTicket:
    """Signup-kind resolution.

    Resolution order:
    a. Auth identity (google, sub) already exists → login, leave invite.
    b. User with matching canonical email exists → link identity, consume invite.
    c. Neither → create user + profile + identity, consume invite.
    """
    sub = str(claims["sub"])
    email_raw = str(claims.get("email", ""))
    email_canonical = email_raw.lower().strip()

    # Path a: identity already linked → login (leave invite unconsumed).
    ticket = await _linked_user_ticket(db, cfg, sub)
    if ticket is not None:
        return ticket

    # Paths b/c: consume invite FIRST — before any account state mutations.
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
                    models.User.canonical_email == email_canonical
                )
            )
        )
        .scalars()
        .first()
    )
    if existing_user is not None:
        return await _link_identity_ticket(db, cfg, existing_user, sub, email_raw)

    # Path c: create new user + profile + identity.
    new_user = models.User(
        canonical_email=email_canonical,
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
            email=email_raw,
        )
    )
    return SessionTicket(new_user.id, profile.id, 0)


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
    if (gate := _google_gate(cfg)) is not None:
        return gate

    flow = await _begin_google_flow(db, cfg, request, kind="login")
    response: RedirectResponse = RedirectResponse(url=flow.auth_url, status_code=302)
    if flow.new_cookie:
        _set_pre_auth_cookie(response, cfg, flow.pre_auth_value)
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_raw_session)],
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    """Handle the Google OAuth callback for both login and signup flows.

    Looks up the attempt by ``state`` alone (the column is UNIQUE) and
    dispatches account resolution on the attempt's ``kind``.

    Uses two transaction windows so no DB connection is held during Google I/O:
    Window 1 reads and validates the attempt (and, for signups, the invite);
    Window 2 consumes the attempt and resolves the account.
    """
    cfg = get_config()
    if (gate := _google_gate(cfg)) is not None:
        return gate

    if error or not state or not code:
        return _oauth_failure_response()

    # Window 1: short read — identify and validate attempt.
    async with db.begin():
        attempt = (
            (
                await db.execute(
                    select(models.OauthAttempt).where(
                        models.OauthAttempt.state == state,
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
        kind = attempt.kind
        attempt_state = attempt.state
        attempt_pkce_verifier = attempt.pkce_verifier or ""
        attempt_nonce = attempt.nonce or ""
        attempt_browser_hash = attempt.browser_session_hash or ""
        attempt_expected_email = attempt.expected_canonical_email or ""
        invite_id = attempt.invite_id
        invite_role = ""

        if kind == "signup":
            # Signup attempts must always reference a still-valid invite;
            # read it here to capture the role.
            if invite_id is None:
                return _oauth_failure_response()
            invite_row = (
                (
                    await db.execute(
                        select(models.Invite).where(
                            models.Invite.id == invite_id,
                            *invite_valid_clauses(datetime.now(UTC)),
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

    # Local ops (no DB): validate browser binding (constant-time comparison).
    pre_auth_value = request.cookies.get(_PRE_AUTH_COOKIE, "")
    cookie_hash = hashlib.sha256(pre_auth_value.encode()).hexdigest()
    if not hmac.compare_digest(cookie_hash, attempt_browser_hash):
        return _oauth_failure_response()

    # Network I/O (no transaction held): exchange authorization code.
    try:
        claims = await fetch_google_id_token_claims(
            code=code,
            code_verifier=attempt_pkce_verifier,
            redirect_uri=cfg.public_base_url.rstrip("/") + _CALLBACK_PATH,
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            expected_nonce=attempt_nonce,
        )
    except OAuthError as e:
        logger.warning("OAuth error in google_callback: %s", e)
        return _oauth_failure_response()

    # Local: a signup must be completed by the invited Google account.
    if kind == "signup":
        google_email_canonical = str(claims.get("email", "")).lower().strip()
        if google_email_canonical != attempt_expected_email:
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
                    models.OauthAttempt.consumed_at.is_(None),
                    models.OauthAttempt.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if int(consume_result.rowcount) == 0:  # type: ignore[attr-defined]
                raise _TxFailure()

            if kind == "signup":
                if invite_id is None:  # window-1 invariant; guard, don't assert
                    raise _TxFailure()
                ticket = await _resolve_signup(
                    db,
                    cfg,
                    claims,
                    invite_id=invite_id,
                    invite_role=invite_role,
                    now=now,
                )
            else:
                ticket = await _resolve_login(db, cfg, claims)
            return issue_session_redirect(cfg, ticket)
    except _TxFailure:
        return _oauth_failure_response()
    except IntegrityError:
        # Two concurrent flows racing to link the same (provider, subject)
        # identity: the loser's commit violates the unique constraint.  Keep
        # the uniform generic failure instead of surfacing a 500.
        logger.warning("Google callback lost an identity-link race; returning 400")
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
    if (gate := _google_gate(cfg)) is not None:
        return gate

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

    flow = await _begin_google_flow(
        db,
        cfg,
        request,
        kind="signup",
        invite_id=invite.id,
        expected_canonical_email=invite.email.lower().strip(),
        login_hint=invite.email,
    )
    response = JSONResponse(
        content={"authorization_url": flow.auth_url},
        headers=_NO_STORE,
    )
    if flow.new_cookie:
        _set_pre_auth_cookie(response, cfg, flow.pre_auth_value)
    return response
