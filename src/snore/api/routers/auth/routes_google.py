"""Google OAuth routes: login and invite-signup initiation, single callback.

Routes
------
GET  /api/v1/auth/google/login
POST /api/v1/auth/invites/google   (invite token in request body)
GET  /api/v1/auth/google/callback  (single callback for both flow kinds)

Both flows authorize with the same redirect URI; the ``oauth_attempts`` row
created at initiation carries ``kind`` ("login" | "signup"), and the callback
dispatches on it after validating the attempt.  Account resolution itself
lives in ``_google_resolution``.

Security controls
-----------------
- All flow state (state, nonce, PKCE verifier) is server-side in the
  ``oauth_attempts`` table; the browser carries only the opaque
  ``snore_pre_auth`` binding cookie.
- The callback uses two transaction windows so no DB connection is held during
  Google network I/O; window 2 consumes the attempt via a conditional UPDATE
  (replay protection) and rolls back on any failure via ``TxFailure``.
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
from typing import Annotated, Literal
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
from snore.api.constants import NO_STORE
from snore.api.deps import get_db, get_raw_session
from snore.api.routers.auth._common import (
    TOKEN_MAX_LEN,
    issue_session_redirect,
    opportunistic_purge_oauth_attempts,
)
from snore.api.routers.auth._google_resolution import (
    TxFailure,
    resolve_login,
    resolve_signup,
)
from snore.auth.emails import normalize_email
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

_FlowKind = Literal["login", "signup"]


class GoogleInviteInitRequest(BaseModel):
    token: Annotated[str, StringConstraints(max_length=TOKEN_MAX_LEN)]


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
        headers=NO_STORE,
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
        headers=NO_STORE,
    )


def _callback_redirect_uri(cfg: AppConfig) -> str:
    """The exact redirect URI sent at authorization time and token exchange."""
    return cfg.public_base_url.rstrip("/") + _CALLBACK_PATH


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
    kind: _FlowKind,
    invite_id: int | None = None,
    expected_canonical_email: str | None = None,
    login_hint: str | None = None,
) -> _GoogleFlowStart:
    """Create an ``oauth_attempts`` row and build the Google authorization URL.

    Owns everything the two initiation routes share: state/nonce generation,
    PKCE S256, browser binding, and opportunistic purge.  An existing pre-auth
    cookie is reused without rotation so concurrent tabs share one hash and
    parallel flows both stay valid; the caller passes the returned flow to
    ``_set_pre_auth_cookie`` unconditionally, which only writes when a new
    value was minted.
    """
    state = secrets.token_hex(32)
    nonce = secrets.token_hex(16)
    verifier, challenge = _pkce_pair()
    now = datetime.now(UTC)

    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": _callback_redirect_uri(cfg),
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


def _set_pre_auth_cookie(
    response: Response, cfg: AppConfig, flow: _GoogleFlowStart
) -> None:
    """Set the browser-binding cookie for a freshly minted flow value.

    No-op when the flow reused an existing cookie — reuse without rotation is
    what keeps concurrent tabs' flows valid, and keeping the check here means
    call sites cannot forget it.
    """
    if not flow.new_cookie:
        return
    response.set_cookie(
        _PRE_AUTH_COOKIE,
        flow.pre_auth_value,
        max_age=cfg.pre_auth_cookie_ttl_seconds,
        path="/",
        secure=cfg.secure_cookie,
        httponly=True,
        samesite="lax",
    )


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
    _set_pre_auth_cookie(response, cfg, flow)
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
        # Deliberately no ``kind`` filter: ``state`` is UNIQUE, and ``kind``
        # is server-written at initiation (CHECK-constrained to
        # login|signup) — it drives dispatch below, never selection.
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

        # For signups: the attempt must reference a still-valid invite; read
        # it here to capture (invite_id, role) for window-2 resolution.
        signup_ctx: tuple[int, str] | None = None
        if kind == "signup":
            if attempt.invite_id is None:
                return _oauth_failure_response()
            invite_row = (
                (
                    await db.execute(
                        select(models.Invite).where(
                            models.Invite.id == attempt.invite_id,
                            *invite_valid_clauses(datetime.now(UTC)),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if invite_row is None:
                return _oauth_failure_response()
            signup_ctx = (attempt.invite_id, invite_row.role)
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
            redirect_uri=_callback_redirect_uri(cfg),
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            expected_nonce=attempt_nonce,
        )
    except OAuthError as e:
        logger.warning("OAuth error in google_callback: %s", e)
        return _oauth_failure_response()

    # Local: a signup must be completed by the invited Google account.
    if kind == "signup":
        google_email_canonical = normalize_email(str(claims.get("email", "")))
        if google_email_canonical != attempt_expected_email:
            return _oauth_failure_response()

    # Window 2: fresh transaction — consume + resolve.
    # Failures raise TxFailure inside the context so SQLAlchemy rolls back
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
                raise TxFailure()

            if kind == "signup":
                if signup_ctx is None:  # window-1 invariant; guard, don't assert
                    raise TxFailure()
                invite_id, invite_role = signup_ctx
                ticket = await resolve_signup(
                    db,
                    cfg,
                    claims,
                    invite_id=invite_id,
                    invite_role=invite_role,
                    now=now,
                )
            else:
                ticket = await resolve_login(db, cfg, claims)
            return issue_session_redirect(cfg, ticket)
    except TxFailure:
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
            headers=NO_STORE,
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
            headers=NO_STORE,
        )

    flow = await _begin_google_flow(
        db,
        cfg,
        request,
        kind="signup",
        invite_id=invite.id,
        expected_canonical_email=normalize_email(invite.email),
        login_hint=invite.email,
    )
    response = JSONResponse(
        content={"authorization_url": flow.auth_url},
        headers=NO_STORE,
    )
    _set_pre_auth_cookie(response, cfg, flow)
    return response
