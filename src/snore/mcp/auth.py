"""MCP OAuth authentication: token → ActorContext resolution.

This module is the single point where a validated FastMCP AccessToken is
mapped to a SNORE ActorContext.  It sits between fastmcp's auth middleware
(which validates bearer tokens and populates the request scope) and tool
code (which calls current_actor() to retrieve the resolved context).

Public API consumed by server.py:
- make_auth_provider  — construct the GoogleProvider for HTTP transport
- actor_scope         — _ScopeProvider seam: resolves actor per request
- current_actor       — retrieve actor bound by actor_scope()
- resolve_actor       — async token→ActorContext mapping (also tested directly)
- GOOGLE_PROVIDER     — constant for auth_identities.provider column
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.actor import ActorContext, AuthMode
from snore.auth.factory import ActorContextFactory
from snore.database import models
from snore.database.session import session_scope

if TYPE_CHECKING:
    from fastmcp.server.auth.auth import AccessToken, AuthProvider

GOOGLE_PROVIDER = "google"

# Per-request ContextVar holding the resolved actor; None when no request is active.
_current_actor: ContextVar[ActorContext | None] = ContextVar(
    "_current_actor", default=None
)


def make_auth_provider(
    *,
    base_url: str,
    google_client_id: str,
    google_client_secret: str,
) -> AuthProvider:
    """Construct a GoogleProvider for HTTP transport.

    Args:
        base_url:            Public server URL (SNORE_MCP_BASE_URL).
        google_client_id:    OAuth client ID (GOOGLE_CLIENT_ID).
        google_client_secret: OAuth client secret (GOOGLE_CLIENT_SECRET).

    Returns:
        Configured GoogleProvider instance.

    Raises:
        ValueError: If any argument is empty or blank, with the env var name.
    """
    from fastmcp.server.auth.providers.google import GoogleProvider  # noqa: PLC0415

    _require_nonempty(base_url, "base_url", "SNORE_MCP_BASE_URL")
    _require_nonempty(google_client_id, "google_client_id", "GOOGLE_CLIENT_ID")
    _require_nonempty(
        google_client_secret, "google_client_secret", "GOOGLE_CLIENT_SECRET"
    )

    return GoogleProvider(
        client_id=google_client_id,
        client_secret=google_client_secret,
        base_url=base_url,
    )


def _require_nonempty(value: str, arg_name: str, env_var: str) -> None:
    if not value or not value.strip():
        raise ValueError(
            f"{arg_name!r} must not be empty or blank "
            f"(set the {env_var} environment variable)"
        )


async def resolve_actor(db: AsyncSession, token: AccessToken) -> ActorContext:
    """Map a validated FastMCP AccessToken to an ActorContext.

    Steps:
    1. Extract ``sub`` from token claims (missing/empty → ToolError).
    2. Look up ``auth_identities`` WHERE provider='google' AND subject=sub
       (miss → ToolError with a user-actionable message).
    3. Build ActorContext via ActorContextFactory (ValueError → ToolError).

    ToolError messages are client-visible.  They are sanitised: they never
    expose user IDs, email addresses, Google subject values, or any internal
    DB state.

    Args:
        db:    Open AsyncSession (within an active transaction).
        token: Validated FastMCP AccessToken from the bearer middleware.

    Returns:
        Resolved ActorContext for the authenticated user.

    Raises:
        fastmcp.exceptions.ToolError: On any identity-resolution failure.
    """
    sub = token.claims.get("sub")
    if not sub:
        raise ToolError(
            "Authentication token is missing the required 'sub' claim. "
            "Re-authenticate and try again."
        )

    stmt = select(models.AuthIdentity).where(
        models.AuthIdentity.provider == GOOGLE_PROVIDER,
        models.AuthIdentity.subject == sub,
    )
    identity = (await db.execute(stmt)).scalars().first()
    if identity is None:
        raise ToolError(
            "No SNORE account is linked to this Google identity. "
            "Sign in to the SNORE web app first."
        )

    try:
        actor = await ActorContextFactory(db).make(
            identity.user_id, None, AuthMode.MULTIUSER
        )
    except ValueError as exc:
        msg = str(exc)
        if "disabled" in msg:
            raise ToolError(
                "This account is disabled. Contact your administrator."
            ) from exc
        raise ToolError(
            "Unable to resolve your SNORE account. Contact your administrator."
        ) from exc

    return actor


def current_actor() -> ActorContext:
    """Return the ActorContext bound by the active actor_scope().

    Raises:
        RuntimeError: If called outside an active actor_scope() — programming error.
    """
    actor = _current_actor.get()
    if actor is None:
        raise RuntimeError(
            "current_actor() called outside an active actor_scope(). "
            "Ensure actor_scope() wraps the tool call."
        )
    return actor


@asynccontextmanager
async def actor_scope() -> AsyncGenerator[AsyncSession]:
    """Scope provider implementing the _ScopeProvider seam.

    1. Retrieve the FastMCP AccessToken from the current request context.
       Raises ToolError("Authentication required…") when unauthenticated.
    2. Open a database transaction via session_scope().
    3. Resolve the token to an ActorContext via resolve_actor().
    4. Bind the actor to _current_actor for the duration of the scope,
       then reset it in the finally block.

    Yields:
        The open AsyncSession (same as session_scope()).

    Raises:
        fastmcp.exceptions.ToolError: If unauthenticated or identity cannot
            be resolved.
    """
    token = get_access_token()
    if token is None:
        raise ToolError(
            "Authentication required. Include a valid bearer token in the request."
        )

    async with session_scope() as db:
        actor = await resolve_actor(db, token)
        token_var = _current_actor.set(actor)
        try:
            yield db
        finally:
            _current_actor.reset(token_var)
