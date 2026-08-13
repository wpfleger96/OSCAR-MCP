"""Unit tests for HealthSampleImporter and the nightly-summary logic.

Uses the async_db_session fixture (no full DB init required — the engine is
created directly from a temp file and create_all builds the schema).
"""

from __future__ import annotations

import uuid

from datetime import date, datetime

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.health_importers import HealthSampleImporter
from snore.database.models import HealthNightlySummary, HealthSample, Profile, User
from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.type_handlers import SLEEP_TYPE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLEEP_NIGHT = date(2024, 1, 15)
WATCH_SOURCE = "Will's Apple Watch"
IPHONE_SOURCE = "Will's iPhone"
SPO2_TYPE = "HKQuantityTypeIdentifierOxygenSaturation"


def _dt(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi)


def _sleep(
    source: str,
    stage: str,
    start: datetime,
    end: datetime,
    night: date = SLEEP_NIGHT,
    channel: str = "export_xml",
) -> RawHealthRecord:
    return RawHealthRecord(
        record_type=SLEEP_TYPE,
        source_name=source,
        source_version="10.2",
        device_info=None,
        start_time=start,
        end_time=end,
        value_text=stage,
        value_num=None,
        unit=None,
        utc_offset_seconds=-18000,
        night_date=night,
        ingest_channel=channel,
    )


def _qty(
    source: str,
    value: float,
    start: datetime,
    night: date = SLEEP_NIGHT,
    record_type: str = SPO2_TYPE,
) -> RawHealthRecord:
    return RawHealthRecord(
        record_type=record_type,
        source_name=source,
        source_version="10.2",
        device_info=None,
        start_time=start,
        end_time=start,
        value_text=None,
        value_num=value,
        unit="%",
        utc_offset_seconds=-18000,
        night_date=night,
        ingest_channel="export_xml",
    )


async def _make_profile(db: AsyncSession) -> int:
    user = User(canonical_email=f"test_{uuid.uuid4().hex[:8]}@ex.test", role="member")
    db.add(user)
    await db.flush()
    profile = Profile(user_id=user.id, name="Test Profile")
    db.add(profile)
    await db.flush()
    return profile.id


async def _count_samples(db: AsyncSession, profile_id: int) -> int:
    result = await db.execute(
        select(HealthSample).where(HealthSample.profile_id == profile_id)
    )
    return len(result.scalars().all())


async def _get_summary(
    db: AsyncSession, profile_id: int, night: date
) -> HealthNightlySummary | None:
    result = await db.execute(
        select(HealthNightlySummary).where(
            HealthNightlySummary.profile_id == profile_id,
            HealthNightlySummary.night_date == night,
        )
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def profile_id(async_db_session):
    return await _make_profile(async_db_session)


@pytest.fixture
def importer():
    return HealthSampleImporter()


# ---------------------------------------------------------------------------
# Dedup: idempotency
# ---------------------------------------------------------------------------


class TestDedupIdempotency:
    async def test_second_insert_skips_all_category_records(
        self, async_db_session, profile_id, importer
    ):
        """Inserting the same sleep (category) records twice skips all on second pass."""
        records = [
            _sleep(
                WATCH_SOURCE,
                "AsleepCore",
                _dt(2024, 1, 15, 23, 30),
                _dt(2024, 1, 16, 0, 30),
            ),
            _sleep(
                WATCH_SOURCE,
                "AsleepDeep",
                _dt(2024, 1, 16, 1, 0),
                _dt(2024, 1, 16, 2, 0),
            ),
        ]

        ins1, skip1, nights1 = await importer.insert_samples_batch(
            records, profile_id, async_db_session
        )
        await async_db_session.flush()

        ins2, skip2, nights2 = await importer.insert_samples_batch(
            records, profile_id, async_db_session
        )
        await async_db_session.flush()

        assert ins1 == 2
        assert skip1 == 0
        assert ins2 == 0
        assert skip2 == 2
        assert await _count_samples(async_db_session, profile_id) == 2

    async def test_second_insert_skips_all_quantity_records(
        self, async_db_session, profile_id, importer
    ):
        """Inserting the same quantity records (value_num, value_text=None) twice skips all."""
        records = [
            _qty(WATCH_SOURCE, 0.962, _dt(2024, 1, 16, 2, 30)),
            _qty(WATCH_SOURCE, 0.958, _dt(2024, 1, 16, 3, 0)),
        ]

        ins1, skip1, _ = await importer.insert_samples_batch(
            records, profile_id, async_db_session
        )
        await async_db_session.flush()

        ins2, skip2, _ = await importer.insert_samples_batch(
            records, profile_id, async_db_session
        )
        await async_db_session.flush()

        assert ins1 == 2, "First insert should insert both quantity records"
        assert ins2 == 0, "Second insert must skip both (NULL value_text dedup path)"
        assert skip2 == 2

    async def test_mixed_batch_category_and_quantity_both_deduped(
        self, async_db_session, profile_id, importer
    ):
        """Batch with both category (value_num=None) and quantity (value_text=None)
        records is fully deduped on second insert — exercises both COALESCE paths."""
        category = _sleep(
            WATCH_SOURCE, "AsleepREM", _dt(2024, 1, 16, 4, 0), _dt(2024, 1, 16, 5, 0)
        )
        quantity = _qty(WATCH_SOURCE, 0.97, _dt(2024, 1, 16, 4, 30))
        records = [category, quantity]

        ins1, _, _ = await importer.insert_samples_batch(
            records, profile_id, async_db_session
        )
        await async_db_session.flush()
        ins2, skip2, _ = await importer.insert_samples_batch(
            records, profile_id, async_db_session
        )
        await async_db_session.flush()

        assert ins1 == 2
        assert ins2 == 0
        assert skip2 == 2

    async def test_rowcount_accuracy_with_partial_conflict(
        self, async_db_session, profile_id, importer
    ):
        """Verify that rowcount correctly counts only inserted (not ignored) rows.

        This test directly validates that multi-VALUES INSERT OR IGNORE under
        aiosqlite returns an accurate rowcount (sqlite3_changes()), not a stale
        or executemany-style -1.  If rowcount were unreliable, the assertion on
        ins2 would fail (it would be wrong rather than 0).
        """
        existing = _sleep(
            WATCH_SOURCE, "AsleepCore", _dt(2024, 1, 15, 23), _dt(2024, 1, 16, 0)
        )
        new_rec = _sleep(
            WATCH_SOURCE, "AsleepDeep", _dt(2024, 1, 16, 1), _dt(2024, 1, 16, 2)
        )

        # Insert the existing record first.
        ins1, _, _ = await importer.insert_samples_batch(
            [existing], profile_id, async_db_session
        )
        await async_db_session.flush()
        assert ins1 == 1

        # Insert a batch that includes the existing record AND a new one.
        ins2, skip2, _ = await importer.insert_samples_batch(
            [existing, new_rec], profile_id, async_db_session
        )
        await async_db_session.flush()

        # rowcount must be 1 (only new_rec inserted; existing ignored).
        assert ins2 == 1, (
            "rowcount from multi-VALUES INSERT OR IGNORE must equal rows actually inserted"
        )
        assert skip2 == 1


# ---------------------------------------------------------------------------
# Dedup: cross-channel
# ---------------------------------------------------------------------------


class TestCrossChannelDedup:
    async def test_same_key_different_channel_deduped(
        self, async_db_session, profile_id, importer
    ):
        """Records with the same natural key but different ingest_channel are deduped.

        ingest_channel is NOT part of the dedup index key, so the second insert
        (with a different channel) must be skipped."""
        base = _sleep(
            WATCH_SOURCE, "AsleepCore", _dt(2024, 1, 15, 23), _dt(2024, 1, 16, 0)
        )

        xml_rec = RawHealthRecord(**{**base.__dict__, "ingest_channel": "export_xml"})
        ios_rec = RawHealthRecord(**{**base.__dict__, "ingest_channel": "ios_app"})

        ins1, skip1, _ = await importer.insert_samples_batch(
            [xml_rec], profile_id, async_db_session
        )
        await async_db_session.flush()
        ins2, skip2, _ = await importer.insert_samples_batch(
            [ios_rec], profile_id, async_db_session
        )
        await async_db_session.flush()

        assert ins1 == 1
        assert ins2 == 0, "Different ingest_channel does not escape dedup"
        assert skip2 == 1
        assert await _count_samples(async_db_session, profile_id) == 1


# ---------------------------------------------------------------------------
# Watch preference
# ---------------------------------------------------------------------------


class TestSourcePreference:
    async def test_watch_with_stages_preferred_over_iphone_inbed(
        self, async_db_session, profile_id, importer
    ):
        """Night with Watch stage records and iPhone InBed-only → Watch is preferred."""
        records = [
            _sleep(
                WATCH_SOURCE,
                "AsleepCore",
                _dt(2024, 1, 15, 23, 30),
                _dt(2024, 1, 16, 0, 30),
            ),
            _sleep(
                WATCH_SOURCE,
                "AsleepDeep",
                _dt(2024, 1, 16, 1, 0),
                _dt(2024, 1, 16, 2, 0),
            ),
            _sleep(
                IPHONE_SOURCE, "InBed", _dt(2024, 1, 15, 23, 0), _dt(2024, 1, 16, 7, 0)
            ),
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None
        assert summary.preferred_source == WATCH_SOURCE

    async def test_iphone_only_falls_back_to_iphone(
        self, async_db_session, profile_id, importer
    ):
        """Night with only iPhone InBed records (no Watch) → iPhone is preferred."""
        records = [
            _sleep(
                IPHONE_SOURCE, "InBed", _dt(2024, 1, 15, 23, 0), _dt(2024, 1, 16, 7, 0)
            ),
            _sleep(
                IPHONE_SOURCE,
                "AsleepUnspecified",
                _dt(2024, 1, 16, 0, 0),
                _dt(2024, 1, 16, 6, 0),
            ),
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None
        assert summary.preferred_source == IPHONE_SOURCE

    async def test_watch_without_stages_not_preferred_when_another_has_stages(
        self, async_db_session, profile_id, importer
    ):
        """Watch that only has InBed records loses priority to another source with stages."""
        other_source = "Oura Ring"  # no "watch" in name but has stage records
        records = [
            _sleep(WATCH_SOURCE, "InBed", _dt(2024, 1, 15, 22), _dt(2024, 1, 16, 6)),
            _sleep(
                other_source, "AsleepCore", _dt(2024, 1, 15, 23), _dt(2024, 1, 16, 1)
            ),
            _sleep(
                other_source, "AsleepDeep", _dt(2024, 1, 16, 1), _dt(2024, 1, 16, 3)
            ),
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None
        # Watch has no stage records so it's not a watch-priority candidate;
        # Oura Ring is picked via the any-source fallback path (has most sleep secs).
        assert summary.preferred_source == other_source


# ---------------------------------------------------------------------------
# Math / summary computation
# ---------------------------------------------------------------------------


class TestSummaryMath:
    async def test_efficiency_and_stage_coverage(
        self, async_db_session, profile_id, importer
    ):
        """7h sleep across stages with 8h InBed → efficiency 87.5%; stage coverage correct."""
        # Watch: 1h Core + 2h Deep + 4h REM = 7h total sleep; no InBed from Watch
        # iPhone: 8h InBed (not used for stage coverage since Watch is preferred)
        # BUT: Watch has no InBed → time_in_bed from Watch = 0 → efficiency = None?
        # Spec: "time_in_bed_seconds = InBed sum" from preferred source.
        # Watch has no InBed, so time_in_bed = 0, sleep_efficiency = None.
        # To test 87.5% we need InBed from the Watch source.
        records = [
            _sleep(
                WATCH_SOURCE, "InBed", _dt(2024, 1, 15, 22), _dt(2024, 1, 16, 6)
            ),  # 8h
            _sleep(
                WATCH_SOURCE, "AsleepCore", _dt(2024, 1, 15, 23), _dt(2024, 1, 16, 0)
            ),  # 1h
            _sleep(
                WATCH_SOURCE, "AsleepDeep", _dt(2024, 1, 16, 1), _dt(2024, 1, 16, 3)
            ),  # 2h
            _sleep(
                WATCH_SOURCE, "AsleepREM", _dt(2024, 1, 16, 3), _dt(2024, 1, 16, 7)
            ),  # 4h
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None
        assert summary.preferred_source == WATCH_SOURCE
        assert summary.time_in_bed_seconds == pytest.approx(8 * 3600)
        assert summary.total_sleep_seconds == pytest.approx(7 * 3600)
        assert summary.sleep_efficiency_pct == pytest.approx(87.5)
        # core + deep + rem = 7h; total_sleep = 7h → 100%
        assert summary.stage_coverage_pct == pytest.approx(100.0)

    async def test_unspecified_only_gives_zero_stage_coverage(
        self, async_db_session, profile_id, importer
    ):
        """Night with only AsleepUnspecified: total_sleep > 0, coverage = 0 (not None)."""
        records = [
            _sleep(
                WATCH_SOURCE,
                "AsleepUnspecified",
                _dt(2024, 1, 16, 0),
                _dt(2024, 1, 16, 6),
            ),
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None
        assert summary.total_sleep_seconds == pytest.approx(6 * 3600)
        # (core+deep+rem) / total_sleep = 0 / 21600 = 0.0 (not None — total_sleep > 0)
        assert summary.stage_coverage_pct == pytest.approx(0.0)
        # Watch is preferred because it has stage records? No — AsleepUnspecified is NOT
        # in _STAGE_SLEEP. Watch has no stage records here, so it falls through to
        # any-source fallback. Only one source anyway.
        assert summary.preferred_source == WATCH_SOURCE

    async def test_no_inbed_gives_none_sleep_efficiency(
        self, async_db_session, profile_id, importer
    ):
        """Preferred source with no InBed records gives sleep_efficiency_pct = None."""
        records = [
            _sleep(
                WATCH_SOURCE, "AsleepCore", _dt(2024, 1, 16, 0), _dt(2024, 1, 16, 3)
            ),
            _sleep(WATCH_SOURCE, "AsleepREM", _dt(2024, 1, 16, 3), _dt(2024, 1, 16, 6)),
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None
        assert summary.sleep_efficiency_pct is None
        assert summary.time_in_bed_seconds == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Recompute-on-new-data
# ---------------------------------------------------------------------------


class TestRecomputeOnNewData:
    async def test_recompute_updates_summary_after_new_samples(
        self, async_db_session, profile_id, importer
    ):
        """Adding more samples for a night and recomputing updates the summary."""
        batch1 = [
            _sleep(
                WATCH_SOURCE, "AsleepCore", _dt(2024, 1, 15, 23), _dt(2024, 1, 16, 1)
            ),  # 2h
        ]
        await importer.insert_samples_batch(batch1, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary_before = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary_before is not None
        assert summary_before.core_seconds == pytest.approx(2 * 3600)

        # Add more samples and recompute.
        batch2 = [
            _sleep(
                WATCH_SOURCE, "AsleepDeep", _dt(2024, 1, 16, 1), _dt(2024, 1, 16, 3)
            ),  # 2h
        ]
        await importer.insert_samples_batch(batch2, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        summary_after = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary_after is not None
        assert summary_after.core_seconds == pytest.approx(2 * 3600)
        assert summary_after.deep_seconds == pytest.approx(2 * 3600)
        assert summary_after.total_sleep_seconds == pytest.approx(4 * 3600)


# ---------------------------------------------------------------------------
# Empty night
# ---------------------------------------------------------------------------


class TestEmptyNight:
    async def test_recompute_removes_summary_when_no_sleep_samples(
        self, async_db_session, profile_id, importer
    ):
        """After all sleep samples for a night are removed, recompute deletes the summary."""
        records = [
            _sleep(
                WATCH_SOURCE, "AsleepCore", _dt(2024, 1, 15, 23), _dt(2024, 1, 16, 1)
            ),
        ]
        await importer.insert_samples_batch(records, profile_id, async_db_session)
        await async_db_session.flush()
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        # Confirm summary exists.
        summary = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert summary is not None

        # Delete all sleep samples for the night (simulate external deletion).
        from sqlalchemy import delete as sa_delete

        await async_db_session.execute(
            sa_delete(HealthSample).where(
                HealthSample.profile_id == profile_id,
                HealthSample.night_date == SLEEP_NIGHT,
            )
        )
        await async_db_session.flush()

        # Recompute: no samples → summary must be removed.
        await importer.recompute_nightly_summary(
            profile_id, SLEEP_NIGHT, async_db_session
        )
        await async_db_session.flush()

        gone = await _get_summary(async_db_session, profile_id, SLEEP_NIGHT)
        assert gone is None, (
            "Summary must be deleted when no sleep samples remain for the night"
        )
