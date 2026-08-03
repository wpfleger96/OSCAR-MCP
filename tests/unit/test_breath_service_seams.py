"""Unit tests for BreathService seams (plan v3.8 — all seam unit-tested against
fixture data, independent of MCP).

Coverage:
- get_analysis_status: NOT_RUN, OK, STALE_VERSION
- get_breath_table: empty → NOT_RUN, paginated BreathRow, time-binned BreathBin
- find_windows: empty → NOT_RUN result, WORST_FLATTENING_LEAK_VALID criterion
- compare_epochs: empty range → NO_DATA_IN_RANGE + null algorithm_identity;
                  populated → nights_with_data > 0
- get_nightly_summary: ValueError no sessions, OK day with analyzed_session_count
- get_nightly_range_summary: empty range returns n_nights=0
- get_device_capabilities: no sessions → null_reason; with sessions → date range
- get_contextual_events: no events → empty; with events → ContextualEvent list
- get_ca_analysis: no CA events → empty; with CA events → CaAnalysisResult
- get_waveform_window: no waveform data → empty channels
- fetch_waveform_window_raw (module-level): missing session raises ValueError
- compute_waveform_window (module-level): empty raw data round-trip
- A-suite rework: A1/A2/A3 drive store_result() production path
- vendor_applicability: non-ResMed device → unvalidated_device in breath rows
- deletion cascade: Breath rows deleted when AnalysisResult parent is deleted
- missing breaths table: actionable RuntimeError on no such table
"""

from __future__ import annotations

import uuid

from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.modes.types import ModeResult
from snore.analysis.service import AnalysisService
from snore.analysis.shared.versioning import (
    AlgorithmIdentity,
    AlgoVersions,
    AnalysisRunMetadata,
    AnalysisStatus,
    NullReason,
)
from snore.analysis.types import AnalysisComputation
from snore.analysis.types import AnalysisResult as AnalysisResultDTO
from snore.database import models
from snore.services.breath_service import (
    BreathQueryRange,
    BreathService,
    EpochRequest,
    WaveformWindowRequest,
    WindowCriterion,
    compute_waveform_window,
    fetch_waveform_window_raw,
)

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


async def _make_profile(db: AsyncSession) -> tuple[int, int]:
    """Return (user_id, profile_id)."""
    user = models.User(
        canonical_email=f"svc_{uuid.uuid4().hex[:8]}@test.example",
        role="admin",
    )
    db.add(user)
    await db.flush()
    profile = models.Profile(user_id=user.id, name="SeamTest")
    db.add(profile)
    await db.flush()
    return user.id, profile.id


async def _make_device(
    db: AsyncSession,
    profile_id: int,
    manufacturer: str = "ResMed",
) -> models.Device:
    device = models.Device(
        profile_id=profile_id,
        serial_number=f"SN_{uuid.uuid4().hex[:6]}",
        manufacturer=manufacturer,
        model="AirCurve 11 VAuto",
        firmware_version="1.0",
    )
    db.add(device)
    await db.flush()
    return device


async def _make_day_and_session(
    db: AsyncSession,
    device_id: int,
    therapy_date: date,
    *,
    duration_hours: float = 7.0,
) -> tuple[models.Day, models.Session]:
    start_dt = datetime(therapy_date.year, therapy_date.month, therapy_date.day, 22, 0)
    day = models.Day(device_id=device_id, date=therapy_date, session_count=1)
    db.add(day)
    await db.flush()
    session = models.Session(
        device_id=device_id,
        day_id=day.id,
        device_session_id=f"SESS_{uuid.uuid4().hex[:8]}",
        start_time=start_dt,
        end_time=start_dt + timedelta(hours=duration_hours),
        duration_seconds=duration_hours * 3600.0,
    )
    db.add(session)
    await db.flush()
    return day, session


def _make_algo_versions(primary_mode: str = "aasm") -> AlgoVersions:
    identity = AlgorithmIdentity.current()
    run_meta = AnalysisRunMetadata(primary_mode=primary_mode, modes=[primary_mode])
    return AlgoVersions(identity=identity, run=run_meta)


async def _store_analysis_with_breaths(
    db: AsyncSession,
    session: models.Session,
    profile_id: int,
    n_breaths: int = 5,
    flow_class: int | None = 1,
    is_recovery: bool = False,
) -> models.AnalysisResult:
    """Write an AnalysisResult + Breath rows via AnalysisService.store_result."""
    result_dto = AnalysisResultDTO(
        session_id=session.id,
        session_duration_hours=session.duration_seconds / 3600.0
        if session.duration_seconds
        else 7.0,
        total_breaths=n_breaths,
        machine_events=[],
        mode_results={
            "aasm": ModeResult(
                mode_name="aasm", apneas=[], hypopneas=[], ahi=0.0, rdi=0.0
            )
        },
        timestamp_start=session.start_time.timestamp(),
        timestamp_end=session.end_time.timestamp()
        if session.end_time
        else (session.start_time + timedelta(hours=7)).timestamp(),
    )

    from snore.analysis.types import ComputedBreath  # noqa: PLC0415

    breaths = [
        ComputedBreath(
            breath_number=i + 1,
            start_offset_s=float(i * 4),
            end_offset_s=float(i * 4 + 3),
            inspiration_time_s=1.2,
            expiration_time_s=1.8,
            total_time_s=3.0,
            i_e_ratio=0.67,
            duty_cycle=0.4,
            peak_flow_lpm=30.0,
            tidal_volume_ml=400.0,
            respiratory_rate_rolling=15.0,
            flatness_index=0.2,
            mid_insp_flattening=0.35,
            flow_class=flow_class,
            flow_confidence=0.9,
            is_recovery_breath=is_recovery if i == n_breaths - 1 else False,
            inferred_trigger_type="normal",
            trigger_confidence=0.8,
            inferred_cycle_type="normal",
            cycle_confidence=0.75,
            trigger_cycle_applicable=True,
            trigger_cycle_reason=None,
            leak_valid=True,
            leak_valid_reason=None,
            ramp_active=None,
            ramp_active_reason="not_available",
            mask_off=False,
            mask_off_reason=None,
        )
        for i in range(n_breaths)
    ]

    computation = AnalysisComputation(
        summary=result_dto, breaths=breaths, primary_mode="aasm"
    )
    svc = AnalysisService(db, profile_id=profile_id)
    await svc.store_result(computation, processing_time_ms=42)
    await db.flush()
    # Re-query to get the committed row
    ar = (
        (
            await db.execute(
                select(models.AnalysisResult)
                .where(models.AnalysisResult.session_id == session.id)
                .order_by(models.AnalysisResult.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    assert ar is not None
    return ar


# ---------------------------------------------------------------------------
# get_analysis_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAnalysisStatus:
    async def test_not_run_when_no_analysis_result(self, async_db_session):
        """get_analysis_status returns NOT_RUN when no AnalysisResult exists."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 1, 10)
        )

        svc = BreathService(async_db_session)
        status, algo = await svc.get_analysis_status(session.id)

        assert status == AnalysisStatus.NOT_RUN
        assert algo is None

    async def test_ok_when_current_version_result_exists(self, async_db_session):
        """get_analysis_status returns OK when latest result matches current identity."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 1, 10)
        )
        await _store_analysis_with_breaths(async_db_session, session, profile_id)

        svc = BreathService(async_db_session)
        status, algo = await svc.get_analysis_status(session.id)

        assert status == AnalysisStatus.OK
        assert algo is not None

    async def test_stale_version_when_engine_versions_missing(self, async_db_session):
        """get_analysis_status returns STALE_VERSION for a legacy flat engine_versions_json."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 1, 10)
        )

        # Insert a bare (legacy) AnalysisResult with no "identity" key
        ar = models.AnalysisResult(
            session_id=session.id,
            timestamp_start=session.start_time,
            timestamp_end=session.end_time or session.start_time + timedelta(hours=7),
            programmatic_result_json={},
            processing_time_ms=10,
            engine_versions_json={"version": "0.1.0"},  # legacy flat shape
        )
        async_db_session.add(ar)
        await async_db_session.flush()

        svc = BreathService(async_db_session)
        status, algo = await svc.get_analysis_status(session.id)

        assert status == AnalysisStatus.STALE_VERSION


# ---------------------------------------------------------------------------
# get_breath_table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBreathTable:
    async def test_not_run_returns_not_run_status(self, async_db_session):
        """get_breath_table returns AnalysisStatus.NOT_RUN when no analysis exists."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 1, 10)
        await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        query = BreathQueryRange(
            therapy_date=therapy_date,
            device_id=dev.id,
            offset_start=0.0,
            offset_end=900.0,
        )
        page = await svc.get_breath_table(query)

        assert page.analysis_status == AnalysisStatus.NOT_RUN
        assert page.rows == []
        assert page.bins == []

    async def test_paginated_breath_rows_returned_when_analysis_exists(
        self, async_db_session
    ):
        """get_breath_table returns BreathRow list for a short window."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 1, 10)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=10
        )

        svc = BreathService(async_db_session)
        # Window spanning first ~15s of session — under 15-min raw cap
        query = BreathQueryRange(
            therapy_date=therapy_date,
            device_id=dev.id,
            offset_start=0.0,
            offset_end=60.0,
        )
        page = await svc.get_breath_table(query)

        assert page.analysis_status == AnalysisStatus.OK
        # Some breath rows should be in the window
        assert len(page.rows) > 0 or len(page.bins) > 0

    async def test_no_data_in_range_when_window_outside_session(self, async_db_session):
        """get_breath_table returns empty rows for an out-of-range window."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 1, 10)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, therapy_date, duration_hours=1.0
        )
        await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=3
        )

        svc = BreathService(async_db_session)
        # Window far beyond session end — use bin_minutes to bypass 15-min raw cap
        query = BreathQueryRange(
            therapy_date=therapy_date,
            device_id=dev.id,
            offset_start=9000.0,
            offset_end=9900.0,
            bin_minutes=15.0,
        )
        page = await svc.get_breath_table(query)

        # Either empty rows, empty bins, or null_reason set
        assert page.rows == [] or page.null_reason == NullReason.NO_DATA_IN_RANGE


# ---------------------------------------------------------------------------
# find_windows
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindWindows:
    async def test_not_run_result_when_no_analysis(self, async_db_session):
        """find_windows returns NOT_RUN day_status when no analysis exists."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 1, 10)
        await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        result = await svc.find_windows(
            therapy_date=therapy_date,
            criterion=WindowCriterion.WORST_FLATTENING_LEAK_VALID,
            n=3,
            device_id=dev.id,
        )

        assert result.windows == []
        from snore.services.breath_service import DayAnalysisStatus  # noqa: PLC0415

        assert result.day_status != DayAnalysisStatus.OK

    async def test_worst_flattening_criterion_returns_result(self, async_db_session):
        """find_windows returns a FindWindowsResult for WORST_FLATTENING_LEAK_VALID."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 1, 10)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=20, flow_class=3
        )

        svc = BreathService(async_db_session)
        result = await svc.find_windows(
            therapy_date=therapy_date,
            criterion=WindowCriterion.WORST_FLATTENING_LEAK_VALID,
            n=3,
            device_id=dev.id,
        )

        from snore.services.breath_service import DayAnalysisStatus  # noqa: PLC0415

        assert result.day_status == DayAnalysisStatus.OK
        # May be empty if flattening threshold not met, but no exception
        assert isinstance(result.windows, list)


# ---------------------------------------------------------------------------
# compare_epochs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompareEpochs:
    async def test_empty_range_yields_no_data_in_range_and_null_identity(
        self, async_db_session
    ):
        """compare_epochs with no sessions → null algorithm_identity + NO_DATA_IN_RANGE."""
        svc = BreathService(async_db_session)
        result = await svc.compare_epochs(
            epochs=[
                EpochRequest(
                    label="empty",
                    date_start=date(2025, 1, 1),
                    date_end=date(2025, 1, 7),
                )
            ]
        )

        assert len(result.epochs) == 1
        es = result.epochs[0]
        assert es.algorithm_identity is None
        assert es.null_reason == NullReason.NO_DATA_IN_RANGE
        assert es.nights_with_data == 0

    async def test_analyzed_session_count_positive_when_analysis_exists(
        self, async_db_session
    ):
        """compare_epochs sets nights_with_data > 0 when a session has an OK analysis."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 2, 1)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        await _store_analysis_with_breaths(async_db_session, session, profile_id)

        svc = BreathService(async_db_session)
        result = await svc.compare_epochs(
            epochs=[
                EpochRequest(
                    label="feb",
                    date_start=therapy_date,
                    date_end=therapy_date,
                    device_id=dev.id,
                )
            ]
        )

        assert len(result.epochs) == 1
        es = result.epochs[0]
        assert es.nights_with_data > 0
        assert es.algorithm_identity is not None
        assert es.null_reason is None


# ---------------------------------------------------------------------------
# get_nightly_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNightlySummary:
    async def test_not_run_when_no_sessions_for_date(self, async_db_session):
        """get_nightly_summary raises ValueError when no sessions exist for date."""
        svc = BreathService(async_db_session)
        with pytest.raises(ValueError, match="No sessions found"):
            await svc.get_nightly_summary(date(2025, 3, 1))

    async def test_ok_day_returns_analyzed_session_count(self, async_db_session):
        """get_nightly_summary returns analyzed_session_count >= 1 for an analyzed day."""
        from snore.services.breath_service import DayAnalysisStatus  # noqa: PLC0415

        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 3, 5)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=10
        )

        svc = BreathService(async_db_session)
        summary = await svc.get_nightly_summary(therapy_date, device_id=dev.id)

        assert summary.therapy_date == therapy_date
        assert summary.device_id == dev.id
        # Should be OK or PARTIAL depending on coverage
        assert summary.day_status in (
            DayAnalysisStatus.OK,
            DayAnalysisStatus.PARTIAL,
        )
        assert summary.analyzed_session_count >= 1

    async def test_not_run_day_returns_not_run_status(self, async_db_session):
        """get_nightly_summary returns NOT_RUN when session has no analysis."""
        from snore.services.breath_service import DayAnalysisStatus  # noqa: PLC0415

        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 3, 6)
        await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        summary = await svc.get_nightly_summary(therapy_date, device_id=dev.id)

        assert summary.day_status == DayAnalysisStatus.NOT_RUN
        assert summary.analyzed_session_count == 0
        assert summary.fl_reason == NullReason.NOT_AVAILABLE


# ---------------------------------------------------------------------------
# get_nightly_range_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNightlyRangeSummary:
    async def test_empty_range_returns_zero_nights(self, async_db_session):
        """get_nightly_range_summary over a range with no sessions returns n_nights=0."""
        svc = BreathService(async_db_session)
        summary = await svc.get_nightly_range_summary(
            date_start=date(2025, 4, 1),
            date_end=date(2025, 4, 3),
        )

        assert summary.n_nights == 0
        assert summary.nights == []

    async def test_range_with_one_session_returns_one_night(self, async_db_session):
        """get_nightly_range_summary over a range with one session returns n_nights=1."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 4, 2)
        await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        summary = await svc.get_nightly_range_summary(
            date_start=date(2025, 4, 1),
            date_end=date(2025, 4, 3),
            device_id=dev.id,
        )

        assert summary.n_nights == 1


# ---------------------------------------------------------------------------
# get_device_capabilities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDeviceCapabilities:
    async def test_no_sessions_returns_null_reason(self, async_db_session):
        """get_device_capabilities returns a null_reason when no sessions exist."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)

        svc = BreathService(async_db_session)
        caps = await svc.get_device_capabilities(device_id=dev.id)

        assert caps.device_id == dev.id
        assert caps.null_reason is not None  # NO_DATA_IN_RANGE or similar

    async def test_with_sessions_returns_date_range(self, async_db_session):
        """get_device_capabilities returns actual_date_start/end when sessions exist."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 5, 1)
        await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        caps = await svc.get_device_capabilities(device_id=dev.id)

        assert caps.device_id == dev.id
        assert caps.actual_date_start == therapy_date
        assert caps.actual_date_end == therapy_date


# ---------------------------------------------------------------------------
# get_contextual_events
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetContextualEvents:
    async def test_empty_when_no_events(self, async_db_session):
        """get_contextual_events returns [] when no machine events exist."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 5, 10)
        await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        events = await svc.get_contextual_events(
            therapy_date=therapy_date, device_id=dev.id
        )

        assert events == []

    async def test_returns_event_with_context_fields(self, async_db_session):
        """get_contextual_events returns ContextualEvent list for sessions with events."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 5, 10)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        # Insert a machine event
        event = models.Event(
            session_id=session.id,
            event_type="OA",
            start_time=session.start_time + timedelta(minutes=30),
            duration_seconds=10.0,
        )
        async_db_session.add(event)
        await async_db_session.flush()

        svc = BreathService(async_db_session)
        events = await svc.get_contextual_events(
            therapy_date=therapy_date, device_id=dev.id
        )

        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "OA"
        assert ev.duration_seconds == 10.0


# ---------------------------------------------------------------------------
# get_ca_analysis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCaAnalysis:
    async def test_empty_when_no_ca_events(self, async_db_session):
        """get_ca_analysis returns empty ca_events when no CA events exist."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 6, 1)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        await _store_analysis_with_breaths(async_db_session, session, profile_id)

        svc = BreathService(async_db_session)
        result = await svc.get_ca_analysis(therapy_date=therapy_date, device_id=dev.id)

        assert result.ca_events == []

    async def test_returns_ca_events_when_ca_present(self, async_db_session):
        """get_ca_analysis populates ca_events list when CA events are present."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 6, 1)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)
        await _store_analysis_with_breaths(async_db_session, session, profile_id)

        ca_event = models.Event(
            session_id=session.id,
            event_type="CA",
            start_time=session.start_time + timedelta(minutes=10),
            duration_seconds=20.0,
        )
        async_db_session.add(ca_event)
        await async_db_session.flush()

        svc = BreathService(async_db_session)
        result = await svc.get_ca_analysis(therapy_date=therapy_date, device_id=dev.id)

        assert len(result.ca_events) == 1
        assert result.ca_events[0].duration_seconds == pytest.approx(20.0)
        assert result.ca_events[0].session_id == session.id
        # periodic_breathing_pct is a float or None
        assert result.periodic_breathing_pct is None or isinstance(
            result.periodic_breathing_pct, float
        )


# ---------------------------------------------------------------------------
# get_waveform_window
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWaveformWindow:
    async def test_no_waveform_data_returns_empty_channels(self, async_db_session):
        """get_waveform_window returns empty channels when no waveform blobs exist."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        therapy_date = date(2025, 6, 10)
        _, session = await _make_day_and_session(async_db_session, dev.id, therapy_date)

        svc = BreathService(async_db_session)
        req = WaveformWindowRequest(
            therapy_date=therapy_date,
            session_id=session.id,
            offset_start=0.0,
            offset_end=120.0,
        )
        window = await svc.get_waveform_window(req)

        # No waveform blobs stored → empty channels
        assert window.channels == []


# ---------------------------------------------------------------------------
# fetch_waveform_window_raw (module-level)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchWaveformWindowRaw:
    async def test_missing_session_raises_value_error(self, async_db_session):
        """fetch_waveform_window_raw raises ValueError for an unknown session_id."""
        req = WaveformWindowRequest(
            therapy_date=date(2025, 1, 1),
            session_id=99999,
            offset_start=0.0,
            offset_end=120.0,
        )
        with pytest.raises(ValueError, match="Session"):
            await fetch_waveform_window_raw(async_db_session, req)


# ---------------------------------------------------------------------------
# compute_waveform_window (module-level)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComputeWaveformWindow:
    def test_empty_raw_returns_empty_channels(self):
        """compute_waveform_window on empty raw channels returns window with no channels."""
        from snore.services.breath_service import RawWaveformWindow  # noqa: PLC0415

        req = WaveformWindowRequest(
            therapy_date=date(2025, 1, 1),
            session_id=1,
            offset_start=0.0,
            offset_end=60.0,
        )
        raw = RawWaveformWindow(
            request=req,
            session_id=1,
            session_start_wall_clock=datetime(2025, 1, 1, 22, 0, 0),
            channels=[],
            missing_channels=[],
        )
        window = compute_waveform_window(raw)
        assert window.channels == []
        assert window.missing_channel_reason is None


# ---------------------------------------------------------------------------
# A-suite rework — drives store_result() production path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAsuiteViaStoreResult:
    """A1-A3 reworked to drive the production store_result() path."""

    async def test_a1_store_result_writes_breath_rows_observable(
        self, async_db_session
    ):
        """A1: store_result() populates Breath rows; asserted via separate query."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 7, 1)
        )
        ar = await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=5
        )

        count = (
            await async_db_session.execute(
                select(func.count())
                .select_from(models.Breath)
                .where(models.Breath.analysis_result_id == ar.id)
            )
        ).scalar()

        assert count == 5, f"Expected 5 Breath rows, got {count}"

    async def test_a1_breath_rows_reference_correct_session_id(self, async_db_session):
        """A1: each Breath.session_id matches the parent Session.id."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 7, 2)
        )
        ar = await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=3
        )

        rows = (
            (
                await async_db_session.execute(
                    select(models.Breath).where(
                        models.Breath.analysis_result_id == ar.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(r.session_id == session.id for r in rows), (
            "All Breath rows must reference the correct session_id"
        )

    async def test_a2_two_store_result_calls_produce_two_rows(self, async_db_session):
        """A2: two store_result() calls → two AnalysisResult rows preserved."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 7, 3)
        )

        ar1 = await _store_analysis_with_breaths(async_db_session, session, profile_id)
        ar2 = await _store_analysis_with_breaths(async_db_session, session, profile_id)

        count = (
            await async_db_session.execute(
                select(func.count())
                .select_from(models.AnalysisResult)
                .where(models.AnalysisResult.session_id == session.id)
            )
        ).scalar()

        assert count == 2, f"Two store_result calls must produce 2 rows; got {count}"
        assert ar2.id > ar1.id, "Second result must have higher id"

    async def test_a2_production_selector_returns_latest_by_id_on_equal_created_at(
        self, async_db_session
    ):
        """A2 tie-breaker: BreathService._latest_analysis_for_session returns higher id."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 7, 4)
        )

        tie_ts = datetime(2025, 7, 4, 8, 0, 0, tzinfo=UTC)
        algo = _make_algo_versions()

        ar_old = models.AnalysisResult(
            session_id=session.id,
            timestamp_start=session.start_time,
            timestamp_end=session.end_time or session.start_time + timedelta(hours=7),
            programmatic_result_json={},
            processing_time_ms=50,
            engine_versions_json=algo.model_dump(),
            created_at=tie_ts,
        )
        async_db_session.add(ar_old)
        await async_db_session.flush()

        ar_new = models.AnalysisResult(
            session_id=session.id,
            timestamp_start=session.start_time,
            timestamp_end=session.end_time or session.start_time + timedelta(hours=7),
            programmatic_result_json={},
            processing_time_ms=55,
            engine_versions_json=algo.model_dump(),
            created_at=tie_ts,  # same timestamp
        )
        async_db_session.add(ar_new)
        await async_db_session.flush()
        ar_new_id = ar_new.id

        # Call the production selector
        svc = BreathService(async_db_session)
        _status, _algo, ar_id = await svc._latest_analysis_for_session(session.id)

        assert ar_id == ar_new_id, (
            f"Tie-breaker must select highest id ({ar_new_id}), got {ar_id}"
        )

    async def test_a3_rollback_when_store_result_raises_after_flush(
        self, async_db_session
    ):
        """A3: AnalysisResult is absent after store_result raises mid-transaction."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 7, 5)
        )

        from snore.analysis.types import ComputedBreath  # noqa: PLC0415

        result_dto = AnalysisResultDTO(
            session_id=session.id,
            session_duration_hours=7.0,
            total_breaths=1,
            machine_events=[],
            mode_results={
                "aasm": ModeResult(
                    mode_name="aasm", apneas=[], hypopneas=[], ahi=0.0, rdi=0.0
                )
            },
            timestamp_start=session.start_time.timestamp(),
            timestamp_end=(
                session.end_time or session.start_time + timedelta(hours=7)
            ).timestamp(),
        )
        good_breath = ComputedBreath(
            breath_number=1,
            start_offset_s=0.0,
            end_offset_s=3.0,
            inspiration_time_s=1.2,
            expiration_time_s=1.8,
            total_time_s=3.0,
            i_e_ratio=0.67,
            duty_cycle=0.4,
            peak_flow_lpm=30.0,
            tidal_volume_ml=400.0,
            respiratory_rate_rolling=15.0,
            flatness_index=0.2,
            mid_insp_flattening=0.35,
            flow_class=1,
            flow_confidence=0.9,
            is_recovery_breath=False,
            inferred_trigger_type="normal",
            trigger_confidence=0.8,
            inferred_cycle_type="normal",
            cycle_confidence=0.75,
            trigger_cycle_applicable=True,
            trigger_cycle_reason=None,
            leak_valid=True,
            leak_valid_reason=None,
            ramp_active=None,
            ramp_active_reason="not_available",
            mask_off=False,
            mask_off_reason=None,
        )
        computation = AnalysisComputation(
            summary=result_dto, breaths=[good_breath], primary_mode="aasm"
        )

        # Monkeypatch add_all to raise after flush
        original_add_all = async_db_session.add_all

        def _failing_add_all(rows: Any) -> None:
            raise RuntimeError("Induced child-insert failure")

        async_db_session.add_all = _failing_add_all

        try:
            svc = AnalysisService(async_db_session, profile_id=profile_id)
            with pytest.raises((RuntimeError, Exception)):
                await svc.store_result(computation, processing_time_ms=1)
        finally:
            async_db_session.add_all = original_add_all
            await async_db_session.rollback()

        # After rollback, no AnalysisResult should exist
        count = (
            await async_db_session.execute(
                select(func.count())
                .select_from(models.AnalysisResult)
                .where(models.AnalysisResult.session_id == session.id)
            )
        ).scalar()
        assert count == 0, "AnalysisResult must be rolled back when child-insert fails"


# ---------------------------------------------------------------------------
# Vendor applicability — non-ResMed device gets unvalidated_device
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVendorApplicability:
    async def test_non_resmed_device_gets_unvalidated_trigger_cycle(
        self, async_db_session
    ):
        """Breaths computed for a non-ResMed device have unvalidated_device applicability."""
        from snore.analysis.service import _build_computed_breaths  # noqa: PLC0415
        from snore.analysis.shared.breath_segmenter import (
            BreathMetrics,  # noqa: PLC0415
        )
        from snore.analysis.shared.trigger_cycle import (  # noqa: PLC0415
            APPLICABILITY_UNVALIDATED_DEVICE,
        )

        t = np.linspace(0.0, 30.0, 750)  # 25Hz, 30s
        flow = np.abs(np.sin(np.linspace(0.0, 10 * np.pi, 750))) * 30.0

        bm = BreathMetrics(
            breath_number=1,
            start_time=0.0,
            middle_time=1.2,
            end_time=3.0,
            inspiration_time=1.2,
            expiration_time=1.8,
            duration=3.0,
            respiratory_rate=20.0,
            respiratory_rate_rolling=15.0,
            tidal_volume=400.0,
            tidal_volume_smoothed=400.0,
            peak_inspiratory_flow=30.0,
            peak_expiratory_flow=20.0,
            i_e_ratio=0.67,
            minute_ventilation=6.0,
            amplitude=50.0,
            is_complete=True,
        )

        results = _build_computed_breaths(
            breaths=[bm],
            timestamps=t,
            flow_values=flow,
            flow_pattern_by_number={},
            recovery_breath_indices=set(),
            leak_timestamps=None,
            leak_values=None,
            device_manufacturer="Philips",
        )

        assert len(results) == 1
        cb = results[0]
        # Non-ResMed: trigger/cycle heuristic is not applicable
        assert (
            cb.trigger_cycle_applicable is False
            or cb.trigger_cycle_reason == APPLICABILITY_UNVALIDATED_DEVICE
        )

    async def test_resmed_device_gets_validated_trigger_cycle(self, async_db_session):
        """Breaths computed for a ResMed device do NOT carry unvalidated_device reason."""
        from snore.analysis.service import _build_computed_breaths  # noqa: PLC0415
        from snore.analysis.shared.breath_segmenter import (
            BreathMetrics,  # noqa: PLC0415
        )

        t = np.linspace(0.0, 30.0, 750)
        flow = np.abs(np.sin(np.linspace(0.0, 10 * np.pi, 750))) * 30.0

        bm = BreathMetrics(
            breath_number=1,
            start_time=0.0,
            middle_time=1.2,
            end_time=3.0,
            inspiration_time=1.2,
            expiration_time=1.8,
            duration=3.0,
            respiratory_rate=20.0,
            respiratory_rate_rolling=15.0,
            tidal_volume=400.0,
            tidal_volume_smoothed=400.0,
            peak_inspiratory_flow=30.0,
            peak_expiratory_flow=20.0,
            i_e_ratio=0.67,
            minute_ventilation=6.0,
            amplitude=50.0,
            is_complete=True,
        )

        results = _build_computed_breaths(
            breaths=[bm],
            timestamps=t,
            flow_values=flow,
            flow_pattern_by_number={},
            recovery_breath_indices=set(),
            leak_timestamps=None,
            leak_values=None,
            device_manufacturer="ResMed",
        )

        assert len(results) == 1
        cb = results[0]
        # ResMed: trigger/cycle heuristic is applicable (may be None on short breath)
        assert cb.trigger_cycle_reason != "unvalidated_device"


# ---------------------------------------------------------------------------
# Deletion cascade — Breath rows cascade-delete with AnalysisResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeletionCascade:
    async def test_breath_rows_deleted_when_analysis_result_deleted(
        self, async_db_session
    ):
        """Breath rows are cascade-deleted when their AnalysisResult parent is deleted."""
        _, profile_id = await _make_profile(async_db_session)
        dev = await _make_device(async_db_session, profile_id)
        _, session = await _make_day_and_session(
            async_db_session, dev.id, date(2025, 9, 1)
        )
        ar = await _store_analysis_with_breaths(
            async_db_session, session, profile_id, n_breaths=5
        )
        ar_id = ar.id

        # Verify breaths exist
        before_count = (
            await async_db_session.execute(
                select(func.count())
                .select_from(models.Breath)
                .where(models.Breath.analysis_result_id == ar_id)
            )
        ).scalar()
        assert before_count == 5

        # Delete the AnalysisResult
        await async_db_session.delete(ar)
        await async_db_session.flush()

        # Breaths must be gone
        after_count = (
            await async_db_session.execute(
                select(func.count())
                .select_from(models.Breath)
                .where(models.Breath.analysis_result_id == ar_id)
            )
        ).scalar()
        assert after_count == 0, (
            f"Breath rows must cascade-delete with AnalysisResult; {after_count} remain"
        )


# ---------------------------------------------------------------------------
# Missing breaths table — actionable RuntimeError (real-path, no monkeypatching)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMissingBreathsTable:
    async def test_missing_breaths_table_raises_actionable_error(
        self, async_db_session, tmp_path
    ):
        """store_result raises a RuntimeError with actionable message when breaths table absent.

        Uses a separate fresh SQLite database (created via Base.metadata.create_all)
        then executes ``DROP TABLE breaths`` before calling store_result().  When
        store_result flushes the breath children, SQLAlchemy raises
        OperationalError("no such table: breaths") from the real SQLite engine —
        no monkeypatching.  The handler must convert this to a RuntimeError with
        the drop-and-reimport guidance message.
        """
        import sqlite3  # noqa: PLC0415

        from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from snore.analysis.types import ComputedBreath  # noqa: PLC0415
        from snore.database.models import Base  # noqa: PLC0415

        db_path = tmp_path / "missing_breaths.db"
        async_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(async_url, echo=False)

        # Create full schema (including breaths table).
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, class_=AsyncSession
        )

        # Insert the prerequisite rows (profile, device, session) using the full schema.
        async with factory() as setup_db:
            _, profile_id = await _make_profile(setup_db)
            dev = await _make_device(setup_db, profile_id)
            _, session = await _make_day_and_session(
                setup_db, dev.id, date(2025, 10, 1)
            )
            await setup_db.commit()
            session_id = session.id

        await engine.dispose()

        # Drop the breaths table directly via sqlite3 (synchronous, no ORM).
        con = sqlite3.connect(str(db_path))
        con.execute("DROP TABLE breaths")
        con.commit()
        con.close()

        # Re-open the engine — now the breaths table is gone.
        engine2 = create_async_engine(async_url, echo=False)
        factory2 = async_sessionmaker(
            bind=engine2, expire_on_commit=False, class_=AsyncSession
        )

        result_dto = AnalysisResultDTO(
            session_id=session_id,
            session_duration_hours=7.0,
            total_breaths=1,
            machine_events=[],
            mode_results={
                "aasm": ModeResult(
                    mode_name="aasm", apneas=[], hypopneas=[], ahi=0.0, rdi=0.0
                )
            },
            timestamp_start=1000.0,
            timestamp_end=26200.0,
        )
        breath = ComputedBreath(
            breath_number=1,
            start_offset_s=0.0,
            end_offset_s=3.0,
            inspiration_time_s=1.2,
            expiration_time_s=1.8,
            total_time_s=3.0,
            i_e_ratio=0.67,
            duty_cycle=0.4,
            peak_flow_lpm=30.0,
            tidal_volume_ml=400.0,
            respiratory_rate_rolling=15.0,
            flatness_index=0.2,
            mid_insp_flattening=0.35,
            flow_class=1,
            flow_confidence=0.9,
            is_recovery_breath=False,
            inferred_trigger_type=None,
            trigger_confidence=None,
            inferred_cycle_type=None,
            cycle_confidence=None,
            trigger_cycle_applicable=None,
            trigger_cycle_reason=None,
            leak_valid=None,
            leak_valid_reason="channel_absent",
            ramp_active=None,
            ramp_active_reason="not_available",
            mask_off=None,
            mask_off_reason=None,
        )
        computation = AnalysisComputation(
            summary=result_dto, breaths=[breath], primary_mode="aasm"
        )

        try:
            async with factory2() as db:
                svc = AnalysisService(db, profile_id=profile_id)
                with pytest.raises(
                    RuntimeError,
                    match="breaths.*table.*missing|drop.*database|re-import",
                ):
                    await svc.store_result(computation, processing_time_ms=1)
        finally:
            await engine2.dispose()
