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


def _pairs_as_stream(pairs: list[tuple[int, Any]]) -> Any:
    """Wrap a list of (session_id, day_date) pairs in an async iterable.

    Simulates what ``AsyncSession.stream()`` returns — an object whose
    ``__aiter__`` yields rows with ``.session_id`` and ``.day_date`` attributes.
    Used by coordinator unit tests that need to drive ``submit()`` without a
    real database session.
    """

    class _Row:
        def __init__(self, session_id: int, day_date: Any) -> None:
            self.session_id = session_id
            self.day_date = day_date

    class _AsyncIterable:
        def __init__(self) -> None:
            self._iter = iter([_Row(sid, dd) for sid, dd in pairs])

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> _Row:
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration from None

    return _AsyncIterable()


def _make_session_scope(mock_sessions: list[MagicMock]) -> Any:
    """Return an async context-manager factory whose session mocks the new
    run_batch_analysis API: COUNT query via execute() + lazy streaming via stream().

    run_batch_analysis now:
    1. Calls ``await session.execute(count_stmt)`` → ``.scalar_one()`` for the total.
    2. Calls ``await session.stream(stmt)`` → async iterable of rows.

    The mock must support both, regardless of call order.
    """
    n = len(mock_sessions)

    # COUNT query result: scalar_one() returns n.
    count_result = MagicMock()
    count_result.scalar_one.return_value = n

    mock_db_session = MagicMock()
    mock_db_session.execute = AsyncMock(return_value=count_result)

    # stream() must return an async iterable of rows with .session_id and .day_date.
    class _Row:
        def __init__(self, sid: int, day_date: Any) -> None:
            self.session_id = sid
            self.day_date = day_date

    class _AsyncStream:
        def __init__(self) -> None:
            self._rows = iter([_Row(s.id, s.day.date) for s in mock_sessions])

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> _Row:
            try:
                return next(self._rows)
            except StopIteration:
                raise StopAsyncIteration from None

    mock_db_session.stream = AsyncMock(return_value=_AsyncStream())

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
        # With pre-cancel, _fill_window checks the flag before pulling any pair,
        # so total = matched_total (3) and all 3 are cancelled via drain.
        result = await coord.submit(
            matched_total=3,
            session_stream=_pairs_as_stream([(1, None), (2, None), (3, None)]),
            store_results=False,
            max_workers=1,
        )
        # With a pre-set cancel flag: _fill_window returns immediately without
        # creating any tasks.  The drain loop then accounts for all 3 pairs as cancelled.
        assert result.total == 3
        assert result.cancelled == 3, (
            f"All 3 unstarted pairs must be drained as cancelled; got {result.cancelled}"
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

    async def test_coordinator_cancel_mid_batch_stops_further_dispatch(self):
        """Cancel requested mid-batch stops dispatching new sessions and accounts all.

        Scenario: 20 sessions, max_workers=2.  After the first window (2 sessions)
        is dispatched and completes, cancel() is called via progress_callback.
        The remaining 18 sessions must NOT be dispatched (cancel flag checked in
        _fill_window before each pull), and must be accounted as cancelled in the
        result (total=20, cancelled>=18).

        This is the true mid-batch test per W3 spec: cancellation guard fires
        on _fill_window's next iteration, not just as a pre-submit guard.
        It uses real async tasks with a controlled blocking mechanism so
        cancellation timing is deterministic.
        """
        import asyncio  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.services.analysis_facade import (  # noqa: PLC0415
            BatchAnalysisCoordinator,
        )

        n_total = 20
        max_workers = 2
        coord = BatchAnalysisCoordinator()

        dispatched: list[int] = []
        dispatch_lock = asyncio.Lock()
        # Barrier: first window must complete before cancel fires.
        first_window_done = asyncio.Event()

        # Patch asyncio.to_thread so analyze_one never touches the DB.

        async def instrumented_to_thread(func, *args, **kwargs):
            sid = args[0] if args else None
            async with dispatch_lock:
                if sid is not None:
                    dispatched.append(sid)
                # Signal after the first window fills.
                if len(dispatched) >= max_workers:
                    first_window_done.set()
            # Return "success" for dispatched sessions.
            return "success"

        with unittest.mock.patch(
            "asyncio.to_thread", side_effect=instrumented_to_thread
        ):
            # progress_callback triggers cancel after the first window completes.
            def on_progress(completed: int, total: int | None) -> None:
                if completed >= max_workers:
                    coord.cancel()

            pairs = [(i, None) for i in range(1, n_total + 1)]
            result = await coord.submit(
                matched_total=n_total,
                session_stream=_pairs_as_stream(pairs),
                store_results=False,
                max_workers=max_workers,
                progress_callback=on_progress,
            )

        # Invariants:
        # 1. total == matched_total (always)
        assert result.total == n_total, (
            f"total must equal matched_total={n_total}, got {result.total}"
        )
        # 2. successful + failed + cancelled == total
        assert result.successful + result.failed + result.cancelled == n_total, (
            f"Accounting mismatch: {result.successful}+{result.failed}+{result.cancelled} "
            f"!= {n_total}"
        )
        # 3. At most max_workers sessions were dispatched to threads.
        assert len(dispatched) <= max_workers * 2, (
            f"Too many sessions dispatched after cancel: {len(dispatched)}"
        )
        # 4. At least (n_total - max_workers*2) were cancelled without dispatch.
        assert result.cancelled >= n_total - max_workers * 2, (
            f"Not enough sessions counted as cancelled: {result.cancelled} "
            f"(expected >= {n_total - max_workers * 2})"
        )
        # 5. Cancel flag still set.
        assert coord._cancel_requested is True

    async def test_coordinator_window_bounded_for_large_batch(self):
        """Coordinator must never have more than max_workers tasks in flight.

        Probe: 10,000 sessions, max_workers=4.  At no point should more than
        max_workers asyncio tasks be simultaneously in flight.  Also verifies
        that session_dates (retained metadata) never exceeds max_workers at
        any checkpoint — entries are pruned as tasks complete so the
        coordinator's retained metadata stays window-bounded (O(max_workers)).
        """
        import asyncio  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.services.analysis_facade import (  # noqa: PLC0415
            BatchAnalysisCoordinator,
        )

        n_total = 10_000
        max_workers = 4
        coord = BatchAnalysisCoordinator()

        peak_in_flight: list[int] = []
        in_flight = 0
        in_flight_lock = asyncio.Lock()

        async def counting_to_thread(func, *args, **kwargs):
            nonlocal in_flight
            async with in_flight_lock:
                in_flight += 1
                peak_in_flight.append(in_flight)
                # Snapshot session_dates size at each enqueue — must be window-bounded.
                peak_in_flight.append(len(coord.session_dates))
            await asyncio.sleep(0)  # yield to let other tasks start
            async with in_flight_lock:
                in_flight -= 1
            return "success"

        with unittest.mock.patch("asyncio.to_thread", side_effect=counting_to_thread):
            pairs = [(i, None) for i in range(1, n_total + 1)]
            result = await coord.submit(
                matched_total=n_total,
                session_stream=_pairs_as_stream(pairs),
                store_results=False,
                max_workers=max_workers,
            )

        assert result.total == n_total
        assert result.successful == n_total
        assert result.cancelled == 0
        assert result.failed == 0

        observed_peak = max(peak_in_flight[0::2]) if peak_in_flight else 0
        assert observed_peak <= max_workers, (
            f"Peak in-flight tasks was {observed_peak}, expected <= {max_workers}. "
            "Sliding window is not bounding task concurrency correctly."
        )
        # session_dates is keyed by the same sid set as `pending` — entries are
        # added at enqueue and popped at task completion.  Snapshots taken at
        # each enqueue must also stay window-bounded.
        observed_sd_peak = max(peak_in_flight[1::2]) if peak_in_flight else 0
        assert observed_sd_peak <= max_workers, (
            f"session_dates peak was {observed_sd_peak}, expected <= {max_workers}. "
            "Retained metadata is not staying window-bounded."
        )
        # After submit() completes, session_dates must be fully drained.
        assert len(coord.session_dates) == 0, (
            f"session_dates has {len(coord.session_dates)} entries after submit(); "
            "expected 0 — all entries should be popped on task completion."
        )
