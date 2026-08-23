"""Unit tests for import-command profile lookup behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from snore.cli.commands.import_data import _resolve_profile_timezone
from snore.cli.decorators import CliCtx
from snore.database.models import Profile


@pytest.mark.unit
class TestResolveProfileTimezone:
    """Best-effort timezone resolution used by import dry runs."""

    async def test_resolution_failure_returns_none(self):
        """A missing actor profile falls back without querying the profile row."""

        @asynccontextmanager
        async def failed_profile_session(*args: Any, **kwargs: Any) -> Any:
            raise click.ClickException("no such profile")
            yield  # pragma: no cover

        with patch(
            "snore.cli.commands.import_data.profile_session",
            failed_profile_session,
        ):
            timezone = await _resolve_profile_timezone(
                "/tmp/snore.db", "user@example.com", "nightly"
            )

        assert timezone is None

    async def test_success_reads_timezone_from_resolved_profile(self):
        """Database and actor selectors reach the shared resolved session."""
        session = MagicMock()
        session.get = AsyncMock(
            return_value=SimpleNamespace(timezone="America/New_York")
        )
        received: list[tuple[str | None, str | None, str | None]] = []

        @asynccontextmanager
        async def tracked_profile_session(
            db: str | None,
            actor_user: str | None,
            actor_profile: str | None,
        ) -> Any:
            received.append((db, actor_user, actor_profile))
            yield CliCtx(db=session, profile_id=23)

        with patch(
            "snore.cli.commands.import_data.profile_session",
            tracked_profile_session,
        ):
            timezone = await _resolve_profile_timezone(
                "/tmp/snore.db", "user@example.com", "nightly"
            )

        assert timezone == "America/New_York"
        assert received == [("/tmp/snore.db", "user@example.com", "nightly")]
        session.get.assert_awaited_once_with(Profile, 23)
