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
        """cleanup_orphaned_records is executed inside write_gate."""
        gate_calls: list[str] = []

        @asynccontextmanager
        async def recording_gate():
            gate_calls.append("enter")
            yield
            gate_calls.append("exit")

        @asynccontextmanager
        async def mock_session_scope():
            yield AsyncMock()

        with (
            patch("snore.services.import_service.write_gate", recording_gate),
            patch("snore.services.import_service.session_scope", mock_session_scope),
            patch(
                "snore.services.import_service.SessionImporter.cleanup_orphaned_records",
                new=AsyncMock(return_value=0),
            ),
            patch("snore.services.import_service.register_all_parsers"),
            patch(
                "snore.services.import_service.parser_registry.detect_all_parsers",
                return_value=[],
            ),
        ):
            from snore.services.import_service import ImportService

            service = ImportService()
            await service.import_sources(sources=[], dry_run=False, profile_id=1)

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
        async def mock_session_scope():
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
