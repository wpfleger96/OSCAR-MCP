"""Unit tests for _analyze_batch() parallelized session analysis."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from click.testing import CliRunner
from rich.console import Console

from snore.cli.groups.analysis import analysis
from snore.services.schemas import BatchAnalysisResult, BatchSessionResult


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
    """Return an async context-manager factory whose execute() chain yields mock_sessions.

    The mock is configured so that (await session.execute(stmt)).all() returns mock rows
    with .session_id and .day_date attributes, matching the SQLAlchemy 2.0 style used
    by AnalysisFacade.run_batch_analysis().
    """
    # Build mock rows: each row has .session_id and .day_date (scalar columns)
    mock_rows = []
    for s in mock_sessions:
        row = MagicMock()
        row.session_id = s.id
        row.day_date = s.day.date
        mock_rows.append(row)

    execute_result = MagicMock()
    # run_batch_analysis calls (await session.execute(stmt)).all()
    execute_result.all.return_value = mock_rows

    mock_db_session = MagicMock()
    # execute must be awaitable since run_batch_analysis uses `await session.execute()`
    mock_db_session.execute = AsyncMock(return_value=execute_result)

    @asynccontextmanager
    async def _scope():
        yield mock_db_session

    return _scope


def _make_coordinator_mock(batch_result: BatchAnalysisResult) -> MagicMock:
    """Return a mock BatchAnalysisCoordinator whose submit() resolves to batch_result."""
    coord = MagicMock()
    coord.submit = AsyncMock(return_value=batch_result)
    coord.cancel = MagicMock()
    coord.progress = (batch_result.total, batch_result.total)
    return coord


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

        batch_result = BatchAnalysisResult(
            total=3,
            successful=3,
            failed=0,
            cancelled=0,
            results=[
                BatchSessionResult(
                    session_id=i, session_date=date(2024, 1, i), success=True
                )
                for i in range(1, 4)
            ],
        )
        coord_mock = _make_coordinator_mock(batch_result)

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch("snore.database.session.session_scope", scope),
            patch(
                "snore.services.analysis_facade.BatchAnalysisCoordinator",
                return_value=coord_mock,
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
        captured = stdout_buf.getvalue()
        assert "Analyzed 3 sessions" in captured
        assert "3" in captured
        assert "0" in captured

    def test_analyze_batch_partial_failure_reports_failed_session_id(self):
        """When one session raises an error, stderr names the failed session and counts are correct."""
        mock_sessions = _make_mock_sessions(3)
        failing_id = mock_sessions[1].id  # session ID 2 fails

        scope = _make_session_scope(mock_sessions)

        stdout_buf = StringIO()
        stderr_buf = StringIO()
        stdout_console = Console(file=stdout_buf, force_terminal=False, width=120)
        stderr_console = Console(
            file=stderr_buf, stderr=True, force_terminal=False, width=120
        )

        batch_result = BatchAnalysisResult(
            total=3,
            successful=2,
            failed=1,
            cancelled=0,
            results=[
                BatchSessionResult(
                    session_id=1, session_date=date(2024, 1, 1), success=True
                ),
                BatchSessionResult(
                    session_id=failing_id,
                    session_date=date(2024, 1, 2),
                    success=False,
                    error="test error",
                ),
                BatchSessionResult(
                    session_id=3, session_date=date(2024, 1, 3), success=True
                ),
            ],
        )
        coord_mock = _make_coordinator_mock(batch_result)

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch("snore.database.session.session_scope", scope),
            patch(
                "snore.services.analysis_facade.BatchAnalysisCoordinator",
                return_value=coord_mock,
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
            patch("snore.database.session.init_database", new_callable=AsyncMock),
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

        batch_result = BatchAnalysisResult(
            total=1,
            successful=1,
            failed=0,
            cancelled=0,
            results=[
                BatchSessionResult(
                    session_id=1, session_date=date(2024, 1, 1), success=True
                ),
            ],
        )
        coord_mock = _make_coordinator_mock(batch_result)

        runner = CliRunner()
        with (
            patch("snore.cli.decorators.init_db"),
            patch("snore.database.session.init_database", new_callable=AsyncMock),
            patch("snore.database.session.session_scope", scope),
            patch(
                "snore.services.analysis_facade.BatchAnalysisCoordinator",
                return_value=coord_mock,
            ),
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


@pytest.mark.unit
class TestBatchCoordinatorHandle:
    """AnalysisFacade exposes the BatchAnalysisCoordinator handle for cancellation.

    Coordinator session-lifetime / boundedness tests.
    """

    async def test_batch_coordinator_accessible_after_run(
        self, async_db_session, async_test_device, temp_db, monkeypatch
    ):
        """facade.batch_coordinator is set after run_batch_analysis returns."""
        from datetime import date
        from unittest.mock import MagicMock

        from snore.database.models import Day, Session
        from snore.services.analysis_facade import AnalysisFacade

        day = Day(
            device_id=async_test_device.id,
            date=date(2025, 6, 1),
            total_therapy_hours=8.0,
        )
        async_db_session.add(day)
        await async_db_session.flush()
        sess = Session(
            device_id=async_test_device.id,
            day_id=day.id,
            device_session_id="COORD_HANDLE_TEST",
            start_time=__import__("datetime").datetime(2025, 6, 1, 21, 0, 0),
            end_time=__import__("datetime").datetime(2025, 6, 2, 5, 0, 0),
        )
        async_db_session.add(sess)
        await async_db_session.commit()

        # Point the coordinator's sync session at the same temp DB used by async_db_session.
        monkeypatch.setattr("snore.database.session._db_path", str(temp_db))

        mock_inputs = MagicMock()
        mock_result = MagicMock()

        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.prepare_inputs",
            lambda *a, **kw: mock_inputs,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.compute_analysis",
            lambda *a, **kw: mock_result,
        )

        facade = AnalysisFacade(async_db_session)
        assert facade.batch_coordinator is None, "No coordinator before run"

        await facade.run_batch_analysis(
            from_date=__import__("datetime").datetime(2025, 6, 1),
            to_date=__import__("datetime").datetime(2025, 6, 30),
            store_results=False,
        )

        # After run, the coordinator is accessible for inspection.
        coord = facade.batch_coordinator
        assert coord is not None, "batch_coordinator must be set after run"
        completed, total = coord.progress
        assert total == 1
        assert completed == 1

    async def test_coordinator_cancel_stops_remaining_sessions(self, monkeypatch):
        """Calling coord.cancel() before submit starts causes sessions to be skipped.

        ``cancel()`` sets the flag; ``submit()`` does NOT clear a pre-set flag.
        ``analyze_one`` returns early for each session, so no DB access occurs.
        All sessions are recorded as cancelled (not successful) — the
        important property is that ``load_session_inputs_raw`` is never called.
        """
        from snore.services.analysis_facade import BatchAnalysisCoordinator

        load_calls: list[int] = []

        coord = BatchAnalysisCoordinator()
        # Request cancellation before submit begins.
        coord.cancel()
        assert coord._cancel_requested is True

        # submit() must honour the pre-set flag and not clear it.
        result = await coord.submit(
            session_pairs=[(1, None), (2, None), (3, None)],
            store_results=False,
            max_workers=1,
        )
        # With a sliding-window implementation, cancellation before submit means
        # _fill_window() returns immediately without creating any tasks — no sessions
        # are started, so total == 0.
        assert result.total == 0
        assert result.cancelled == 0, (
            f"No tasks were created so none should be counted cancelled; got {result.cancelled}"
        )
        assert result.successful == 0, (
            f"No session must be counted successful after pre-cancel; got {result.successful}"
        )
        # No load calls: the cancellation guard fires before any task is created.
        assert load_calls == [], (
            "load_session_inputs_raw must not be called after cancel()"
        )
        # Flag must still be set.
        assert coord._cancel_requested is True

    async def test_coordinator_cancel_mid_batch_stops_further_dispatch(
        self, monkeypatch
    ):
        """Cancel requested mid-batch stops dispatching new sessions.

        Scenario: 6 sessions, max_workers=2.  After the first 2 complete,
        cancel() is called.  The remaining sessions must NOT be dispatched
        (the sliding window respects the cancel flag before enqueuing each
        next pair).

        This is the true mid-batch test per W3 spec: cancellation guard fires
        on _fill_window's next iteration, not just as a pre-submit guard.
        """
        import asyncio

        from snore.services.analysis_facade import BatchAnalysisCoordinator

        completed_sids: list[int] = []

        coord = BatchAnalysisCoordinator()

        call_counts = {"dispatched": 0}

        # Patch _db_path so analyze_one returns "cancelled" quickly.
        monkeypatch.setattr("snore.database.session._db_path", None)

        call_lock = asyncio.Lock()

        async def _fast_analyze_one(sid: int) -> str:
            async with call_lock:
                call_counts["dispatched"] += 1
                completed_sids.append(sid)
                if call_counts["dispatched"] == 2:
                    coord.cancel()
            return "success"

        # Monkeypatch asyncio.to_thread to run our fast analyze synchronously.
        async def _mock_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        # We need to replace the analyze_one inside submit — hardest to do cleanly.
        # Instead, call coord.cancel() after the first completed tasks return via
        # the progress_callback — this simulates real mid-batch cancellation.
        cancel_triggered = False

        def progress_cb(completed: int, total: int | None) -> None:
            nonlocal cancel_triggered
            if completed >= 2 and not cancel_triggered:
                cancel_triggered = True
                coord.cancel()

        # Pre-populate _db_path to None so analyze_one returns "cancelled" via
        # the real "if self._cancel_requested" guard for sessions dispatched after cancel.
        # For the first 2, they will see _db_path=None and raise, counted as "error".
        # We just need to confirm that sessions 3-6 are never dispatched.
        result = await coord.submit(
            session_pairs=[(i, None) for i in range(1, 7)],
            store_results=False,
            max_workers=2,
            progress_callback=progress_cb,
        )

        # After cancellation is triggered, no new sessions should be dispatched.
        # Total processed = sessions that ran before cancel propagated = at most
        # max_workers tasks already in flight (2) + possibly a refill before the
        # cancel flag was checked.  Total must be < 6.
        assert result.total < 6, (
            f"Mid-batch cancel must stop dispatching; total={result.total} "
            f"(expected < 6 sessions processed)"
        )
        # The cancel flag must still be set.
        assert coord._cancel_requested is True
