"""Integration tests for the get_ca_analysis MCP tool adapter.

Exercises the full stack: get_ca_analysis adapter → BreathService → SQLite.
Each test is self-contained: seed helpers are defined in this file and must not
be imported from sibling test modules.

Scenarios:
  1. Seeded CA event on an analyzed day → ca_events length 1, offset matches seed;
     night-level fields null+not_available (no waveform data seeded).
  2. Device owned but no sessions on date → NOT_RUN response, ca_events=[].
  3. Session present, no AnalysisResult → CA events STILL returned (event-anchored);
     day_status=not_run, night-level null+not_available.
  4. Two-profile isolation: profile B's CA event absent from profile A's query.
"""

from __future__ import annotations

import uuid

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import (
    AnalysisResult,
    Day,
    Device,
    Event,
    Profile,
    Session,
    User,
)

# ---------------------------------------------------------------------------
# Seed helpers — self-contained, do not import from sibling test modules
# ---------------------------------------------------------------------------


async def _make_profile(db: AsyncSession) -> Any:
    user = User(
        canonical_email=f"ca_{uuid.uuid4().hex[:8]}@example.com",
        role="member",
    )
    db.add(user)
    await db.flush()
    profile = Profile(user_id=user.id, name="CaAnalysis Profile")
    db.add(profile)
    await db.flush()
    return profile


async def _make_device(
    db: AsyncSession,
    profile_id: int,
) -> Device:
    device = Device(
        profile_id=profile_id,
        manufacturer="CaMfr",
        model="CaModel",
        serial_number=f"CA_{uuid.uuid4().hex[:8]}",
    )
    db.add(device)
    await db.flush()
    return device


async def _make_day_session(
    db: AsyncSession,
    device: Device,
    day_date: date,
    duration_hours: float = 8.0,
) -> tuple[Day, Session]:
    day = Day(
        device_id=device.id,
        date=day_date,
        total_therapy_hours=duration_hours,
    )
    db.add(day)
    await db.flush()

    start_dt = datetime(day_date.year, day_date.month, day_date.day, 22, 0, 0)
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"ca_{day_date.isoformat()}_{uuid.uuid4().hex[:6]}",
        start_time=start_dt,
        end_time=start_dt + timedelta(hours=duration_hours),
        duration_seconds=duration_hours * 3600,
        enabled=True,
    )
    db.add(sess)
    await db.flush()
    return day, sess


async def _make_analysis_result(
    db: AsyncSession,
    session: Session,
) -> AnalysisResult:
    """Create an AnalysisResult with the current algorithm identity (status=OK)."""
    from snore.analysis.shared.versioning import (  # noqa: PLC0415
        AlgorithmIdentity,
        AlgoVersions,
        AnalysisRunMetadata,
    )

    algo_versions = AlgoVersions(
        identity=AlgorithmIdentity.current(),
        run=AnalysisRunMetadata(primary_mode="aasm", modes=["aasm"]),
    )
    ar = AnalysisResult(
        session_id=session.id,
        timestamp_start=session.start_time,
        timestamp_end=session.end_time,
        engine_versions_json=algo_versions.model_dump(),
    )
    db.add(ar)
    await db.flush()
    return ar


async def _make_ca_event(
    db: AsyncSession,
    session: Session,
    offset_seconds: float = 120.0,
    duration_seconds: float = 15.0,
) -> Event:
    """Create a CA Event at the given offset from session start."""
    start_time = session.start_time + timedelta(seconds=offset_seconds)
    event = Event(
        session_id=session.id,
        event_type="CA",
        start_time=start_time,
        duration_seconds=duration_seconds,
    )
    db.add(event)
    await db.flush()
    return event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetCaAnalysisSeededEvent:
    async def test_seeded_ca_event_on_analyzed_day(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Seeded CA event on a day with analysis: ca_events length 1, offset matches seed.

        Night-level fields (periodic_breathing_pct, mv_rolling_variance) are null
        with 'not_available' reason because no waveform data is seeded.
        """
        from snore.mcp.tools.ca_analysis import get_ca_analysis  # noqa: PLC0415

        day_date = date(2025, 1, 15)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(async_db_session, device, day_date)
        await _make_analysis_result(async_db_session, sess)
        await _make_ca_event(
            async_db_session, sess, offset_seconds=120.0, duration_seconds=15.0
        )
        await async_db_session.flush()

        result = await get_ca_analysis(
            async_db_session, day_date, async_test_profile.id
        )

        assert result.day_status == "ok"
        assert len(result.ca_events) == 1

        ev = result.ca_events[0]
        assert ev.offset_seconds == pytest.approx(120.0)
        assert ev.duration_seconds == pytest.approx(15.0)
        assert ev.session_start_wall_clock == "2025-01-15T22:00:00"
        assert ev.timezone_status == "unknown"

        # No waveform data → context fields null+not_available
        assert ev.preceding_mv_slope_lpm_per_min is None
        assert ev.preceding_mv_slope_reason == "not_available"
        assert ev.ps_delivered_cmh2o is None
        assert ev.ps_reason == "not_available"
        assert ev.stability_index is None
        assert ev.stability_reason == "not_available"

        # Night-level: no waveform → not_available reasons
        assert result.periodic_breathing_pct is None
        assert result.pb_reason == "not_available"
        assert result.mv_rolling_variance is None
        assert result.mv_variance_reason == "not_available"

        # Algorithm identity present and a dict
        assert isinstance(result.algorithm_identity, dict)
        assert "format_version" in result.algorithm_identity

        # Coverage entry present
        assert len(result.session_coverage) == 1
        assert result.session_coverage[0].analysis_status == "ok"


class TestGetCaAnalysisNoSessions:
    async def test_device_owned_no_sessions_on_date_returns_not_run(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Device owned but no sessions on the queried date → NOT_RUN response,
        ca_events=[], night-level null+not_available."""
        from snore.mcp.tools.ca_analysis import get_ca_analysis  # noqa: PLC0415

        device = await _make_device(async_db_session, async_test_profile.id)
        # No Day/Session seeded for the queried date
        await async_db_session.flush()

        result = await get_ca_analysis(
            async_db_session,
            date(2025, 1, 15),
            async_test_profile.id,
            device_id=device.id,
        )

        assert result.day_status == "not_run"
        assert result.null_reason == "analysis_not_run"
        assert result.ca_events == []
        assert result.session_coverage == []
        assert result.algorithm_identity is None
        assert result.periodic_breathing_pct is None
        assert result.pb_reason == "not_available"
        assert result.mv_rolling_variance is None
        assert result.mv_variance_reason == "not_available"


class TestGetCaAnalysisNoAnalysisResult:
    async def test_session_without_analysis_result_ca_events_still_returned(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Session and CA event seeded, but no AnalysisResult row.

        CA events are event-anchored (import-time) so they must appear even
        when analysis has not run.  Night-level fields are null+not_available.
        """
        from snore.mcp.tools.ca_analysis import get_ca_analysis  # noqa: PLC0415

        day_date = date(2025, 1, 20)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(async_db_session, device, day_date)
        # Seed a CA event — NO AnalysisResult
        await _make_ca_event(
            async_db_session, sess, offset_seconds=240.0, duration_seconds=8.0
        )
        await async_db_session.flush()

        result = await get_ca_analysis(
            async_db_session, day_date, async_test_profile.id
        )

        # Day status: no OK analysis → not_run
        assert result.day_status == "not_run"
        assert result.null_reason == "analysis_not_run"

        # Event-anchored: CA event still returned despite no analysis
        assert len(result.ca_events) == 1
        assert result.ca_events[0].offset_seconds == pytest.approx(240.0)
        assert result.ca_events[0].duration_seconds == pytest.approx(8.0)

        # Night-level null + not_available (no OK sessions)
        assert result.periodic_breathing_pct is None
        assert result.pb_reason == "not_available"
        assert result.mv_rolling_variance is None
        assert result.mv_variance_reason == "not_available"

        assert result.algorithm_identity is None


class TestGetCaAnalysisProfileIsolation:
    async def test_profile_b_ca_event_absent_from_profile_a_query(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Profile B's CA event (distinctive offset 300s) must not appear
        in profile A's query result."""
        from snore.mcp.tools.ca_analysis import get_ca_analysis  # noqa: PLC0415

        day_date = date(2025, 1, 25)

        # Profile A: device + session, no CA events
        device_a = await _make_device(async_db_session, async_test_profile.id)
        _, sess_a = await _make_day_session(async_db_session, device_a, day_date)
        await _make_analysis_result(async_db_session, sess_a)

        # Profile B: separate user/profile, device, session, CA event at 300s
        profile_b = await _make_profile(async_db_session)
        device_b = await _make_device(async_db_session, profile_b.id)
        _, sess_b = await _make_day_session(async_db_session, device_b, day_date)
        await _make_ca_event(async_db_session, sess_b, offset_seconds=300.0)

        await async_db_session.flush()

        # Query as profile A with profile A's device
        result = await get_ca_analysis(
            async_db_session, day_date, async_test_profile.id, device_id=device_a.id
        )

        # Profile B's 300s event must not appear
        assert all(ev.offset_seconds != pytest.approx(300.0) for ev in result.ca_events)
        # Profile A had no CA events seeded
        assert result.ca_events == []
