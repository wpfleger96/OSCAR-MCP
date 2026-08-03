"""Route-level auth guards as FastAPI dependencies.

Every route in ``/api/v1/*`` carries exactly one of these guards (or no
guard for explicitly public routes such as ``/api/v1/auth/*``).

``require_auth``
    Returns the ``ActorContext`` for the request.  Raises 401 if the
    middleware did not resolve one (unauthenticated in multiuser mode or
    actor resolution failed in local mode).

``require_writable``
    Extends ``require_auth``: additionally raises 403 if ``actor.can_write``
    is False (demo role).  Use for any mutating endpoint.

``require_admin``
    Extends ``require_auth``: additionally raises 403 if the actor is not
    admin.  Use for account/invite lifecycle and maintenance commands.

``require_local_only``
    Raises 403 in multiuser mode.  Registered only for local-mode-only routes
    such as ``/import/detect`` and ``/import/path``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from snore.auth.actor import ActorContext, AuthMode


def _get_actor(request: Request) -> ActorContext:
    """Dependency that returns the actor or raises 401."""
    actor: ActorContext | None = getattr(request.state, "actor", None)
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return actor


def require_auth(actor: Annotated[ActorContext, Depends(_get_actor)]) -> ActorContext:
    """Require an authenticated actor; return it.

    Raises 401 if unauthenticated.
    """
    return actor


def require_writable(
    actor: Annotated[ActorContext, Depends(require_auth)],
) -> ActorContext:
    """Require an authenticated, writable (non-demo) actor.

    Raises 401 if unauthenticated, 403 if the actor is demo role.
    """
    if not actor.can_write:
        raise HTTPException(status_code=403, detail="Write access is not permitted")
    return actor


def require_admin(
    actor: Annotated[ActorContext, Depends(require_auth)],
) -> ActorContext:
    """Require an authenticated admin actor.

    Raises 401 if unauthenticated, 403 if the actor is not admin.
    """
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return actor


def require_local_only(request: Request) -> None:
    """Require local auth mode; raise 403 in multiuser mode.

    Used for server-path import routes that must not exist in multiuser.
    """
    from snore.api.config import get_config  # noqa: PLC0415

    if get_config().auth_mode is AuthMode.MULTIUSER:
        raise HTTPException(
            status_code=403,
            detail="Server-path import is not available in multiuser mode",
        )


RequireAuth = Annotated[ActorContext, Depends(require_auth)]
RequireWritable = Annotated[ActorContext, Depends(require_writable)]
RequireAdmin = Annotated[ActorContext, Depends(require_admin)]
