"""HTTP middleware: authentication and rate limiting.

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

``RateLimitMiddleware``
    Stub; logging hook only.  The actual lockout logic lives in
    ``snore.auth.lockout`` and is enforced in the auth router.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from snore.auth.actor import ActorContext

logger = logging.getLogger(__name__)


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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """No-op middleware stub; actual lockout is enforced in the auth router."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        return await call_next(request)
