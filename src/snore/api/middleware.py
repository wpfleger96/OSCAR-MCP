"""HTTP middleware: authentication, CSRF, and rate limiting.

``AuthMiddleware``
    - Local mode: bootstraps the single local admin user + profile, sets
      ``request.state.actor`` to the ``ActorContext``.
    - Multiuser mode: reads the signed session cookie, DB-validates the
      actor (session_version bump, stale-profile fallback), sets
      ``request.state.actor``; unauthenticated requests get ``None``.

    The middleware never raises 401/403 itself — route dependencies
    (``require_auth``, ``require_writable``, ``require_admin``) enforce those
    boundaries so that auth-free endpoints (``/health``, ``/api/v1/auth/*``)
    always pass through.

``CsrfMiddleware``
    In multiuser mode, all unsafe-method (POST/PUT/PATCH/DELETE) requests must
    carry an ``Origin`` or ``Referer`` header whose parsed origin exactly
    matches the configured ``SNORE_PUBLIC_BASE_URL`` (or ``SNORE_DEV_ORIGINS``
    for local development).  The comparison is on canonical
    ``(scheme, host, effective_port)`` tuples; ``startswith`` string matching
    is explicitly NOT used.  A ``null`` origin is always rejected.

    Also adds ``Cache-Control: no-store`` to all responses on
    ``/api/v1/auth/`` paths so 4xx and framework error responses are covered.

``RateLimitMiddleware``
    Per-IP sliding-window rate limiter applied to all ``/api/v1/auth/``
    requests in multiuser mode.  Default: 30 requests per 60-second window per
    IP.  Trusts the same ``SNORE_TRUSTED_PROXIES`` forwarded-IP chain as
    ``AuthMiddleware``.
"""

from __future__ import annotations

import ipaddress
import logging

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from snore.auth.actor import ActorContext

logger = logging.getLogger(__name__)

_AUTH_PATH_PREFIX = "/api/v1/auth"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _trusted_client_ip(request: Request) -> str:
    """Return the trusted client IP, honouring ``SNORE_TRUSTED_PROXIES``.

    The forwarded header value is validated as a well-formed IP address before
    use.  An invalid or missing forwarded value falls back to the peer address.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    peer = request.client.host if request.client else "unknown"
    cfg = get_config()
    if peer in cfg.trusted_proxies:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            try:
                ipaddress.ip_address(forwarded)
                return forwarded
            except ValueError:
                logger.warning(
                    "cf-connecting-ip %r is not a valid IP address; using peer %r",
                    forwarded,
                    peer,
                )
    return peer


def _parse_origin(url: str) -> tuple[str, str, int] | None:
    """Parse ``url`` to ``(scheme, host, effective_port)`` or ``None`` on failure."""
    try:
        p = urlparse(url)
        scheme = p.scheme.lower()
        host = (p.hostname or "").lower()
        if not scheme or not host:
            return None
        port = p.port
        if port is None:
            port = 443 if scheme == "https" else 80
        return (scheme, host, port)
    except Exception:
        return None


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve and attach ``request.state.actor`` on every request.

    In multiuser mode, unauthenticated requests produce ``request.state.actor = None``.
    Route dependencies enforce auth requirements independently.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from snore.api.config import get_config  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415

        cfg = get_config()

        if cfg.auth_mode is AuthMode.LOCAL:
            # Local mode: resolve (or auto-provision) the single admin profile.
            # Done inside a short-lived DB scope that is committed on exit.
            try:
                actor = await _resolve_local_actor()
            except Exception as exc:
                logger.warning("Local mode actor resolution failed: %s", exc)
                actor = None
            request.state.actor = actor
        else:
            # Multiuser mode: read and validate the signed session cookie.
            request.state.actor = None  # Default — routes enforce auth.
            actor = await _resolve_multiuser_actor(request)
            if actor is not None:
                request.state.actor = actor

        return await call_next(request)


async def _resolve_local_actor() -> ActorContext | None:
    """Resolve (or auto-provision) the local admin ActorContext."""
    from snore.auth.actor import AuthMode  # noqa: PLC0415
    from snore.auth.factory import ActorContextFactory  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        factory = ActorContextFactory(db)
        return await factory.make_local(mode=AuthMode.LOCAL)


async def _resolve_multiuser_actor(request: Request) -> ActorContext | None:
    """Validate the session cookie and return an ``ActorContext`` or ``None``."""
    from snore.api.config import get_config  # noqa: PLC0415
    from snore.auth.actor import AuthMode  # noqa: PLC0415
    from snore.auth.factory import ActorContextFactory  # noqa: PLC0415
    from snore.auth.session_cookie import (  # noqa: PLC0415
        decode_session,
        get_session_token,
    )
    from snore.database.session import session_scope  # noqa: PLC0415

    cfg = get_config()
    token = get_session_token(request)
    if not token:
        return None

    decoded = decode_session(cfg.session_secret, token)
    if decoded is None:
        return None

    user_id, active_profile_id, cookie_version = decoded

    try:
        async with session_scope() as db:
            from snore.database import models  # noqa: PLC0415

            # Load user — check disabled and session_version.
            user = await db.get(models.User, user_id)
            if user is None or user.disabled_at is not None:
                return None
            if user.session_version != cookie_version:
                # Password changed / user disabled / role changed — cookie invalidated.
                return None

            factory = ActorContextFactory(db)
            # Pass the cookie's active_profile_id; factory falls back gracefully
            # if it is stale/foreign/deleted.
            return await factory.make(
                user_id=user_id,
                active_profile_id=active_profile_id,
                mode=AuthMode.MULTIUSER,
            )
    except Exception as exc:
        logger.warning("Multiuser actor resolution failed: %s", exc)
        return None


class CsrfMiddleware(BaseHTTPMiddleware):
    """CSRF origin check for all unsafe methods in multiuser mode.

    In multiuser mode rejects any unsafe-method request whose Origin header
    does not exactly match the configured public origin (scheme + host + port).
    Compares canonical ``(scheme, host, effective_port)`` tuples — never
    ``startswith``.  A literal ``"null"`` origin or a missing Origin/Referer
    is rejected.  Falls back to ``Referer`` (parsed to its origin) when
    ``Origin`` is absent.

    Also adds ``Cache-Control: no-store`` to all responses on
    ``/api/v1/auth/`` paths, covering 2xx and 4xx alike.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from snore.api.config import get_config  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415

        cfg = get_config()

        if cfg.auth_mode is AuthMode.MULTIUSER and request.method in _UNSAFE_METHODS:
            error = self._check_origin(request, cfg)
            if error is not None:
                from starlette.responses import JSONResponse  # noqa: PLC0415

                response = JSONResponse(
                    {"detail": error},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
                return response

        inner_response: Response = await call_next(request)

        # Add Cache-Control: no-store to all /api/v1/auth/ responses so
        # 4xx, 422, and framework error responses are also covered.
        if request.url.path.startswith(_AUTH_PATH_PREFIX):
            inner_response.headers["Cache-Control"] = "no-store"

        return inner_response

    @staticmethod
    def _check_origin(request: Request, cfg: object) -> str | None:
        """Return an error string if the origin fails the check, or None if OK."""
        from snore.api.config import AppConfig  # noqa: PLC0415

        assert isinstance(cfg, AppConfig)
        public_origin = cfg.public_origin
        if public_origin is None:
            # No public origin configured — allow (shouldn't happen in multiuser).
            return None

        raw = (request.headers.get("origin") or "").strip()

        # Browsers send the literal string "null" for file:// or sandboxed iframes.
        if raw.lower() == "null":
            logger.warning("CSRF check: rejected null origin")
            return "Origin not allowed"

        if not raw:
            # Origin absent — fall back to Referer.
            raw = (request.headers.get("referer") or "").strip()

        if not raw:
            logger.warning("CSRF check: no Origin or Referer present")
            return "Origin not allowed"

        incoming = _parse_origin(raw)
        if incoming is None:
            logger.warning("CSRF check: could not parse origin %r", raw)
            return "Origin not allowed"

        # Check against the primary public origin.
        if incoming == public_origin:
            return None

        # Check against dev-only extra origins (empty frozenset in production).
        for extra in cfg.dev_origins:
            extra_origin = _parse_origin(extra)
            if extra_origin is not None and incoming == extra_origin:
                return None

        logger.warning(
            "CSRF check: %r (%s) not in allowed origins",
            raw,
            incoming,
        )
        return "Origin not allowed"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter for public auth endpoints.

    Applies to all requests under ``/api/v1/auth/`` in multiuser mode.
    Default limits: 30 requests per 60-second window per IP.  Uses the same
    trusted-proxy forwarding logic as ``AuthMiddleware``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from snore.api.config import get_config  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415
        from snore.auth.lockout import get_rate_limit_store  # noqa: PLC0415

        cfg = get_config()
        if cfg.auth_mode is AuthMode.MULTIUSER and request.url.path.startswith(
            _AUTH_PATH_PREFIX
        ):
            ip = _trusted_client_ip(request)
            store = get_rate_limit_store()
            if not store.check_and_record(ip):
                from starlette.responses import JSONResponse  # noqa: PLC0415

                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": "60", "Cache-Control": "no-store"},
                )

        return await call_next(request)
