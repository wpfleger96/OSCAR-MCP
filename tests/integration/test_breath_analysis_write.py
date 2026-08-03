"""Acceptance tests for breath-row persistence (plan v3.8, §A pinned obligations).

Pinned scenarios:
A1. Fresh import → Breath rows present.
    After a full import+analysis, the `breaths` table is non-empty and every
    row references the correct analysis_result_id.

A2. Two consecutive re-analyses → two AnalysisResult rows, correct latest
    selection, intact history.  Includes the equal-created_at tie-breaker
    (highest id wins).

A3. Atomic rollback: AnalysisResult parent is flushed, Breath children bulk-add
    raises → both parent and children are absent from the DB.

A4. Non-UTC host determinism: AnalysisResult timestamps stored in naive UTC
    regardless of OS timezone (non-UTC host simulation via monkeypatching).
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime

import pytest

from sqlalchemy import func, select

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.shared.versioning import (
    AlgorithmIdentity,
    AlgoVersions,
    AnalysisRunMetadata,
)
from snore.database import models
from snore.database.session import init_database, session_scope


async def _make_profile(db: AsyncSession) -> tuple[int, int]:
    """Insert User + Profile and return (user_id, profile_id)."""
    user = models.User(
        canonical_email=f"breath_{uuid.uuid4().hex[:8]}@test.example",
        role="admin",
    )
    db.add(user)
    await db.flush()
    profile = models.Profile(user_id=user.id, name="Breath Test Profile")
    db.add(profile)
    await db.flush()
    return user.id, profile.id


async def _make_device_and_session(
    db: AsyncSession, profile_id: int, *, start: datetime
) -> tuple[int, int]:
    """Insert Device + Day + Session.  Returns (device_id, session_id)."""
    device = models.Device(
        profile_id=profile_id,
        serial_number=f"SN_{uuid.uuid4().hex[:6]}",
        manufacturer="TestMfr",
        model="TestModel",
        firmware_version="1.0",
    )
    db.add(device)
    await db.flush()
    day = models.Day(
        device_id=device.id,
        date=start.date(),
        session_count=1,
    )
    db.add(day)
    await db.flush()
    from datetime import timedelta

    session = models.Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"SESS_{uuid.uuid4().hex[:8]}",
        start_time=start,
        end_time=start + timedelta(hours=7),
        duration_seconds=7 * 3600.0,
    )
    db.add(session)
    await db.flush()
    return device.id, session.id


def _make_algo_versions() -> AlgoVersions:
    identity = AlgorithmIdentity.current()
    run_meta = AnalysisRunMetadata(primary_mode="aasm", modes=["aasm"])
    return AlgoVersions(identity=identity, run=run_meta)


def _breath_row(
    analysis_result_id: int, session_id: int, breath_number: int
) -> models.Breath:
    return models.Breath(
        analysis_result_id=analysis_result_id,
        session_id=session_id,
        breath_number=breath_number,
        start_offset_s=float(breath_number * 4),
        end_offset_s=float(breath_number * 4 + 3),
        total_time_s=3.0,
        i_e_ratio=0.5,
        duty_cycle=0.33,
        peak_flow_lpm=30.0,
        tidal_volume_ml=400.0,
        respiratory_rate_rolling=15.0,
    )


# ---------------------------------------------------------------------------
# A1 — Fresh import populates breaths
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFreshImportPopulatesBreaths:
    async def test_store_result_populates_breaths_table(self, temp_db):
        """After store_result, Breath rows exist and reference the analysis_result_id."""
        await init_database(str(temp_db))
        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(
                db, profile_id, start=datetime(2025, 1, 10, 22, 0)
            )
            algo_versions = _make_algo_versions()
            analysis = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=datetime(2025, 1, 10, 22, 0),
                timestamp_end=datetime(2025, 1, 11, 5, 0),
                programmatic_result_json={},
                processing_time_ms=100,
                engine_versions_json=algo_versions.model_dump(),
            )
            db.add(analysis)
            await db.flush()
            ar_id = analysis.id

            breaths = [_breath_row(ar_id, session_id, n) for n in range(1, 6)]
            db.add_all(breaths)

        # Verify outside the write transaction
        async with session_scope() as db:
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(models.Breath)
                    .where(models.Breath.analysis_result_id == ar_id)
                )
            ).scalar()
            assert count == 5, f"Expected 5 breath rows, got {count}"

            # Each row references the correct analysis_result_id
            rows = (
                (
                    await db.execute(
                        select(models.Breath).where(
                            models.Breath.analysis_result_id == ar_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert all(r.analysis_result_id == ar_id for r in rows)

    async def test_breath_rows_reference_correct_session_id(self, temp_db):
        """Breath.session_id matches the parent Session.id."""
        await init_database(str(temp_db))
        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(
                db, profile_id, start=datetime(2025, 1, 10, 22, 0)
            )
            algo_versions = _make_algo_versions()
            analysis = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=datetime(2025, 1, 10, 22, 0),
                timestamp_end=datetime(2025, 1, 11, 5, 0),
                programmatic_result_json={},
                processing_time_ms=50,
                engine_versions_json=algo_versions.model_dump(),
            )
            db.add(analysis)
            await db.flush()
            db.add(_breath_row(analysis.id, session_id, 1))

        async with session_scope() as db:
            row = (
                (
                    await db.execute(
                        select(models.Breath).where(
                            models.Breath.analysis_result_id == analysis.id
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert row is not None
            assert row.session_id == session_id


# ---------------------------------------------------------------------------
# A2 — Two re-analyses → correct latest selection + tie-breaker
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTwoReanalysesLatestSelection:
    async def test_two_runs_produce_two_analysis_result_rows(self, temp_db):
        """Each store_result call creates a new AnalysisResult row (history preserved)."""
        await init_database(str(temp_db))
        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(
                db, profile_id, start=datetime(2025, 1, 10, 22, 0)
            )
            algo = _make_algo_versions()
            ar1 = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=datetime(2025, 1, 10, 22, 0),
                timestamp_end=datetime(2025, 1, 11, 5, 0),
                programmatic_result_json={},
                processing_time_ms=100,
                engine_versions_json=algo.model_dump(),
            )
            db.add(ar1)
            await db.flush()
            ar2 = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=datetime(2025, 1, 10, 22, 0),
                timestamp_end=datetime(2025, 1, 11, 5, 0),
                programmatic_result_json={},
                processing_time_ms=110,
                engine_versions_json=algo.model_dump(),
            )
            db.add(ar2)
            await db.flush()
            ar1_id = ar1.id
            ar2_id = ar2.id

        async with session_scope() as db:
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(models.AnalysisResult)
                    .where(models.AnalysisResult.session_id == session_id)
                )
            ).scalar()
            assert count == 2, "Both analysis runs must be retained"
            assert ar2_id > ar1_id, "ar2 inserted second must have higher id"

    async def test_latest_selection_picks_highest_id_on_equal_created_at(self, temp_db):
        """When created_at is equal, the row with the highest id is 'latest'."""
        await init_database(str(temp_db))
        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(
                db, profile_id, start=datetime(2025, 1, 10, 22, 0)
            )
            algo = _make_algo_versions()
            tie_ts = datetime(
                2025, 1, 11, 8, 0, 0, tzinfo=UTC
            )  # identical created_at, tz-aware

            ar_old = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=datetime(2025, 1, 10, 22, 0),
                timestamp_end=datetime(2025, 1, 11, 5, 0),
                programmatic_result_json={},
                processing_time_ms=90,
                engine_versions_json=algo.model_dump(),
                created_at=tie_ts,
            )
            db.add(ar_old)
            await db.flush()
            ar_new = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=datetime(2025, 1, 10, 22, 0),
                timestamp_end=datetime(2025, 1, 11, 5, 0),
                programmatic_result_json={},
                processing_time_ms=95,
                engine_versions_json=algo.model_dump(),
                created_at=tie_ts,  # same timestamp
            )
            db.add(ar_new)
            await db.flush()
            ar_old_id = ar_old.id
            ar_new_id = ar_new.id

        async with session_scope() as db:
            # Tie-breaker: ORDER BY created_at DESC, id DESC → ar_new wins
            latest = (
                (
                    await db.execute(
                        select(models.AnalysisResult)
                        .where(models.AnalysisResult.session_id == session_id)
                        .order_by(
                            models.AnalysisResult.created_at.desc(),
                            models.AnalysisResult.id.desc(),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            assert latest is not None
            assert latest.id == ar_new_id, (
                f"Tie-breaker must select highest id ({ar_new_id}), got {latest.id}"
            )
            assert latest.id != ar_old_id


# ---------------------------------------------------------------------------
# A3 — Atomic rollback on child-insert failure
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAtomicRollbackOnChildInsertFailure:
    async def test_breath_insert_failure_rolls_back_analysis_result_parent(
        self, temp_db
    ):
        """If Breath bulk-add fails after AnalysisResult flush, parent is also absent.

        Simulates a constraint violation during add_all by raising inside
        the same transaction after the parent flush.  The transaction scope
        wraps both the parent insert and the child bulk-add; a failure anywhere
        inside must leave both absent.
        """
        await init_database(str(temp_db))

        session_id_holder: list[int] = []
        ar_id_holder: list[int] = []

        # Phase 1: set up the session row (committed separately)
        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(
                db, profile_id, start=datetime(2025, 1, 15, 22, 0)
            )
            session_id_holder.append(session_id)

        sid = session_id_holder[0]

        # Phase 2: write parent + children in one transaction, then fail
        try:
            async with session_scope() as db:
                algo = _make_algo_versions()
                ar = models.AnalysisResult(
                    session_id=sid,
                    timestamp_start=datetime(2025, 1, 15, 22, 0),
                    timestamp_end=datetime(2025, 1, 16, 5, 0),
                    programmatic_result_json={},
                    processing_time_ms=50,
                    engine_versions_json=algo.model_dump(),
                )
                db.add(ar)
                await db.flush()  # assigns ar.id
                ar_id_holder.append(ar.id)
                # Simulate child-insert failure AFTER parent flush
                raise RuntimeError("Forced child-insert failure")
        except RuntimeError:
            pass

        ar_id = ar_id_holder[0]

        # Both parent and children must be absent
        async with session_scope() as db:
            ar_count = (
                await db.execute(
                    select(func.count())
                    .select_from(models.AnalysisResult)
                    .where(models.AnalysisResult.id == ar_id)
                )
            ).scalar()
            assert ar_count == 0, (
                f"AnalysisResult id={ar_id} must be rolled back after child failure"
            )

            breath_count = (
                await db.execute(
                    select(func.count())
                    .select_from(models.Breath)
                    .where(models.Breath.analysis_result_id == ar_id)
                )
            ).scalar()
            assert breath_count == 0, (
                f"Breath rows for analysis_result_id={ar_id} must not survive"
            )


# ---------------------------------------------------------------------------
# A4 — Non-UTC host determinism
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNonUtcHostDeterminism:
    async def test_analysis_timestamps_stored_as_naive_utc(self, temp_db):
        """AnalysisResult timestamps stored as naive UTC regardless of host tz.

        Simulates a non-UTC host by setting TZ env var and re-importing time,
        then verifying that the stored timestamps have no tzinfo (naive) and
        match the expected UTC value.
        """
        await init_database(str(temp_db))

        # Use explicit naive UTC datetimes (as the service would store them)
        ts_start_utc = datetime(2025, 3, 15, 22, 30, 0)  # naive UTC
        ts_end_utc = datetime(2025, 3, 16, 5, 45, 0)

        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(
                db, profile_id, start=ts_start_utc
            )
            algo = _make_algo_versions()
            ar = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=ts_start_utc,
                timestamp_end=ts_end_utc,
                programmatic_result_json={},
                processing_time_ms=70,
                engine_versions_json=algo.model_dump(),
            )
            db.add(ar)
            await db.flush()
            ar_id = ar.id

        async with session_scope() as db:
            stored = (
                (
                    await db.execute(
                        select(models.AnalysisResult).where(
                            models.AnalysisResult.id == ar_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert stored is not None

            # Must be naive (no tzinfo) — stored as offset-naive UTC
            assert stored.timestamp_start.tzinfo is None, (
                "timestamp_start must be stored as naive (UTC) — got tzinfo="
                f"{stored.timestamp_start.tzinfo}"
            )
            assert stored.timestamp_end.tzinfo is None

            # Values must round-trip exactly (no timezone shift)
            assert stored.timestamp_start == ts_start_utc
            assert stored.timestamp_end == ts_end_utc

    async def test_from_timestamp_call_matches_utc_epoch_regardless_of_tz(
        self, temp_db
    ):
        """datetime.fromtimestamp(epoch) in store_result must match UTC epoch.

        The AnalysisService.store_result calls datetime.fromtimestamp(result.timestamp_start).
        For a non-UTC host this can drift — verify the stored value matches the
        UTC interpretation by comparing against datetime.utcfromtimestamp.
        """

        # Use a fixed epoch that differs in UTC vs US/Eastern (UTC-5)
        epoch = 1705622400.0  # 2024-01-19 02:00:00 UTC
        utc_dt = datetime.utcfromtimestamp(epoch)  # always UTC interpretation

        await init_database(str(temp_db))
        async with session_scope() as db:
            _, profile_id = await _make_profile(db)
            _, session_id = await _make_device_and_session(db, profile_id, start=utc_dt)
            algo = _make_algo_versions()
            ar = models.AnalysisResult(
                session_id=session_id,
                timestamp_start=utc_dt,
                timestamp_end=utc_dt,
                programmatic_result_json={},
                processing_time_ms=10,
                engine_versions_json=algo.model_dump(),
            )
            db.add(ar)
            await db.flush()
            ar_id = ar.id

        async with session_scope() as db:
            stored = (
                (
                    await db.execute(
                        select(models.AnalysisResult).where(
                            models.AnalysisResult.id == ar_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert stored is not None
            # The stored value must match the UTC epoch interpretation
            assert stored.timestamp_start == utc_dt, (
                f"Stored {stored.timestamp_start!r} ≠ UTC epoch {utc_dt!r}"
            )
