"""Unit tests for the shared single-session analysis runner (run_one)."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.runner import Dispatch, run_one
from snore.analysis.types import AnalysisComputation, AnalysisResult
from snore.database.models import Day, Device, Session
from snore.exceptions import NotFoundError

_DUMMY_FLOW_BLOB = b"flowbytes"
_DUMMY_FLOW_META: dict = {"sample_rate_hz": 25.6}
_DUMMY_FLOW_COUNT = 100


def _patch_waveform() -> Any:
    """Patch fetch_waveform_blob so load_session_inputs_raw needs no real blobs."""
    return patch(
        "snore.analysis.service.fetch_waveform_blob",
        new=AsyncMock(
            return_value=(_DUMMY_FLOW_BLOB, _DUMMY_FLOW_COUNT, _DUMMY_FLOW_META)
        ),
    )


def _patch_events() -> Any:
    return patch(
        "snore.analysis.service.AnalysisService._load_machine_events",
        new=AsyncMock(return_value=[]),
    )


def _fake_summary(session_id: int) -> AnalysisResult:
    return AnalysisResult(
        session_id=session_id,
        session_duration_hours=8.0,
        total_breaths=100,
        machine_events=[],
        mode_results={},
    )


async def _seed_session(
    db_session: AsyncSession, device: Device, day_date: date
) -> Session:
    """Insert a Day + Session and return the flushed Session."""
    day = Day(device_id=device.id, date=day_date, total_therapy_hours=8.0)
    db_session.add(day)
    await db_session.flush()
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"runner_{day_date.isoformat()}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time()) + timedelta(hours=8),
        duration_seconds=28800,
    )
    db_session.add(sess)
    await db_session.flush()
    return sess


@pytest.fixture
def patched_session_scope(async_db_session, monkeypatch):
    """Patch session_scope so run_one's read scope uses the test session."""

    @asynccontextmanager
    async def _fake_scope(*args, **kwargs):
        yield async_db_session

    monkeypatch.setattr("snore.database.session.session_scope", _fake_scope)
    return async_db_session


@pytest.fixture
def patched_compute(monkeypatch):
    """Patch prepare_inputs/compute_analysis with deterministic fakes.

    Returns a dict capturing the raw DTO seen by prepare_inputs, so tests can
    assert on primary_mode propagation without running real NumPy work.
    """
    captured: dict[str, Any] = {}

    def _fake_prepare(raw):
        captured["raw"] = raw
        return raw  # pass the DTO straight through as "inputs"

    def _fake_compute(self, inputs):
        return AnalysisComputation(
            summary=_fake_summary(inputs.session_id),
            breaths=[],
            primary_mode=inputs.primary_mode,
        )

    monkeypatch.setattr(
        "snore.analysis.service.AnalysisService.prepare_inputs",
        staticmethod(_fake_prepare),
    )
    monkeypatch.setattr(
        "snore.analysis.service.AnalysisService.compute_analysis", _fake_compute
    )
    return captured


@pytest.fixture
def captured_stores(monkeypatch):
    """Patch runner.store_with_retry, capturing (profile_id, computation) calls."""
    calls: list[tuple[int, AnalysisComputation]] = []

    async def _fake_store(profile_id, computation, processing_time_ms):
        calls.append((profile_id, computation))

    monkeypatch.setattr("snore.analysis.runner.store_with_retry", _fake_store)
    return calls


@pytest.mark.unit
class TestRunOne:
    """run_one: dispatch parity, ownership, and primary_mode propagation."""

    async def test_inline_and_thread_dispatch_store_identical_results(
        self,
        patched_session_scope,
        async_test_device,
        patched_compute,
        captured_stores,
    ):
        """INLINE and THREAD dispatch run the same pipeline and store equal results."""
        sess = await _seed_session(
            patched_session_scope, async_test_device, date(2025, 1, 1)
        )
        await patched_session_scope.commit()

        with _patch_waveform(), _patch_events():
            inline_summary = await run_one(
                sess.id,
                profile_id=async_test_device.profile_id,
                dispatch=Dispatch.INLINE,
            )
            thread_summary = await run_one(
                sess.id,
                profile_id=async_test_device.profile_id,
                dispatch=Dispatch.THREAD,
            )

        assert inline_summary == thread_summary
        assert inline_summary.session_id == sess.id

        # Both dispatches stored, with identical payloads.
        assert len(captured_stores) == 2
        (pid_a, comp_a), (pid_b, comp_b) = captured_stores
        assert pid_a == pid_b == async_test_device.profile_id
        assert comp_a.summary == comp_b.summary
        assert comp_a.primary_mode == comp_b.primary_mode

    async def test_process_dispatch_stores_result_via_pool(
        self,
        patched_session_scope,
        async_test_device,
        patched_compute,
        captured_stores,
    ):
        """PROCESS dispatch runs compute through the shared pool and stores it.

        get_pool is patched to a real ThreadPoolExecutor so the process branch is
        exercised without spawning a subprocess; the pooled worker still runs the
        (patched) prepare/compute pipeline in-process, so the stored envelope is
        assertable exactly as for INLINE/THREAD.
        """
        from concurrent.futures import ThreadPoolExecutor

        sess = await _seed_session(
            patched_session_scope, async_test_device, date(2025, 1, 6)
        )
        await patched_session_scope.commit()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with (
                _patch_waveform(),
                _patch_events(),
                patch("snore.utils.process_pool.get_pool", return_value=pool),
            ):
                summary = await run_one(
                    sess.id,
                    profile_id=async_test_device.profile_id,
                    dispatch=Dispatch.PROCESS,
                )

        assert summary.session_id == sess.id

        # The pooled compute result was stored for the owning profile.
        assert len(captured_stores) == 1
        pid, computation = captured_stores[0]
        assert pid == async_test_device.profile_id
        assert computation.summary == summary

    async def test_store_false_skips_write_phase(
        self,
        patched_session_scope,
        async_test_device,
        patched_compute,
        captured_stores,
    ):
        """store=False returns the summary without persisting anything."""
        sess = await _seed_session(
            patched_session_scope, async_test_device, date(2025, 1, 2)
        )
        await patched_session_scope.commit()

        with _patch_waveform(), _patch_events():
            summary = await run_one(
                sess.id,
                profile_id=async_test_device.profile_id,
                store=False,
                dispatch=Dispatch.INLINE,
            )

        assert summary.session_id == sess.id
        assert captured_stores == []

    async def test_foreign_profile_session_raises_not_found(
        self, patched_session_scope, async_test_device
    ):
        """A session owned by a different profile raises NotFoundError."""
        import uuid

        from snore.database.models import Profile, User

        other_user = User(
            canonical_email=f"other_{uuid.uuid4().hex[:8]}@example.com",
            role="member",
        )
        patched_session_scope.add(other_user)
        await patched_session_scope.flush()

        other_profile = Profile(user_id=other_user.id, name="Other Profile")
        patched_session_scope.add(other_profile)
        await patched_session_scope.flush()

        other_device = Device(
            profile_id=other_profile.id,
            manufacturer="OtherCo",
            model="Model X",
            serial_number=f"OTHER_{uuid.uuid4().hex[:8]}",
        )
        patched_session_scope.add(other_device)
        await patched_session_scope.flush()

        other_sess = await _seed_session(
            patched_session_scope, other_device, date(2025, 1, 3)
        )
        await patched_session_scope.commit()

        with pytest.raises(NotFoundError, match=str(other_sess.id)):
            await run_one(
                other_sess.id,
                profile_id=async_test_device.profile_id,
                dispatch=Dispatch.INLINE,
            )

    async def test_missing_session_raises_not_found(
        self, patched_session_scope, async_test_device
    ):
        """An unknown session_id raises NotFoundError before any load/compute."""
        with pytest.raises(NotFoundError, match="99999"):
            await run_one(
                99999,
                profile_id=async_test_device.profile_id,
                dispatch=Dispatch.INLINE,
            )

    async def test_primary_mode_propagates_to_raw_inputs(
        self,
        patched_session_scope,
        async_test_device,
        patched_compute,
        captured_stores,
    ):
        """primary_mode reaches the RawSessionBlobs DTO and the stored computation."""
        sess = await _seed_session(
            patched_session_scope, async_test_device, date(2025, 1, 4)
        )
        await patched_session_scope.commit()

        with _patch_waveform(), _patch_events():
            await run_one(
                sess.id,
                profile_id=async_test_device.profile_id,
                modes=["aasm", "resmed"],
                primary_mode="resmed",
                dispatch=Dispatch.INLINE,
            )

        raw = patched_compute["raw"]
        assert raw.primary_mode == "resmed"
        assert raw.modes == ["aasm", "resmed"]

        assert len(captured_stores) == 1
        _, computation = captured_stores[0]
        assert computation.primary_mode == "resmed"

    async def test_invalid_primary_mode_raises_value_error(
        self, patched_session_scope, async_test_device
    ):
        """primary_mode not in modes raises ValueError from the load phase."""
        sess = await _seed_session(
            patched_session_scope, async_test_device, date(2025, 1, 5)
        )
        await patched_session_scope.commit()

        with (
            _patch_waveform(),
            _patch_events(),
            pytest.raises(ValueError, match="primary_mode"),
        ):
            await run_one(
                sess.id,
                profile_id=async_test_device.profile_id,
                modes=["aasm"],
                primary_mode="resmed",
                dispatch=Dispatch.INLINE,
            )
