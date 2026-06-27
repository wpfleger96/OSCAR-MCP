"""Unit tests for _analyze_batch() parallelized session analysis."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from click.testing import CliRunner
from rich.console import Console

from snore.cli.groups.analysis import analysis


def _make_mock_sessions(count: int) -> list[MagicMock]:
    """Return mock session objects with sequential .id attributes."""
    sessions = []
    for i in range(1, count + 1):
        s = MagicMock()
        s.id = i
        s.day.date = date(2024, 1, i)
        s.start_time.date.return_value = date(2024, 1, i)
        sessions.append(s)
    return sessions


def _make_session_scope(mock_sessions: list[MagicMock]) -> Any:
    """Return a context-manager factory whose query chain yields mock_sessions.

    The mock is configured so that chained .filter() calls (there are two when both
    --from and --to are supplied) still resolve to the same query object, allowing
    .order_by(...).all() to return the desired session list regardless of how many
    filter predicates are applied.
    """
    query_mock = MagicMock()
    # Make .filter() return the same query_mock so repeated filter calls still chain
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.all.return_value = mock_sessions

    mock_db_session = MagicMock()
    mock_db_session.query.return_value.join.return_value = query_mock

    @contextmanager
    def _scope():
        yield mock_db_session

    return _scope


@pytest.mark.unit
class TestAnalyzeBatch:
    """Tests for batch analysis via `analysis run --from --to`."""

    def test_analyze_batch_happy_path_reports_all_successful(self):
        """All sessions analyzed successfully reports correct success and failure counts."""
        mock_sessions = _make_mock_sessions(3)
        scope = _make_session_scope(mock_sessions)

        stdout_buf = StringIO()
        stderr_buf = StringIO()
        stdout_console = Console(file=stdout_buf, force_terminal=False, width=120)
        stderr_console = Console(
            file=stderr_buf, stderr=True, force_terminal=False, width=120
        )

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.session_scope", scope),
            patch("snore.analysis.service.AnalysisService.analyze_session"),
            patch("snore.cli.display.console", stdout_console),
            patch("snore.cli.display.err_console", stderr_console),
            patch("snore.cli.groups.analysis.console", stdout_console),
        ):
            result = runner.invoke(
                analysis,
                ["run", "--from", "2024-01-01", "--to", "2024-01-31"],
            )

        assert result.exit_code == 0, result.output
        captured = stdout_buf.getvalue()
        assert "Analyzed 3 sessions" in captured
        assert "3" in captured
        assert "0" in captured

    def test_analyze_batch_partial_failure_reports_failed_session_id(self):
        """When one session raises an error, stderr names the failed session and counts are correct."""
        mock_sessions = _make_mock_sessions(3)
        failing_id = mock_sessions[1].id  # session ID 2 fails

        scope = _make_session_scope(mock_sessions)

        def analyze_side_effect(session_id, modes, store_results):
            if session_id == failing_id:
                raise RuntimeError("test error")

        stdout_buf = StringIO()
        stderr_buf = StringIO()
        stdout_console = Console(file=stdout_buf, force_terminal=False, width=120)
        stderr_console = Console(
            file=stderr_buf, stderr=True, force_terminal=False, width=120
        )

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.session_scope", scope),
            patch(
                "snore.analysis.service.AnalysisService.analyze_session",
                side_effect=analyze_side_effect,
            ),
            patch("snore.cli.display.console", stdout_console),
            patch("snore.cli.display.err_console", stderr_console),
            patch("snore.cli.groups.analysis.console", stdout_console),
        ):
            result = runner.invoke(
                analysis,
                ["run", "--from", "2024-01-01", "--to", "2024-01-31"],
            )

        assert result.exit_code == 0, result.output
        stdout_captured = stdout_buf.getvalue()
        # successful=2, failed=1
        assert "2" in stdout_captured
        assert "1" in stdout_captured
        # the failed session ID must appear in the warning output
        assert str(failing_id) in stdout_captured

    def test_analyze_batch_empty_sessions_prints_no_sessions_found(self):
        """When query returns no sessions, the empty-result message is printed and command exits cleanly."""
        scope = _make_session_scope([])

        stdout_buf = StringIO()
        stdout_console = Console(file=stdout_buf, force_terminal=False, width=120)

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.session_scope", scope),
            patch("snore.cli.display.console", stdout_console),
            patch("snore.cli.groups.analysis.console", stdout_console),
        ):
            result = runner.invoke(
                analysis,
                ["run", "--from", "2024-01-01", "--to", "2024-01-31"],
            )

        assert result.exit_code == 0, result.output
        assert "No sessions found" in stdout_buf.getvalue()

    def test_analyze_batch_single_session_reports_one_successful(self):
        """A single matching session is analyzed and reported with the correct session count."""
        mock_sessions = _make_mock_sessions(1)
        scope = _make_session_scope(mock_sessions)

        stdout_buf = StringIO()
        stdout_console = Console(file=stdout_buf, force_terminal=False, width=120)

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.session_scope", scope),
            patch("snore.analysis.service.AnalysisService.analyze_session"),
            patch("snore.cli.display.console", stdout_console),
            patch("snore.cli.groups.analysis.console", stdout_console),
        ):
            result = runner.invoke(
                analysis,
                ["run", "--from", "2024-01-01", "--to", "2024-01-31"],
            )

        assert result.exit_code == 0, result.output
        captured = stdout_buf.getvalue()
        assert "Analyzed 1 sessions" in captured
        assert "1" in captured
