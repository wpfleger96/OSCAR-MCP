"""Shared fixtures for MCP roundtrip unit tests.

These fixtures provide an in-memory fastmcp.Client environment with the
lifespan patched to a lightweight mock — no real database or file-system
access is required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fastmcp
import pytest

from snore.mcp.server import SNORERuntime


@pytest.fixture
def mock_db_session() -> MagicMock:
    """Return a minimal AsyncSession mock that satisfies scalar queries.

    The mock models an empty database: every scalar query returns 0 or None,
    every collection query returns an empty list.  Tests that need specific
    return values should override ``session.execute`` with their own
    ``AsyncMock(side_effect=[...])`` after receiving this fixture.
    """
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one.return_value = 0
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    result_mock.scalars.return_value.first.return_value = None
    result_mock.one.return_value = (0, None, None)
    result_mock.all.return_value = []
    session.execute = AsyncMock(return_value=result_mock)
    return session


@pytest.fixture
def mcp_client_factory() -> Any:
    """Return a factory that yields a connected fastmcp.Client for in-memory tool tests.

    The factory signature is::

        async def factory(
            session: MagicMock,
            extra_patches: list | None = None,
        ) -> AsyncContextManager[fastmcp.Client]

    Usage::

        async def test_something(mcp_client_factory, mock_db_session):
            async with mcp_client_factory(mock_db_session) as client:
                result = await client.call_tool("get_data_overview", {})

    ``_lifespan`` is replaced with a stub that yields a ``SNORERuntime``
    backed by ``session`` (profile_id=1).  Any additional
    ``unittest.mock.patch`` context managers can be passed via
    ``extra_patches`` and are entered alongside the lifespan patch.

    Important: the returned factory is an async context manager.  Use it with
    ``async with``, not as a regular call.
    """
    from snore.mcp.server import make_server  # noqa: PLC0415

    @asynccontextmanager
    async def _client_context(
        session: MagicMock,
        extra_patches: list[Any] | None = None,
    ) -> AsyncIterator[fastmcp.Client]:
        @asynccontextmanager
        async def _mock_scope(s: MagicMock = session) -> Any:  # noqa: RUF029
            yield s

        @asynccontextmanager
        async def _fake_lifespan(
            app: Any,
            db_flag: str | None = None,
            profile_name: str = "neutral",
        ) -> Any:  # noqa: RUF029
            yield SNORERuntime(
                scope_provider=lambda: _mock_scope(session),
                profile_id=1,
            )

        mcp = make_server()
        with patch("snore.mcp.server._lifespan", _fake_lifespan):
            cm_stack = list(extra_patches or [])
            for cm in cm_stack:
                cm.__enter__()
            try:
                async with fastmcp.Client(mcp) as client:
                    yield client
            finally:
                for cm in reversed(cm_stack):
                    cm.__exit__(None, None, None)

    return _client_context
