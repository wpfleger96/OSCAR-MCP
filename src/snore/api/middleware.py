"""HTTP middleware: authentication, CSRF, rate limiting, and body ceilings.

``AuthMiddleware``
    Resolves ``request.state.actor`` on every request.  Local mode auto-
    provisions; multiuser mode validates the signed session cookie.

``CsrfMiddleware``
    In multiuser mode, all unsafe-method (POST/PUT/PATCH/DELETE) requests must
    carry an ``Origin`` or ``Referer`` header whose parsed origin exactly
    matches ``AppConfig.public_origin`` or a pre-parsed ``dev_origins`` entry.
    Comparison is on canonical ``(scheme, host, effective_port)`` tuples —
    ``startswith`` matching is explicitly NOT used.  A ``"null"`` origin is
    always rejected.  When ``public_origin`` is ``None`` in multiuser mode the
    check fails closed (403) rather than open.

    Also:
    - Applies a small body-size ceiling to all ``/api/v1/auth/`` requests so
      Pydantic materialisation is not the first resource boundary for auth
      endpoints.
    - Adds ``Cache-Control: no-store`` to all ``/api/v1/auth/`` responses,
      covering 2xx, 4xx, and framework error responses.

``RateLimitMiddleware``
    Per-IP sliding-window rate limiter (30 req/60 s) on ``/api/v1/auth/``
    in multiuser mode.  Uses the canonical trusted-client-IP helper.

``_ByteCeilingReceive``
    ASGI receive wrapper that raises 413 once a cumulative byte ceiling is
    crossed.  Used for the ingress ceiling on upload bodies and the conservative
    auth-endpoint body limit.
"""

from __future__ import annotations

import logging

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from snore.auth.actor import ActorContext

logger = logging.getLogger(__name__)

_AUTH_PATH_PREFIX = "/api/v1/auth"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Conservative body ceiling for all /api/v1/auth/ endpoints.  Prevents
# Pydantic from materialising arbitrarily large bodies before model validation.
_AUTH_BODY_LIMIT = 16 * 1024  # 16 KiB


class _BodyCeilingExceeded(Exception):
    """Sentinel raised by _ByteCeilingReceive when the auth-body ceiling is crossed.

    A separate exception type (not ``HTTPException``) is used so
    ``CsrfMiddleware.dispatch`` can catch it before Starlette's form/body
    parser gets a chance to convert it into a 400 "error parsing body" response.
    The upload ingress ceiling (import_data.py) continues to raise
    ``HTTPException(413)`` directly because it lives inside the route handler
    rather than in middleware.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail


# ---------------------------------------------------------------------------
# Shared ASGI receive wrapper
# ---------------------------------------------------------------------------


class _ByteCeilingReceive:
    """ASGI receive wrapper that raises 413 once a cumulative byte ceiling is hit.

    Counts bytes as multipart/body chunks arrive — before any parser spools
    them.  Used both for upload ingress ceilings and the auth-endpoint body
    ceiling.
    """

    def __init__(
        self,
        inner: Callable[[], Awaitable[MutableMapping[str, Any]]],
        max_bytes: int,
        detail: str | None = None,
        raise_as_body_ceiling: bool = False,
    ) -> None:
        self._inner = inner
        self._max = max_bytes
        self._detail = (
            detail or f"Request body exceeds the {max_bytes // 1024} KiB limit"
        )
        self._seen = 0
        # When True, raise _BodyCeilingExceeded instead of HTTPException so the
        # auth middleware can catch it and return a proper 413 before Starlette's
        # body/form parser converts it to a 400.
        self._raise_as_body_ceiling = raise_as_body_ceiling

    async def __call__(self) -> MutableMapping[str, Any]:
        msg = await self._inner()
        if msg.get("type") == "http.request":
            chunk: bytes = msg.get("body", b"")
            self._seen += len(chunk)
            if self._seen > self._max:
                if self._raise_as_body_ceiling:
                    raise _BodyCeilingExceeded(self._detail)
                from fastapi import HTTPException  # noqa: PLC0415

                raise HTTPException(status_code=413, detail=self._detail)
        return msg


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------


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
            try:
                actor = await _resolve_local_actor()
            except Exception as exc:
                logger.warning("Local mode actor resolution failed: %s", exc)
                actor = None
            request.state.actor = actor
        else:
            request.state.actor = None
            actor = await _resolve_multiuser_actor(request)
            if actor is not None:
                request.state.actor = actor

        return await call_next(request)


async def _resolve_local_actor() -> ActorContext | None:
    from snore.auth.actor import AuthMode  # noqa: PLC0415
    from snore.auth.factory import ActorContextFactory  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        factory = ActorContextFactory(db)
        return await factory.make_local(mode=AuthMode.LOCAL)


async def _resolve_multiuser_actor(request: Request) -> ActorContext | None:
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

            user = await db.get(models.User, user_id)
            if user is None or user.disabled_at is not None:
                return None
            if user.session_version != cookie_version:
                return None

            from snore.auth.factory import ActorContextFactory  # noqa: PLC0415

            factory = ActorContextFactory(db)
            return await factory.make(
                user_id=user_id,
                active_profile_id=active_profile_id,
                mode=AuthMode.MULTIUSER,
            )
    except Exception as exc:
        logger.warning("Multiuser actor resolution failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# CsrfMiddleware
# ---------------------------------------------------------------------------


class CsrfMiddleware(BaseHTTPMiddleware):
    """CSRF origin check + auth-path no-store + auth-body ceiling.

    In multiuser mode, rejects any unsafe-method request whose parsed origin
    does not exactly match ``AppConfig.public_origin`` or a ``dev_origins``
    entry.  Fails closed when ``public_origin`` is ``None`` (which is
    unreachable under correct config, but defensive).

    Also applies a 16 KiB body ceiling to all ``/api/v1/auth/`` requests and
    adds ``Cache-Control: no-store`` to all ``/api/v1/auth/`` responses.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from snore.api.config import get_config  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415

        cfg = get_config()
        is_auth_path = request.url.path.startswith(_AUTH_PATH_PREFIX)

        # Apply conservative body ceiling to all auth-endpoint requests.
        # We first check Content-Length so we can return 413 directly before
        # Starlette's body/form parser gets control (it catches exceptions from
        # the receive wrapper and converts them to 400).  The receive wrapper
        # remains as a backstop for chunked requests that omit Content-Length.
        if is_auth_path:
            cl_header = request.headers.get("content-length", "")
            try:
                if int(cl_header) > _AUTH_BODY_LIMIT:
                    from starlette.responses import JSONResponse  # noqa: PLC0415

                    return JSONResponse(
                        {
                            "detail": (
                                f"Request body exceeds the "
                                f"{_AUTH_BODY_LIMIT // 1024} KiB limit"
                            )
                        },
                        status_code=413,
                        headers={"Cache-Control": "no-store"},
                    )
            except (ValueError, TypeError):
                # No valid Content-Length — streaming ceiling handles it.
                pass
            # Streaming backstop for chunked bodies without Content-Length.
            request._receive = _ByteCeilingReceive(  # noqa: SLF001
                request.receive, _AUTH_BODY_LIMIT
            )

        if cfg.auth_mode is AuthMode.MULTIUSER and request.method in _UNSAFE_METHODS:
            error = self._check_origin(request, cfg)
            if error is not None:
                from starlette.responses import JSONResponse  # noqa: PLC0415

                return JSONResponse(
                    {"detail": error},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )

        inner_response: Response = await call_next(request)

        if is_auth_path:
            inner_response.headers["Cache-Control"] = "no-store"

        return inner_response

    @staticmethod
    def _check_origin(request: Request, cfg: object) -> str | None:
        """Return an error string if the origin fails the check, or None if OK."""
        from snore.api.config import AppConfig, parse_origin  # noqa: PLC0415

        assert isinstance(cfg, AppConfig)
        public_origin = cfg.public_origin

        if public_origin is None:
            # multiuser with no public origin: fail closed.
            logger.warning(
                "CSRF check: public_origin is None in multiuser mode — failing closed"
            )
            return "Origin not allowed"

        raw = (request.headers.get("origin") or "").strip()

        if raw.lower() == "null":
            logger.warning("CSRF check: rejected null origin")
            return "Origin not allowed"

        if not raw:
            raw = (request.headers.get("referer") or "").strip()

        if not raw:
            logger.warning("CSRF check: no Origin or Referer present")
            return "Origin not allowed"

        incoming = parse_origin(raw)
        if incoming is None:
            logger.warning("CSRF check: could not parse origin %r", raw)
            return "Origin not allowed"

        if incoming == public_origin:
            return None

        if incoming in cfg.dev_origins:
            return None

        logger.warning("CSRF check: %r (%s) not in allowed origins", raw, incoming)
        return "Origin not allowed"


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter for public auth endpoints.

    Applies to all requests under ``/api/v1/auth/`` in multiuser mode.
    Uses the canonical ``get_client_ip()`` helper so the IP key matches
    what the credential lockout store keys on.
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
            from snore.api.client_ip import get_client_ip  # noqa: PLC0415

            ip = get_client_ip(request)
            store = get_rate_limit_store()
            if not store.check_and_record(ip):
                from starlette.responses import JSONResponse  # noqa: PLC0415

                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": "60", "Cache-Control": "no-store"},
                )

        return await call_next(request)
