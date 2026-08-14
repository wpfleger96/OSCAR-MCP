"""Integration tests for the ResMed session-chaining pipeline.

Tests exercise the full chain_session_segments → SessionImporter flow, covering
three behavioral guarantees introduced by the proximity-chaining refactor:

1. Noon rollover: two EDF segments crossing noon chain into one session, with
   the Day row landing on the previous calendar day.
2. Blip + sleep: a brief diagnostic blip and the subsequent sleep session form
   separate chains on the same night; day total_therapy_hours reflects actual
   mask-on usage, not the inter-chain span.
3. Legacy row repair: an old noon-bucket "YYYYMMDD_merged" row is purged by a
   plain re-import (no --force), and day totals are recomputed from the new
   chained sessions.

Tests 1 and 2 exercise the parser seam: synthetic stub EDF files drive
``chain_session_segments``, whose output is used to build ``UnifiedSession``
objects manually (parsing real signal bodies from stub EDFs is impractical and
not the behavior under test).  The seam boundary is the ``(night_date, chain_id,
segments)`` tuple list returned by ``chain_session_segments``.

Test 3 works entirely at the importer level: ORM-seeded legacy rows are
imported first, then replaced by new-format ``UnifiedSession`` objects.
"""

from __future__ import annotations

import uuid

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.importers import SessionImporter
from snore.parsers.resmed_file_index import chain_session_segments
from snore.parsers.unified import DeviceInfo, SessionStatistics, UnifiedSession
from tests.integration.conftest import _make_profile

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Synthetic EDF stub helpers (layout matches resmed_file_index.get_segment_duration_seconds)
# ---------------------------------------------------------------------------


def _make_edf_header(num_records: int, record_duration: float) -> bytes:
    """Build a 256-byte EDF header with num_records at [236:244] and record_duration at [244:252]."""
    header = bytearray(b" " * 256)
    header[236:244] = str(num_records).ljust(8).encode("ascii")
    header[244:252] = f"{record_duration:g}".ljust(8).encode("ascii")
    return bytes(header)


def _write_edf(
    path: Path, num_records: int = 1800, record_duration: float = 1.0
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_make_edf_header(num_records, record_duration))
    return path


def _make_segment(
    datalog_dir: Path,
    start: datetime,
    duration_s: float,
    file_types: tuple[str, ...] = ("BRP",),
) -> str:
    """Write stub EDF file(s) and return the segment's session_id (YYYYMMDD_HHMMSS)."""
    session_id = start.strftime("%Y%m%d_%H%M%S")
    for ft in file_types:
        _write_edf(datalog_dir / f"{session_id}_{ft}.edf", int(duration_s), 1.0)
    return session_id


# ---------------------------------------------------------------------------
# UnifiedSession builder for seam-level import tests
# ---------------------------------------------------------------------------

_MANUFACTURER = "ResMed"
_MODEL = "AirSense 10 AutoSet"


def _build_unified(
    serial: str,
    device_session_id: str,
    start: datetime,
    end: datetime,
    mask_on_segments: list[tuple[float, float]] | None = None,
    usage_hours: float | None = None,
) -> UnifiedSession:
    """Build a minimal UnifiedSession for import testing.

    If mask_on_segments is provided, usage_hours is derived from them unless
    explicitly overridden.  has_statistics is set to True so the Statistics
    row is written and day aggregation can use statistics.usage_hours.
    """
    stats = SessionStatistics()
    if usage_hours is not None:
        stats.usage_hours = usage_hours
    elif mask_on_segments is not None:
        stats.usage_hours = sum(e - s for s, e in mask_on_segments) / 3600.0

    session = UnifiedSession(
        device_info=DeviceInfo(
            manufacturer=_MANUFACTURER,
            model=_MODEL,
            serial_number=serial,
        ),
        device_session_id=device_session_id,
        start_time=start,
        end_time=end,
        import_source="resmed_edf",
        statistics=stats,
        has_statistics=True,
    )
    if mask_on_segments is not None:
        session.mask_on_segments = mask_on_segments
    return session


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------


async def _get_device_by_serial(db: AsyncSession, serial: str) -> models.Device | None:
    result = await db.execute(
        select(models.Device).where(models.Device.serial_number == serial)
    )
    return result.scalars().first()


async def _session_exists(db: AsyncSession, device_session_id: str) -> bool:
    result = await db.execute(
        select(models.Session).where(
            models.Session.device_session_id == device_session_id
        )
    )
    return result.scalars().first() is not None


async def _get_all_sessions(db: AsyncSession, device_id: int) -> list[models.Session]:
    result = await db.execute(
        select(models.Session).where(models.Session.device_id == device_id)
    )
    return list(result.scalars().all())


async def _get_days_for_device(db: AsyncSession, device_id: int) -> list[models.Day]:
    result = await db.execute(
        select(models.Day).where(models.Day.device_id == device_id)
    )
    return list(result.scalars().all())


async def _get_day_for_date(
    db: AsyncSession, device_id: int, day_date: date
) -> models.Day | None:
    result = await db.execute(
        select(models.Day).where(
            models.Day.device_id == device_id,
            models.Day.date == day_date,
        )
    )
    return result.scalars().first()


# ===========================================================================
# Test 1 — Noon rollover
# ===========================================================================


class TestNoonRolloverChaining:
    """A pre-noon + post-noon segment pair chains into one session landing on the previous day."""

    def test_noon_rollover_parser_produces_one_chain(self, tmp_path: Path) -> None:
        """chain_session_segments collapses a pre/post-noon pair into exactly one chain."""
        datalog = tmp_path / "DATALOG"

        # Pre-noon segment: 03:19:33, duration = 8h 36m = 30960 s → ends 11:55:33
        pre_noon_start = datetime(2026, 1, 15, 3, 19, 33)
        pre_noon_duration_s = 8 * 3600 + 36 * 60  # 30960 s
        pre_noon_id = _make_segment(datalog, pre_noon_start, pre_noon_duration_s)

        # Post-noon segment: 12:00:09 — gap from end of pre-noon is 4m 36s = 276 s < 4h threshold
        post_noon_start = datetime(2026, 1, 15, 12, 0, 9)
        post_noon_duration_s = int(3.5 * 3600)  # 12600 s
        post_noon_id = _make_segment(datalog, post_noon_start, post_noon_duration_s)

        # Act
        chains = chain_session_segments(datalog)

        # Assert
        assert len(chains) == 1, "Noon-rollover pair must produce exactly one chain"
        night_date, chain_id, segments = chains[0]
        assert night_date == "20260114", (
            "Pre-noon chain start (03:19) must assign night_date to the previous calendar day"
        )
        assert chain_id == pre_noon_id, (
            "chain_id must be the first (pre-noon) segment's session_id"
        )
        assert pre_noon_id in segments, (
            "Pre-noon segment must be in the chain's segment dict"
        )
        assert post_noon_id in segments, (
            "Post-noon segment must be in the chain's segment dict"
        )

    async def test_noon_rollover_import_creates_previous_day_row(
        self, async_db_session: AsyncSession
    ) -> None:
        """Importing a noon-rollover chain creates a Day row for the night before the calendar date."""
        profile = await _make_profile(async_db_session)
        serial = f"NR_{uuid.uuid4().hex[:8]}"
        importer = SessionImporter(profile_id=profile.id)

        pre_noon_start = datetime(2026, 1, 15, 3, 19, 33)
        pre_noon_duration_s = 8 * 3600 + 36 * 60  # 30960 s

        post_noon_start = datetime(2026, 1, 15, 12, 0, 9)
        post_noon_duration_s = int(3.5 * 3600)  # 12600 s
        post_noon_end = post_noon_start + timedelta(seconds=post_noon_duration_s)

        # Mirror the mask_on_segments that the parser would compute for a two-segment chain.
        seg2_offset = (post_noon_start - pre_noon_start).total_seconds()  # 31236 s
        mask_on = [
            (0.0, float(pre_noon_duration_s)),
            (seg2_offset, seg2_offset + post_noon_duration_s),
        ]
        expected_usage_hours = (
            pre_noon_duration_s + post_noon_duration_s
        ) / 3600.0  # ~12.1 h

        chain_id = pre_noon_start.strftime("%Y%m%d_%H%M%S")
        session = _build_unified(
            serial,
            f"{chain_id}_merged",
            pre_noon_start,
            post_noon_end,
            mask_on_segments=mask_on,
        )

        # Act
        imported, skipped, failed, _ = await importer.import_sessions_batch(
            [session], db=async_db_session
        )

        # Assert: import succeeded
        assert imported == 1
        assert skipped == 0
        assert failed == 0
        assert await _session_exists(async_db_session, f"{chain_id}_merged")

        device = await _get_device_by_serial(async_db_session, serial)
        assert device is not None

        days = await _get_days_for_device(async_db_session, device.id)
        assert len(days) == 1, "One chain must produce exactly one Day row"

        day = days[0]
        assert day.date == date(2026, 1, 14), (
            "Pre-noon chain start must map to the previous calendar day (2026-01-14)"
        )
        assert pytest.approx(day.total_therapy_hours, abs=0.01) == expected_usage_hours


# ===========================================================================
# Test 2 — Blip + sleep: two chains, same night_date, usage-hours correct
# ===========================================================================


class TestBlipAndSleepChaining:
    """A diagnostic blip and main sleep produce separate chains; day usage reflects actual therapy."""

    def test_blip_and_sleep_produce_two_chains(self, tmp_path: Path) -> None:
        """A 2-min blip followed by sleep 5.76 h later produces two distinct chains."""
        datalog = tmp_path / "DATALOG"

        # 2-min blip at 18:46 — ends at 18:48:00
        blip_start = datetime(2026, 2, 5, 18, 46, 0)
        blip_duration_s = 120  # 2 min
        blip_id = _make_segment(datalog, blip_start, blip_duration_s)

        # Sleep starting 00:33 on Feb 6 — gap from blip end = 5 h 45 min > 4 h threshold
        sleep_start = datetime(2026, 2, 6, 0, 33, 0)
        sleep_duration_s = (9 * 3600 + 12 * 60) - (
            33 * 60
        )  # 00:33 → 09:12 = 8 h 39 min = 31140 s
        sleep_id = _make_segment(datalog, sleep_start, sleep_duration_s)

        # Act
        chains = chain_session_segments(datalog)

        # Assert two chains
        assert len(chains) == 2, "Blip and sleep separated by >4 h must form two chains"
        chain_ids = [c[1] for c in chains]
        assert blip_id in chain_ids
        assert sleep_id in chain_ids

    def test_blip_and_sleep_share_same_night_date(self, tmp_path: Path) -> None:
        """Both chains (blip at 18:46 and sleep at 00:33 next day) belong to the same night."""
        datalog = tmp_path / "DATALOG"

        blip_start = datetime(2026, 2, 5, 18, 46, 0)
        _make_segment(datalog, blip_start, 120)

        sleep_start = datetime(2026, 2, 6, 0, 33, 0)
        _make_segment(datalog, sleep_start, 31140)

        chains = chain_session_segments(datalog)

        night_dates = {c[0] for c in chains}
        assert night_dates == {"20260205"}, (
            "Blip (18:46, same day → Feb 5) and sleep (00:33 next day, pre-noon → Feb 5) "
            "must share night_date 20260205"
        )

    async def test_blip_sleep_day_usage_reflects_mask_on_time(
        self, async_db_session: AsyncSession
    ) -> None:
        """Day total_therapy_hours sums mask-on usage from both sessions, NOT the inter-session span."""
        profile = await _make_profile(async_db_session)
        serial = f"BS_{uuid.uuid4().hex[:8]}"
        importer = SessionImporter(profile_id=profile.id)

        # Blip: 18:46:00 → 18:48:00 (120 s)
        blip_start = datetime(2026, 2, 5, 18, 46, 0)
        blip_end = blip_start + timedelta(seconds=120)
        blip_usage_hours = 120.0 / 3600.0  # ≈ 0.033 h

        # Sleep: 00:33:00 → 09:12:00 (31140 s = 8 h 39 min)
        sleep_start = datetime(2026, 2, 6, 0, 33, 0)
        sleep_duration_s = 31140
        sleep_end = sleep_start + timedelta(seconds=sleep_duration_s)
        sleep_usage_hours = sleep_duration_s / 3600.0  # ≈ 8.65 h

        expected_total = blip_usage_hours + sleep_usage_hours  # ≈ 8.683 h

        # The old noon-bucket behavior would have put blip and sleep in a single
        # session spanning 18:46 on Feb 5 to 09:12 on Feb 6 = 14 h 26 min ≈ 14.43 h.
        wrong_span_hours = (
            sleep_end - blip_start
        ).total_seconds() / 3600.0  # ≈ 14.43 h

        blip_session = _build_unified(
            serial,
            blip_start.strftime("%Y%m%d_%H%M%S"),
            blip_start,
            blip_end,
            mask_on_segments=[(0.0, 120.0)],
        )
        sleep_session = _build_unified(
            serial,
            sleep_start.strftime("%Y%m%d_%H%M%S"),
            sleep_start,
            sleep_end,
            mask_on_segments=[(0.0, float(sleep_duration_s))],
        )

        # Act
        imported, skipped, failed, _ = await importer.import_sessions_batch(
            [blip_session, sleep_session], db=async_db_session
        )

        # Assert: both sessions imported successfully
        assert imported == 2
        assert skipped == 0
        assert failed == 0

        device = await _get_device_by_serial(async_db_session, serial)
        assert device is not None

        # Both sessions belong to night 2026-02-05
        day = await _get_day_for_date(async_db_session, device.id, date(2026, 2, 5))
        assert day is not None, "Day row for 2026-02-05 must exist"
        assert day.session_count == 2

        assert day.total_therapy_hours < wrong_span_hours - 1.0, (
            "total_therapy_hours must be less than the naive span (14.43 h) by a wide margin"
        )
        assert pytest.approx(day.total_therapy_hours, abs=0.01) == expected_total


# ===========================================================================
# Test 3 — Re-import repairs legacy rows
# ===========================================================================


class TestLegacyRowRepair:
    """A plain re-import replaces stale YYYYMMDD_merged rows and recomputes day totals."""

    async def test_reimport_replaces_old_format_row_and_recomputes_day(
        self, async_db_session: AsyncSession
    ) -> None:
        """Seeded 'YYYYMMDD_merged' row is purged by a plain import; day totals reflect new sessions."""
        profile = await _make_profile(async_db_session)
        serial = f"LR_{uuid.uuid4().hex[:8]}"
        importer = SessionImporter(profile_id=profile.id)

        # --- Arrange: seed the legacy old-format row via the importer so the full
        # device/day/statistics chain is correctly wired up.
        legacy_start = datetime(2026, 1, 29, 22, 0, 0)
        legacy_end = datetime(2026, 1, 30, 22, 0, 0)  # 24-hour span
        legacy_usage_h = 24.0  # stale value that should be replaced

        legacy_session = _build_unified(
            serial,
            "20260130_merged",
            legacy_start,
            legacy_end,
            usage_hours=legacy_usage_h,
        )
        # The legacy session has usage_hours=24.0 in its statistics.
        legacy_session.statistics.usage_hours = legacy_usage_h

        seed_imported, *_ = await importer.import_sessions_batch(
            [legacy_session], db=async_db_session
        )
        assert seed_imported == 1, "Seed import of legacy row must succeed"
        assert await _session_exists(async_db_session, "20260130_merged")

        device = await _get_device_by_serial(async_db_session, serial)
        assert device is not None

        # Verify the seeded day reflects the legacy's inflated usage_hours.
        day_before = await _get_day_for_date(
            async_db_session, device.id, date(2026, 1, 29)
        )
        assert day_before is not None
        assert day_before.session_count == 1
        assert day_before.total_therapy_hours == pytest.approx(legacy_usage_h, abs=0.01)

        # --- Act: import new-format chained sessions that overlap the legacy row.
        # Proximity-chained IDs use YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_merged — NOT matched
        # by the old-format pattern ^\d{8}_merged$.
        new_start = datetime(2026, 1, 29, 22, 30, 0)
        new_end = datetime(2026, 1, 30, 7, 0, 0)  # 8.5 h span
        new_usage_h = 8.0  # realistic replacement value
        new_mask_on = [(0.0, new_usage_h * 3600)]

        new_session = _build_unified(
            serial,
            "20260129_223000_merged",
            new_start,
            new_end,
            mask_on_segments=new_mask_on,
        )

        # Import WITHOUT force — the legacy old-format row must be purged unconditionally.
        re_imported, re_skipped, re_failed, _ = await importer.import_sessions_batch(
            [new_session], db=async_db_session
        )

        # --- Assert: import succeeded without force
        assert re_imported == 1, "New-format session must be imported without force"
        assert re_skipped == 0
        assert re_failed == 0

        # Old-format row must be gone; new-format row must exist.
        assert not await _session_exists(async_db_session, "20260130_merged"), (
            "Old noon-bucket row must be purged by the plain re-import"
        )
        assert await _session_exists(async_db_session, "20260129_223000_merged"), (
            "New proximity-chained session must be present after import"
        )

        # Only one session remains for the device.
        all_sessions = await _get_all_sessions(async_db_session, device.id)
        assert len(all_sessions) == 1

        # Day totals must reflect the new session's usage, not the legacy's inflated span.
        day_after = await _get_day_for_date(
            async_db_session, device.id, date(2026, 1, 29)
        )
        assert day_after is not None, (
            "Day row for 2026-01-29 must survive re-aggregation"
        )
        assert day_after.session_count == 1
        assert day_after.total_therapy_hours == pytest.approx(new_usage_h, abs=0.01), (
            "total_therapy_hours must be recomputed from the new session's usage_hours, "
            "not the legacy row's 24-h span"
        )

    async def test_new_format_session_not_skipped_as_partial_overlap(
        self, async_db_session: AsyncSession
    ) -> None:
        """New-format session contained within an old-format row's span is NOT skipped.

        Under the old noon-bucket code, a new narrow session (22:30–07:00) fully
        contained within the stale 22:00–22:00 span would have been skipped because
        ``incoming.start > existing.start`` makes it look like a partial overlap.
        With the unconditional purge of old-format rows, the legacy row is removed
        first so the overlap check sees no blocking rows.
        """
        profile = await _make_profile(async_db_session)
        serial = f"NF_{uuid.uuid4().hex[:8]}"
        importer = SessionImporter(profile_id=profile.id)

        # Seed stale legacy row with a wide 24-h span.
        legacy_session = _build_unified(
            serial,
            "20260310_merged",
            datetime(2026, 3, 9, 22, 0, 0),
            datetime(2026, 3, 10, 22, 0, 0),
            usage_hours=24.0,
        )
        await importer.import_sessions_batch([legacy_session], db=async_db_session)
        assert await _session_exists(async_db_session, "20260310_merged")

        # New-format session strictly contained within the old row's span.
        new_session = _build_unified(
            serial,
            "20260309_223000_merged",
            datetime(2026, 3, 9, 22, 30, 0),
            datetime(2026, 3, 10, 7, 0, 0),
            usage_hours=8.0,
        )
        imported, skipped, failed, _ = await importer.import_sessions_batch(
            [new_session], db=async_db_session
        )

        # Must be imported — NOT skipped as a partial overlap.
        assert imported == 1, (
            "New-format session must not be skipped as a partial overlap of the purged legacy row"
        )
        assert skipped == 0
        assert failed == 0
        assert not await _session_exists(async_db_session, "20260310_merged")
        assert await _session_exists(async_db_session, "20260309_223000_merged")
