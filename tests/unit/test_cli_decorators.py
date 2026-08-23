"""Unit tests for profile-scoped CLI decorators and one-shot resolution."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from click.testing import CliRunner

from snore.cli.decorators import (
    CliCtx,
    profile_scoped_command,
    resolve_profile_id_once,
)


def _make_session_scope(session: Any) -> Any:
    """Return an async context-manager factory yielding ``session``."""

    @asynccontextmanager
    async def _scope(*args: Any, **kwargs: Any) -> Any:
        yield session

    return _scope


def _patches(session: Any, *, profile_id: int = 7) -> Any:
    """Patch the DB init/scope and profile resolver used by db_session.

    ``db_session`` imports ``init_database``/``session_scope`` from
    ``snore.database.session`` at call time, and the shared profile session
    imports ``resolve_cli_profile_id`` from ``snore.auth.factory`` at call
    time, so patching those module attributes intercepts both entry points.
    """
    resolve = AsyncMock(return_value=profile_id)
    return (
        patch("snore.database.session.init_database", new_callable=AsyncMock),
        patch("snore.database.session.session_scope", _make_session_scope(session)),
        patch("snore.auth.factory.resolve_cli_profile_id", resolve),
    ), resolve


@pytest.mark.unit
class TestResolveProfileIdOnce:
    """Behavior of the short-lived CLI profile resolver."""

    async def test_returns_profile_id_after_session_closes(self, tmp_path):
        """The helper forwards actor options and closes its session before returning."""
        session = MagicMock()
        events: list[str] = []

        @asynccontextmanager
        async def tracked_scope(*args: Any, **kwargs: Any) -> Any:
            events.append("open")
            try:
                yield session
            finally:
                events.append("closed")

        init = AsyncMock()
        resolve = AsyncMock(return_value=17)
        db_path = tmp_path / "snore.db"

        with (
            patch("snore.database.session.init_database", init),
            patch("snore.database.session.session_scope", tracked_scope),
            patch("snore.auth.factory.resolve_cli_profile_id", resolve),
        ):
            profile_id = await resolve_profile_id_once(
                str(db_path), "user@example.com", "nightly"
            )

        assert profile_id == 17
        assert events == ["open", "closed"]
        init.assert_awaited_once_with(str(db_path))
        resolve.assert_awaited_once_with(session, "user@example.com", "nightly")

    async def test_resolution_failure_closes_session_and_propagates(self):
        """A ClickException propagates after the short-lived session closes."""
        session = MagicMock()
        closed = False

        @asynccontextmanager
        async def tracked_scope(*args: Any, **kwargs: Any) -> Any:
            nonlocal closed
            try:
                yield session
            finally:
                closed = True

        resolve = AsyncMock(side_effect=click.ClickException("no such profile"))
        with (
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch("snore.database.session.session_scope", tracked_scope),
            patch("snore.auth.factory.resolve_cli_profile_id", resolve),
            pytest.raises(click.ClickException, match="no such profile"),
        ):
            await resolve_profile_id_once(None, None, None)

        assert closed is True


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

    def test_body_runs_before_profile_session_closes(self):
        """The decorator keeps its resolved session open through the command body."""
        session = MagicMock()
        events: list[str] = []

        @asynccontextmanager
        async def tracked_scope(*args: Any, **kwargs: Any) -> Any:
            events.append("open")
            try:
                yield session
            finally:
                events.append("closed")

        async def resolve(*args: Any, **kwargs: Any) -> int:
            events.append("resolve")
            return 7

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            assert ctx.db is session
            events.append("body")

        with (
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch("snore.database.session.session_scope", tracked_scope),
            patch("snore.auth.factory.resolve_cli_profile_id", resolve),
        ):
            result = CliRunner().invoke(dummy)

        assert result.exit_code == 0, result.output
        assert events == ["open", "resolve", "body", "closed"]

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

    def test_body_runtime_error_surfaces_and_exits_nonzero(self):
        """A non-Click exception in the body propagates out and exits non-zero.

        The fake ``session_scope`` here yields the session directly and does not
        model commit/rollback, so the rollback path is not observable; the
        contract asserted is that a plain ``RuntimeError`` is not swallowed.
        """

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            raise RuntimeError("boom")

        session = MagicMock()
        (init_p, scope_p, resolve_p), _ = _patches(session)

        runner = CliRunner()
        with init_p, scope_p, resolve_p:
            result = runner.invoke(dummy)

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
        assert "boom" in str(result.exception)

    def test_actor_envvars_forwarded_to_resolver(self):
        """SNORE_USER/SNORE_PROFILE envvars reach resolve_cli_profile_id without flags."""

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            click.echo("ok")

        session = MagicMock()
        (init_p, scope_p, resolve_p), resolve = _patches(session)

        runner = CliRunner()
        with init_p, scope_p, resolve_p:
            result = runner.invoke(
                dummy,
                env={"SNORE_USER": "env@example.com", "SNORE_PROFILE": "envprof"},
            )

        assert result.exit_code == 0, result.output
        resolve.assert_awaited_once_with(session, "env@example.com", "envprof")

    def test_tilde_db_path_missing_fails_before_session(self, tmp_path, monkeypatch):
        """A tilde --db path is expanded and, if missing, rejected before any session opens."""
        monkeypatch.setenv("HOME", str(tmp_path))

        ran = False

        @click.command("dummy")
        @profile_scoped_command
        async def dummy(ctx: CliCtx) -> None:
            nonlocal ran
            ran = True

        # No patches: the guard must fire before any DB or resolver work.
        result = CliRunner().invoke(dummy, ["--db", "~/definitely-missing.db"])

        assert result.exit_code == 1
        assert "Database not found" in result.output
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
