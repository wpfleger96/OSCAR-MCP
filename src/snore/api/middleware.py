"""HTTP middleware: authentication, CSRF, rate limiting, and body ceilings.

``AuthMiddleware``
    Resolves ``request.state.actor`` on every request.  Local mode auto-
    provisions; multiuser mode validates the signed session cookie.

``AuthPathMiddleware``
    In multiuser mode, all unsafe-method (POST/PUT/PATCH/DELETE) requests must
    carry an ``Origin`` or ``Referer`` header whose parsed origin exactly
    matches ``AppConfig.public_origin`` or a pre-parsed ``dev_origins`` entry.
    Comparison is on canonical ``(scheme, host, effective_port)`` tuples —
    ``startswith`` matching is explicitly NOT used.  A ``"null"`` origin is
    always rejected.  When ``public_origin`` is ``None`` in multiuser mode the
    check fails closed (403) rather than open.

    Also:
    - Applies a 16 KiB body ceiling to all ``/api/v1/auth/`` requests via a
      pre-read buffer in ``dispatch``.  The full body is consumed before
      ``call_next``; if the ceiling is exceeded, 413 is returned directly so
      ``Content-Length`` presence, accuracy, or absence is irrelevant.
    - Adds ``Cache-Control: no-store`` to all ``/api/v1/auth/`` responses,
      covering 2xx, 4xx, and framework error responses.

``RateLimitMiddleware``
    Per-IP sliding-window rate limiter (30 req/60 s) on ``/api/v1/auth/``
    in multiuser mode.  Uses the canonical trusted-client-IP helper.
"""

from __future__ import annotations

import logging

from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from snore.api.config import AppConfig

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from snore.auth.actor import ActorContext

logger = logging.getLogger(__name__)

_AUTH_PATH_PREFIX = "/api/v1/auth"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Conservative body ceiling for all /api/v1/auth/ endpoints.
_AUTH_BODY_LIMIT = 16 * 1024  # 16 KiB


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
# AuthPathMiddleware
# ---------------------------------------------------------------------------


class AuthPathMiddleware(BaseHTTPMiddleware):
    """CSRF origin check + auth-path no-store + auth-body ceiling.

    In multiuser mode, rejects any unsafe-method request whose parsed origin
    does not exactly match ``AppConfig.public_origin`` or a ``dev_origins``
    entry.  Fails closed when ``public_origin`` is ``None``.

    Auth-body ceiling (16 KiB):
    The full ASGI receive stream is consumed into a buffer before ``call_next``.
    If total bytes exceed ``_AUTH_BODY_LIMIT``, 413 is returned immediately —
    before Starlette's body parser ever executes.  Bodies within the limit are
    replayed via a synthetic receive callable so downstream handlers see an
    identical stream.  This approach enforces the ceiling regardless of whether
    the client sends ``Content-Length``, uses chunked encoding, omits the
    header, or lies about the value.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from snore.api.config import get_config  # noqa: PLC0415
        from snore.auth.actor import AuthMode  # noqa: PLC0415

        cfg = get_config()
        is_auth_path = request.url.path.startswith(_AUTH_PATH_PREFIX)

        # Pre-read auth-endpoint bodies before call_next so the ceiling fires
        # regardless of Content-Length presence or accuracy.
        #
        # Three explicit terminal states:
        #   total > _AUTH_BODY_LIMIT  → immediate 413 (no further receive calls)
        #   http.disconnect           → immediate 499 abort (never calls call_next)
        #   http.request more_body=F  → replay once, then delegate to original receive
        if is_auth_path:
            original_receive = request.receive  # save before any mutation
            body_chunks: list[bytes] = []
            total = 0

            while True:
                msg = await original_receive()
                msg_type = msg.get("type")
                if msg_type == "http.request":
                    chunk = msg.get("body", b"")
                    total += len(chunk)
                    if total > _AUTH_BODY_LIMIT:
                        # Immediate 413 — do NOT drain; any further receive call
                        # can block indefinitely and bypass the rate limiter.
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
                    body_chunks.append(chunk)
                    if not msg.get("more_body", False):
                        break
                elif msg_type == "http.disconnect":
                    # Client disconnected before completing the request.
                    # Never invoke call_next — the request is incomplete and
                    # side-effecting handlers (e.g. /invites/redeem) must not run.
                    from starlette.responses import Response  # noqa: PLC0415

                    return Response(
                        status_code=499, headers={"Cache-Control": "no-store"}
                    )
                else:
                    # Unknown ASGI message — treat as disconnect.
                    from starlette.responses import Response  # noqa: PLC0415

                    return Response(
                        status_code=499, headers={"Cache-Control": "no-store"}
                    )

            # A genuine terminal http.request frame was received.
            # Replay the buffered body once, then delegate subsequent receives
            # to the original callable so any streaming downstream work functions.
            full_body = b"".join(body_chunks)
            _replayed = False

            async def _replay_receive() -> MutableMapping[str, Any]:
                nonlocal _replayed
                if not _replayed:
                    _replayed = True
                    return {
                        "type": "http.request",
                        "body": full_body,
                        "more_body": False,
                    }
                return await original_receive()

            request._receive = _replay_receive  # noqa: SLF001

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
    def _check_origin(request: Request, cfg: AppConfig) -> str | None:
        """Return an error string if the origin fails the check, or None if OK."""
        from snore.api.config import parse_origin  # noqa: PLC0415

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


# Backward-compat alias — app.py imports this name; remove once app.py is updated.
CsrfMiddleware = AuthPathMiddleware


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
