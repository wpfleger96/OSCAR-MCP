"""Unit tests for the profile_scoped_command decorator and CliCtx."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from click.testing import CliRunner

from snore.cli.decorators import CliCtx, profile_scoped_command


def _make_session_scope(session: Any) -> Any:
    """Return an async context-manager factory yielding ``session``."""

    @asynccontextmanager
    async def _scope(*args: Any, **kwargs: Any) -> Any:
        yield session

    return _scope


def _patches(session: Any, *, profile_id: int = 7) -> Any:
    """Patch the DB init/scope and profile resolver used by db_session.

    ``db_session`` imports ``init_database``/``session_scope`` from
    ``snore.database.session`` at call time, and ``profile_scoped_command``
    imports ``resolve_cli_profile_id`` from ``snore.auth.factory`` at call
    time, so patching those module attributes intercepts both.
    """
    resolve = AsyncMock(return_value=profile_id)
    return (
        patch("snore.database.session.init_database", new_callable=AsyncMock),
        patch("snore.database.session.session_scope", _make_session_scope(session)),
        patch("snore.auth.factory.resolve_cli_profile_id", resolve),
    ), resolve


@pytest.mark.unit
class TestProfileScopedCommand:
    """Behavior of the profile_scoped_command decorator."""

    def test_body_receives_ctx_with_session_and_profile_id(self):
        """The body runs with a CliCtx carrying the session and resolved profile id."""
        captured: dict[str, Any] = {}

        @click.command("dummy")
        @click.option("--flag", is_flag=True)
        @profile_scoped_command
        async def dummy(ctx: CliCtx, flag: bool) -> None:
            captured.update(db=ctx.db, profile_id=ctx.profile_id, flag=flag)
            click.echo("ok")

        session = MagicMock()
        (init_p, scope_p, resolve_p), resolve = _patches(session, profile_id=7)

        runner = CliRunner()
        with init_p, scope_p, resolve_p:
            result = runner.invoke(dummy, ["--flag"])

        assert result.exit_code == 0, result.output
        assert "ok" in result.output
        assert captured["db"] is session
        assert captured["profile_id"] == 7
        assert captured["flag"] is True
        resolve.assert_awaited_once_with(session, None, None)

    def test_nonexistent_db_path_fails_before_session(self, tmp_path):
        """An explicit --db pointing at a missing file is rejected before the body runs."""
        ran = False

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            nonlocal ran
            ran = True

        missing = tmp_path / "nope.db"

        # No patches: the guard must fire before any DB or resolver work.
        result = CliRunner().invoke(dummy, ["--db", str(missing)])

        assert result.exit_code == 1
        assert "Database not found" in result.output
        assert str(missing) in result.output
        assert ran is False

    def test_existing_db_path_passes_guard(self, tmp_path):
        """An explicit --db that exists passes the guard and runs the body."""
        existing = tmp_path / "real.db"
        existing.touch()

        ran = False

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            nonlocal ran
            ran = True

        session = MagicMock()
        (init_p, scope_p, resolve_p), _ = _patches(session)

        runner = CliRunner()
        with init_p, scope_p, resolve_p:
            result = runner.invoke(dummy, ["--db", str(existing)])

        assert result.exit_code == 0, result.output
        assert ran is True

    def test_actor_options_forwarded_to_resolver(self):
        """--user/--profile values are passed through to resolve_cli_profile_id."""

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            click.echo("ok")

        session = MagicMock()
        (init_p, scope_p, resolve_p), resolve = _patches(session)

        runner = CliRunner()
        with init_p, scope_p, resolve_p:
            result = runner.invoke(
                dummy, ["--user", "u@example.com", "--profile", "main"]
            )

        assert result.exit_code == 0, result.output
        resolve.assert_awaited_once_with(session, "u@example.com", "main")

    def test_return_value_propagates(self):
        """The wrapper returns whatever the async body returns."""
        sentinel = object()

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> Any:
            return sentinel

        session = MagicMock()
        (init_p, scope_p, resolve_p), _ = _patches(session)

        assert dummy.callback is not None
        with init_p, scope_p, resolve_p:
            # CliRunner discards return values, so call the wrapper directly.
            returned = dummy.callback(db=None, actor_user=None, actor_profile=None)

        assert returned is sentinel

    def test_click_exception_in_body_reports_error_and_nonzero_exit(self):
        """A ClickException raised in the body surfaces its message and exits non-zero."""

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            raise click.ClickException("boom")

        session = MagicMock()
        (init_p, scope_p, resolve_p), _ = _patches(session)

        runner = CliRunner()
        with init_p, scope_p, resolve_p:
            result = runner.invoke(dummy)

        assert result.exit_code == 1
        assert "boom" in result.output

    def test_resolution_failure_aborts_before_body(self):
        """When profile resolution fails, the body never runs and the error surfaces."""
        ran = False

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            nonlocal ran
            ran = True

        session = MagicMock()
        resolve = AsyncMock(side_effect=click.ClickException("no such profile"))

        runner = CliRunner()
        with (
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch(
                "snore.database.session.session_scope",
                _make_session_scope(session),
            ),
            patch("snore.auth.factory.resolve_cli_profile_id", resolve),
        ):
            result = runner.invoke(dummy)

        assert result.exit_code == 1
        assert "no such profile" in result.output
        assert ran is False

    def test_help_does_not_touch_database(self):
        """--help renders the bundled options without any DB or resolver patches."""

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            click.echo("ok")

        result = CliRunner().invoke(dummy, ["--help"])

        assert result.exit_code == 0, result.output
        assert "--db" in result.output
        assert "--user" in result.output
        assert "--profile" in result.output
