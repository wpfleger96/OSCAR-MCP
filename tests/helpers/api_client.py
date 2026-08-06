"""Shared TestClient factory for integration API tests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from snore.api.app import create_app
from snore.api.deps import get_actor, get_db
from snore.auth.actor import ActorContext, AuthMode
from snore.auth.factory import ActorContextFactory


def make_test_client(
    async_db_session: AsyncSession,
    *,
    actor: ActorContext | None = None,
    unauthenticated: bool = False,
    no_actor_override: bool = False,
    localhost: bool = False,
) -> TestClient:
    """Build a TestClient with get_db overridden for test isolation.

    Args:
        async_db_session: The shared async session to inject into the app.
        actor: A fixed ActorContext returned for every request.  When None
            and ``unauthenticated`` is False, auto-provisions a local admin.
        unauthenticated: Raise 401 on every request — simulates an absent or
            invalid session cookie.
        no_actor_override: Skip the get_actor override entirely so
            AuthMiddleware runs normally (required for endpoints that read the
            session cookie directly, e.g. demo-login tests).
        localhost: Wrap the ASGI app in a thin middleware that sets the
            client IP to ``127.0.0.1`` (required for localhost-only endpoints
            and rate-limit tests).

    The actor override always declares ``Depends(get_db)`` so FastAPI's
    per-request dependency cache resolves both the actor and route db params
    to the same override node, calling ``begin()`` exactly once per request.
    """
    app = create_app()

    async def _override_db():
        async with async_db_session.begin():
            yield async_db_session

    app.dependency_overrides[get_db] = _override_db

    if not no_actor_override:
        if unauthenticated:

            async def _override_actor(
                db: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                raise HTTPException(status_code=401, detail="Authentication required")

        elif actor is not None:
            _actor = actor

            async def _override_actor(
                db: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return _actor

        else:

            async def _override_actor(
                db: Annotated[AsyncSession, Depends(get_db)],
            ) -> ActorContext:
                return await ActorContextFactory(db).make_local(mode=AuthMode.LOCAL)

        app.dependency_overrides[get_actor] = _override_actor

    if localhost:

        class _LocalhostMiddleware:
            def __init__(self, inner: ASGIApp) -> None:
                self.app = inner

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                if scope["type"] == "http":
                    scope["client"] = ("127.0.0.1", 12345)
                await self.app(scope, receive, send)

        return TestClient(_LocalhostMiddleware(app), raise_server_exceptions=True)

    return TestClient(app, raise_server_exceptions=True)
