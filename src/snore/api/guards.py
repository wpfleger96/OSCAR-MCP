"""Route-level auth guards as FastAPI dependencies.

Auth classification for ``/api/v1/*`` routes:

- **Explicit guard parameter** (e.g. ``_actor: RequireAuth``): used on routes
  where the guard is the *only* reason to declare the dependency (profiles,
  db stats/vacuum, mutating session/analysis/import routes, auth router).

- **Implicit via ``service_dep`` chain**: routes that take a
  ``service_dep(SomeService)`` parameter authenticate through
  ``ActorDep → get_actor``, which raises 401 in multiuser mode when no
  actor is present.  The effect is identical to ``require_auth`` — there
  is simply no duplicate declaration needed.  Affected routers: ``days``,
  ``devices``, ``events``, ``reports``, ``rx``, ``sessions`` (list / read),
  ``stats``, ``waveforms``, ``analysis`` (read routes).

- **Public routes** (no auth): ``/api/v1/auth/*`` (login, logout, status,
  invite lookup/redeem).  These are intentionally unauthenticated.

- **Local-only routes** (not registered in multiuser): ``/import/detect``,
  ``/import/path``.

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

from snore.api.deps import get_actor
from snore.auth.actor import ActorContext, AuthMode


def require_auth(actor: Annotated[ActorContext, Depends(get_actor)]) -> ActorContext:
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
