"""Integration tests for the get_breath_table MCP tool adapter.

Covers:
- Raw fetch: correct offsets/fields, total_breaths pre-pagination, page 2 remainder.
- Binned fetch: aggregates correctly (breath_count per bin, median of seeded values).
- Multi-session ambiguity: ValidationError listing session IDs; explicit session_id succeeds.
- Analysis status: not_run and stale_version refusals.
- Isolation: profile A cannot access profile B's sessions or devices; A's query
  returns only A's breaths.

Seed helpers are self-contained — do not import from sibling test files.
"""

from __future__ import annotations

import uuid

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import (
    AnalysisResult,
    Breath,
    Day,
    Device,
    Profile,
    Session,
    User,
)

# ---------------------------------------------------------------------------
# Seed helpers (self-contained — do not import across test files)
# ---------------------------------------------------------------------------


async def _make_profile(db: AsyncSession) -> Any:
    user = User(
        canonical_email=f"bt_{uuid.uuid4().hex[:8]}@example.com",
        role="member",
    )
    db.add(user)
    await db.flush()
    profile = Profile(user_id=user.id, name="BreathTable Profile")
    db.add(profile)
    await db.flush()
    return profile


async def _make_device(
    db: AsyncSession,
    profile_id: int,
    manufacturer: str = "BTMfr",
    model: str = "BTModel",
    serial_number: str | None = None,
) -> Device:
    device = Device(
        profile_id=profile_id,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number or f"SN_{uuid.uuid4().hex[:8]}",
    )
    db.add(device)
    await db.flush()
    return device


async def _make_day_session(
    db: AsyncSession,
    device: Device,
    day_date: date,
    duration_hours: float = 8.0,
    start_hour: int = 22,
    **day_kwargs: Any,
) -> tuple[Day, Session]:
    """Create a linked Day + enabled Session pair."""
    day = Day(
        device_id=device.id,
        date=day_date,
        total_therapy_hours=duration_hours,
        **day_kwargs,
    )
    db.add(day)
    await db.flush()

    start_dt = datetime(day_date.year, day_date.month, day_date.day, start_hour, 0, 0)
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"bt_{day_date.isoformat()}_{uuid.uuid4().hex[:6]}",
        start_time=start_dt,
        end_time=start_dt + timedelta(hours=duration_hours),
        duration_seconds=duration_hours * 3600,
        enabled=True,
    )
    db.add(sess)
    await db.flush()
    return day, sess


async def _make_analysis_result(db: AsyncSession, session: Session) -> AnalysisResult:
    """Create an AnalysisResult matching the current algorithm identity (status=OK)."""
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


async def _make_stale_analysis_result(
    db: AsyncSession, session: Session
) -> AnalysisResult:
    """Create an AnalysisResult with an empty engine_versions_json (→ STALE_VERSION)."""
    ar = AnalysisResult(
        session_id=session.id,
        timestamp_start=session.start_time,
        timestamp_end=session.end_time,
        engine_versions_json={},  # missing "identity" key → STALE_VERSION
    )
    db.add(ar)
    await db.flush()
    return ar


async def _make_breath(
    db: AsyncSession,
    ar: AnalysisResult,
    session: Session,
    breath_number: int,
    start_offset_s: float | None = None,
    inspiration_time_s: float = 1.2,
    i_e_ratio: float = 0.4,
    leak_valid: bool = True,
    tidal_volume_ml: float | None = None,
    flatness_index: float | None = None,
    mid_insp_flattening: float | None = None,
) -> Breath:
    """Create a Breath row with configurable measurement fields.

    Does NOT flush — callers must flush after their seeding loop so the DB sees
    all rows in a single round-trip.
    """
    offset = (
        start_offset_s if start_offset_s is not None else float(breath_number) * 5.0
    )
    breath = Breath(
        analysis_result_id=ar.id,
        session_id=session.id,
        breath_number=breath_number,
        start_offset_s=offset,
        end_offset_s=offset + 4.0,
        inspiration_time_s=inspiration_time_s,
        i_e_ratio=i_e_ratio,
        leak_valid=leak_valid,
        tidal_volume_ml=tidal_volume_ml,
        flatness_index=flatness_index,
        mid_insp_flattening=mid_insp_flattening,
    )
    db.add(breath)
    return breath


# ---------------------------------------------------------------------------
# TestRawFetch — raw paginated fetch
# ---------------------------------------------------------------------------


class TestRawFetch:
    async def test_raw_fetch_returns_seeded_breaths_with_correct_fields(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Raw fetch returns rows matching the seeded breaths.

        total_breaths is the full count before pagination; page 2 returns remainder.
        """
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 1)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        ar = await _make_analysis_result(async_db_session, sess)

        # Seed 5 breaths within the 0–900 s window
        for i in range(1, 6):
            await _make_breath(
                async_db_session,
                ar,
                sess,
                breath_number=i,
                start_offset_s=float(i) * 100.0,
                tidal_volume_ml=400.0 + float(i),
            )
        await async_db_session.flush()

        # Page 1 of 3
        page1 = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=900.0,
            page=1,
            page_size=3,
        )

        assert page1.is_binned is False
        assert page1.total_breaths == 5
        assert len(page1.rows) == 3
        assert page1.analysis_status == "ok"

        # Each row carries the renamed fields
        assert page1.rows[0].ti_s == pytest.approx(1.2, abs=0.01)
        assert page1.rows[0].start_offset_seconds == pytest.approx(100.0)

        # Page 2: remainder
        page2 = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=900.0,
            page=2,
            page_size=3,
        )

        assert page2.total_breaths == 5
        assert len(page2.rows) == 2

    async def test_raw_fetch_session_anchor_populated(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Top-level session_id and session_start_wall_clock come from the first row."""
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 2)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        ar = await _make_analysis_result(async_db_session, sess)
        await _make_breath(
            async_db_session, ar, sess, breath_number=1, start_offset_s=50.0
        )
        await async_db_session.flush()

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=900.0,
        )

        assert result.session_id == sess.id
        assert result.session_start_wall_clock is not None
        assert result.timezone_status == "unknown"


# ---------------------------------------------------------------------------
# TestBinnedFetch — aggregated bin fetch
# ---------------------------------------------------------------------------


class TestBinnedFetch:
    async def test_binned_fetch_aggregates_breath_count_and_medians(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """bin_minutes aggregates breaths into time bins.

        Seeds 6 breaths spanning two 900 s bins; asserts breath_count and
        tidal_volume median per bin.
        """
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 5)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(
            async_db_session, device, target_date, duration_hours=1.0
        )
        ar = await _make_analysis_result(async_db_session, sess)

        # Bin 0 (0–900 s): 3 breaths with tidal volumes 400, 500, 600 → median 500
        bin0_tvs = [400.0, 500.0, 600.0]
        for i, tv in enumerate(bin0_tvs):
            await _make_breath(
                async_db_session,
                ar,
                sess,
                breath_number=i + 1,
                start_offset_s=float(i + 1) * 100.0,
                tidal_volume_ml=tv,
            )

        # Bin 1 (900–1800 s): 3 breaths with tidal volumes 300, 400, 500 → median 400
        bin1_tvs = [300.0, 400.0, 500.0]
        for i, tv in enumerate(bin1_tvs):
            await _make_breath(
                async_db_session,
                ar,
                sess,
                breath_number=i + 4,
                start_offset_s=900.0 + float(i + 1) * 100.0,
                tidal_volume_ml=tv,
            )
        await async_db_session.flush()

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=1800.0,
            bin_minutes=15.0,  # 900 s bins
        )

        assert result.is_binned is True
        assert result.total_breaths == 6
        assert result.rows == []
        assert len(result.bins) == 2

        b0 = result.bins[0]
        assert b0.breath_count == 3
        assert b0.tidal_volume_median_ml == pytest.approx(500.0)

        b1 = result.bins[1]
        assert b1.breath_count == 3
        assert b1.tidal_volume_median_ml == pytest.approx(400.0)

    async def test_binned_auto_resolved_session_returns_session_id(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Binned fetch with auto-resolve (no session_id arg) sets response session_id.

        Regression guard: before fix 4, binned responses returned session_id=null
        when the caller relied on auto-resolve because bins carry no session id
        and the DTO only echoed the input query.
        """
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 3, 15)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(
            async_db_session, device, target_date, duration_hours=1.0
        )
        ar = await _make_analysis_result(async_db_session, sess)

        for i in range(1, 4):
            await _make_breath(
                async_db_session,
                ar,
                sess,
                breath_number=i,
                start_offset_s=float(i) * 200.0,
            )
        await async_db_session.flush()

        # No session_id arg — must trigger auto-resolve
        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=1800.0,
            bin_minutes=15.0,
        )

        assert result.is_binned is True
        assert result.session_id == sess.id


# ---------------------------------------------------------------------------
# TestMultiSessionAmbiguity — two sessions on same day
# ---------------------------------------------------------------------------


class TestMultiSessionAmbiguity:
    async def test_two_sessions_no_session_id_raises_validation_error(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Day with two sessions and no session_id → ValidationError listing both IDs."""
        from snore.mcp.errors import ValidationError  # noqa: PLC0415
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 10)
        device = await _make_device(async_db_session, async_test_profile.id)

        day = Day(
            device_id=device.id,
            date=target_date,
            total_therapy_hours=10.0,
        )
        async_db_session.add(day)
        await async_db_session.flush()

        start1 = datetime(
            target_date.year, target_date.month, target_date.day, 22, 0, 0
        )
        sess1 = Session(
            device_id=device.id,
            day_id=day.id,
            device_session_id=f"bt_s1_{uuid.uuid4().hex[:6]}",
            start_time=start1,
            end_time=start1 + timedelta(hours=4),
            duration_seconds=4 * 3600,
            enabled=True,
        )
        async_db_session.add(sess1)

        start2 = start1 + timedelta(hours=5)
        sess2 = Session(
            device_id=device.id,
            day_id=day.id,
            device_session_id=f"bt_s2_{uuid.uuid4().hex[:6]}",
            start_time=start2,
            end_time=start2 + timedelta(hours=4),
            duration_seconds=4 * 3600,
            enabled=True,
        )
        async_db_session.add(sess2)
        await async_db_session.flush()

        with pytest.raises(ValidationError) as exc_info:
            await get_breath_table(
                async_db_session,
                target_date,
                profile_id=async_test_profile.id,
                offset_start=0.0,
                offset_end=300.0,
            )

        message = str(exc_info.value)
        assert str(sess1.id) in message
        assert str(sess2.id) in message

    async def test_explicit_session_id_succeeds(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """With explicit session_id on a multi-session day, fetch succeeds."""
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 11)
        device = await _make_device(async_db_session, async_test_profile.id)

        day = Day(
            device_id=device.id,
            date=target_date,
            total_therapy_hours=10.0,
        )
        async_db_session.add(day)
        await async_db_session.flush()

        start1 = datetime(
            target_date.year, target_date.month, target_date.day, 22, 0, 0
        )
        sess1 = Session(
            device_id=device.id,
            day_id=day.id,
            device_session_id=f"bt_es1_{uuid.uuid4().hex[:6]}",
            start_time=start1,
            end_time=start1 + timedelta(hours=4),
            duration_seconds=4 * 3600,
            enabled=True,
        )
        async_db_session.add(sess1)

        start2 = start1 + timedelta(hours=5)
        sess2 = Session(
            device_id=device.id,
            day_id=day.id,
            device_session_id=f"bt_es2_{uuid.uuid4().hex[:6]}",
            start_time=start2,
            end_time=start2 + timedelta(hours=4),
            duration_seconds=4 * 3600,
            enabled=True,
        )
        async_db_session.add(sess2)
        await async_db_session.flush()

        ar1 = await _make_analysis_result(async_db_session, sess1)
        await _make_breath(
            async_db_session, ar1, sess1, breath_number=1, start_offset_s=50.0
        )
        await async_db_session.flush()

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            session_id=sess1.id,
            offset_start=0.0,
            offset_end=300.0,
        )

        assert result.total_breaths == 1
        assert result.session_id == sess1.id


# ---------------------------------------------------------------------------
# TestAnalysisStatus — not_run and stale_version refusals
# ---------------------------------------------------------------------------


class TestAnalysisStatus:
    async def test_no_analysis_returns_not_run(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Day with no AnalysisResult → analysis_status='not_run', total_breaths=0."""
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 15)
        device = await _make_device(async_db_session, async_test_profile.id)
        await _make_day_session(async_db_session, device, target_date)
        # No AnalysisResult seeded

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=300.0,
        )

        assert result.analysis_status == "not_run"
        assert result.null_reason == "analysis_not_run"
        assert result.total_breaths == 0

    async def test_stale_analysis_returns_stale_version(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Day with stale engine_versions_json → analysis_status='stale_version'."""
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 2, 16)
        device = await _make_device(async_db_session, async_test_profile.id)
        _, sess = await _make_day_session(async_db_session, device, target_date)
        await _make_stale_analysis_result(async_db_session, sess)

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=300.0,
        )

        assert result.analysis_status == "stale_version"
        assert result.null_reason == "analysis_stale"
        assert result.total_breaths == 0


# ---------------------------------------------------------------------------
# TestIsolation — adversarial two-profile isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    async def test_profile_a_passing_b_session_id_raises_validation_error(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Profile A passing profile B's session_id → ValidationError (not B's data)."""
        from snore.mcp.errors import ValidationError  # noqa: PLC0415
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        profile_b = await _make_profile(async_db_session)
        device_b = await _make_device(async_db_session, profile_b.id)
        target_date = date(2024, 3, 1)
        _, sess_b = await _make_day_session(async_db_session, device_b, target_date)
        ar_b = await _make_analysis_result(async_db_session, sess_b)
        await _make_breath(async_db_session, ar_b, sess_b, breath_number=1)
        await async_db_session.flush()

        with pytest.raises(ValidationError):
            await get_breath_table(
                async_db_session,
                target_date,
                profile_id=async_test_profile.id,  # profile A
                session_id=sess_b.id,  # B's session
                offset_start=0.0,
                offset_end=300.0,
            )

    async def test_profile_a_passing_b_device_id_raises_not_available(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """Profile A passing profile B's device_id → ValidationError 'not available'."""
        from snore.mcp.errors import ValidationError  # noqa: PLC0415
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        profile_b = await _make_profile(async_db_session)
        device_b = await _make_device(async_db_session, profile_b.id)
        target_date = date(2024, 3, 2)

        with pytest.raises(ValidationError) as exc_info:
            await get_breath_table(
                async_db_session,
                target_date,
                profile_id=async_test_profile.id,  # profile A
                device_id=device_b.id,  # B's device
                offset_start=0.0,
                offset_end=300.0,
            )

        message = str(exc_info.value)
        assert "is not available in this session" in message
        assert "profile" not in message.lower()

    async def test_profile_a_query_excludes_profile_b_breaths(
        self, async_db_session: AsyncSession, async_test_profile: Any
    ) -> None:
        """A's own query returns only A's breaths; B's unique tidal_volume absent."""
        from snore.mcp.tools.breath_table import get_breath_table  # noqa: PLC0415

        target_date = date(2024, 3, 5)

        # Profile A: 2 breaths, tidal_volume_ml=400
        device_a = await _make_device(async_db_session, async_test_profile.id)
        _, sess_a = await _make_day_session(async_db_session, device_a, target_date)
        ar_a = await _make_analysis_result(async_db_session, sess_a)
        for i in range(1, 3):
            await _make_breath(
                async_db_session,
                ar_a,
                sess_a,
                breath_number=i,
                start_offset_s=float(i) * 50.0,
                tidal_volume_ml=400.0,
            )
        await async_db_session.flush()

        # Profile B: 2 breaths, tidal_volume_ml=999.5 (unique sentinel)
        profile_b = await _make_profile(async_db_session)
        device_b = await _make_device(async_db_session, profile_b.id)
        _, sess_b = await _make_day_session(async_db_session, device_b, target_date)
        ar_b = await _make_analysis_result(async_db_session, sess_b)
        for i in range(1, 3):
            await _make_breath(
                async_db_session,
                ar_b,
                sess_b,
                breath_number=i,
                start_offset_s=float(i) * 50.0,
                tidal_volume_ml=999.5,  # sentinel unique to B
            )
        await async_db_session.flush()

        result = await get_breath_table(
            async_db_session,
            target_date,
            profile_id=async_test_profile.id,
            offset_start=0.0,
            offset_end=900.0,
        )

        assert result.total_breaths == 2
        # B's sentinel tidal_volume must not appear
        b_sentinel = 999.5
        for row in result.rows:
            assert row.tidal_volume_ml != pytest.approx(b_sentinel)
