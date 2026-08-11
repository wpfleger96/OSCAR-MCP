"""Unit tests for the shared background write gate."""

from __future__ import annotations

import asyncio
import threading
import time

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from snore.database.write_gate import _gate, write_gate

pytestmark = pytest.mark.unit


class TestWriteGateSerializes:
    """Two concurrent event loops: only one holds the gate at a time."""

    def test_strict_serialization(self) -> None:
        """The second acquirer starts only after the first releases the gate."""
        hold_seconds = 0.05
        intervals: list[tuple[float, float]] = []
        t1_acquired = threading.Event()
        ref = time.monotonic()

        def run_holder(
            duration: float, *, signal: threading.Event | None = None
        ) -> None:
            async def _inner() -> None:
                async with write_gate():
                    if signal is not None:
                        signal.set()
                    start = time.monotonic() - ref
                    await asyncio.sleep(duration)
                    end = time.monotonic() - ref
                    intervals.append((start, end))

            asyncio.run(_inner())

        t1 = threading.Thread(
            target=run_holder, kwargs={"duration": hold_seconds, "signal": t1_acquired}
        )
        t2 = threading.Thread(target=run_holder, kwargs={"duration": hold_seconds})
        t1.start()
        t1_acquired.wait(timeout=2)  # Wait until t1 has acquired the gate.
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(intervals) == 2, f"Expected 2 completed intervals, got {intervals}"
        intervals.sort()
        (_, a_end), (b_start, _) = intervals
        assert b_start >= a_end - 0.002, (
            f"Intervals overlapped: first ended at {a_end:.3f}s, "
            f"second started at {b_start:.3f}s"
        )


class TestWriteGateReleasesOnException:
    """Gate is always released even when the body raises."""

    async def test_release_on_exception(self) -> None:
        with pytest.raises(ValueError, match="boom"):
            async with write_gate():
                raise ValueError("boom")

        # Lock must be free — a non-blocking acquire must succeed immediately.
        acquired = _gate.acquire(blocking=False)
        assert acquired, "Gate was not released after an exception in the body"
        _gate.release()

    async def test_gate_reusable_after_exception(self) -> None:
        """Subsequent acquisition succeeds without blocking after a failed context."""
        with pytest.raises(RuntimeError):
            async with write_gate():
                raise RuntimeError("fail")

        entered = False
        async with write_gate():
            entered = True
        assert entered


class TestWriteGateWiring:
    """Import service and analysis facade enter the gate for their write paths."""

    async def test_import_service_cleanup_enters_gate(self) -> None:
        """cleanup_orphaned_records is executed inside write_gate when force_cleanup=True."""
        gate_calls: list[str] = []

        @asynccontextmanager
        async def recording_gate():
            gate_calls.append("enter")
            yield
            gate_calls.append("exit")

        @asynccontextmanager
        async def mock_session_scope(*args, **kwargs):
            # get() -> None so the profile-timezone preflight sees no profile.
            yield AsyncMock(get=AsyncMock(return_value=None))

        with (
            patch("snore.services.import_service.write_gate", recording_gate),
            patch("snore.services.import_service.session_scope", mock_session_scope),
            patch(
                "snore.services.import_service.SessionImporter.cleanup_orphaned_records",
                new=AsyncMock(return_value={}),
            ),
            patch("snore.services.import_service.ensure_registered_parsers"),
            patch(
                "snore.services.import_service.parser_registry.detect_all_parsers",
                return_value=[],
            ),
        ):
            from snore.services.import_service import ImportService

            service = ImportService()
            await service.import_sources(
                sources=[], dry_run=False, profile_id=1, force_cleanup=True
            )

        assert gate_calls == ["enter", "exit"], (
            f"Expected write_gate entered once for orphan cleanup, got {gate_calls}"
        )

    async def test_analysis_facade_run_analysis_enters_gate(self) -> None:
        """The store_result write phase in run_analysis executes inside write_gate."""
        gate_calls: list[str] = []

        @asynccontextmanager
        async def recording_gate():
            gate_calls.append("enter")
            yield
            gate_calls.append("exit")

        _scope_call = 0
        mock_read_db = AsyncMock()
        mock_read_result = MagicMock()
        mock_read_result.scalar_one_or_none.return_value = 42  # session is owned
        mock_read_db.execute = AsyncMock(return_value=mock_read_result)
        mock_write_db = AsyncMock()

        @asynccontextmanager
        async def mock_session_scope(*args, **kwargs):
            nonlocal _scope_call
            _scope_call += 1
            yield mock_read_db if _scope_call == 1 else mock_write_db

        mock_computation = MagicMock()
        mock_computation.summary = MagicMock()

        mock_svc = MagicMock()
        mock_svc.load_session_inputs_raw = AsyncMock(return_value=MagicMock())
        mock_svc.store_result = AsyncMock()
        mock_svc.compute_analysis = MagicMock(return_value=mock_computation)

        mock_analysis_cls = MagicMock()
        mock_analysis_cls.return_value = mock_svc
        mock_analysis_cls.prepare_inputs = MagicMock(return_value=MagicMock())

        with (
            patch("snore.database.write_gate.write_gate", recording_gate),
            patch("snore.database.session.session_scope", mock_session_scope),
            patch("snore.analysis.service.AnalysisService", mock_analysis_cls),
        ):
            from snore.services.analysis_facade import AnalysisFacade

            facade = AnalysisFacade(db_session=MagicMock(), profile_id=1)
            await facade.run_analysis(session_id=42, store_results=True)

        assert "enter" in gate_calls, (
            "write_gate was not entered during run_analysis store phase"
        )
        assert "exit" in gate_calls, (
            "write_gate was not exited cleanly during run_analysis store phase"
        )


class TestWriteGateCancellationSafety:
    """Cancellation of a waiter must not leak the lock and deadlock future callers."""

    async def test_cancelled_waiter_releases_gate_when_acquire_completes(self) -> None:
        """Cancelling write_gate() after the acquire task completes does not leak the lock.

        Protocol:
        1. Hold ``_gate`` from a helper thread so any new ``write_gate()`` call
           blocks in ``asyncio.to_thread(_gate.acquire)``.
        2. Start ``write_gate()`` as an asyncio Task — it blocks waiting for the lock.
        3. Cancel the Task (simulating a caller that timed out or was cancelled).
        4. Release the helper thread's hold so the shielded acquire task can complete.
        5. Give the event loop a moment to run the done-callback.
        6. Assert ``_gate`` is immediately acquirable (non-blocking) — the callback
           released the lock, so no future caller can deadlock.
        """
        import threading  # noqa: PLC0415

        from snore.database.write_gate import _gate, write_gate  # noqa: PLC0415

        # Step 1: hold _gate from a helper thread.
        hold_release = threading.Event()
        hold_acquired = threading.Event()

        def holder() -> None:
            _gate.acquire()
            hold_acquired.set()
            hold_release.wait(timeout=5)
            _gate.release()

        holder_thread = threading.Thread(target=holder, daemon=True)
        holder_thread.start()
        assert hold_acquired.wait(timeout=2), "Holder thread did not acquire the lock"

        # Step 2: start write_gate() as a task — blocked waiting for _gate.
        async def enter_gate() -> None:
            async with write_gate():
                pass  # only reached if not cancelled before the acquire

        gate_task = asyncio.create_task(enter_gate())
        # Yield briefly so gate_task starts and reaches asyncio.to_thread.
        await asyncio.sleep(0.02)

        # Step 3: cancel the task while it is blocked in the acquire wait.
        gate_task.cancel()
        await asyncio.gather(gate_task, return_exceptions=True)

        # Step 4: release the holder so the shielded acquire_task can complete.
        hold_release.set()
        holder_thread.join(timeout=2)

        # Step 5: give the event loop a moment to run the done-callback which
        # releases _gate if the acquire completed after cancellation.
        await asyncio.sleep(0.05)

        # Step 6: assert the gate is free — non-blocking acquire must succeed.
        acquired = _gate.acquire(blocking=False)
        assert acquired, (
            "write_gate leaked the lock after cancellation — "
            "the done-callback did not release it"
        )
        _gate.release()
