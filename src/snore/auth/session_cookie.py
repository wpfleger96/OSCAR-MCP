"""Signed session cookie serialisation.

The cookie holds only ``{user_id, active_profile_id, session_version}`` —
no role, email, PII, or OAuth tokens.  ItsDangerous TimestampSigner is used
for HMAC signing; it is NOT encryption (signed ≠ encrypted).

Cookie attributes:
    HttpOnly:    Always.
    SameSite:    Lax.
    Secure:      Derived from ``AppConfig.secure_cookie`` (True when the
                 public base URL is HTTPS on a non-loopback host).
    Max-Age:     14 days.
    Path:        /.
"""

from __future__ import annotations

import json
import logging

from datetime import timedelta

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

COOKIE_NAME = "snore_session"
COOKIE_MAX_AGE_SECONDS = int(timedelta(days=14).total_seconds())


def _make_signer(secret: str) -> TimestampSigner:
    return TimestampSigner(secret, salt="snore-session-v1")


def encode_session(
    secret: str,
    user_id: int,
    active_profile_id: int,
    session_version: int,
) -> str:
    """Produce a signed, opaque session token."""
    payload = json.dumps(
        {
            "u": user_id,
            "p": active_profile_id,
            "v": session_version,
        },
        separators=(",", ":"),
    )
    return _make_signer(secret).sign(payload).decode()


def decode_session(
    secret: str, token: str, max_age: int = COOKIE_MAX_AGE_SECONDS
) -> tuple[int, int, int] | None:
    """Decode and validate a session token.

    Returns:
        ``(user_id, active_profile_id, session_version)`` on success.
        ``None`` if the token is absent, malformed, expired, or tampered.
    """
    if not token:
        return None
    try:
        raw = _make_signer(secret).unsign(token.encode(), max_age=max_age).decode()
        data = json.loads(raw)
        return int(data["u"]), int(data["p"]), int(data["v"])
    except (SignatureExpired, BadSignature):
        return None
    except (KeyError, ValueError, TypeError):
        return None


def set_session_cookie(
    response: Response,
    secret: str,
    user_id: int,
    active_profile_id: int,
    session_version: int,
    *,
    secure: bool,
) -> None:
    """Write the session cookie onto ``response``.

    No ``Domain`` attribute is set so the browser issues a host-only cookie —
    the cookie is scoped to the exact origin and never shared with subdomains.
    """
    token = encode_session(secret, user_id, active_profile_id, session_version)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(
    response: Response,
    *,
    secure: bool,
) -> None:
    """Clear the session cookie on ``response``."""
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def get_session_token(request: Request) -> str:
    """Extract the raw session token from the request cookies."""
    return request.cookies.get(COOKIE_NAME, "")
