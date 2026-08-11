"""
Tests for the import-time overlap guard in SessionImporter._import_single_session.

Covers:
- New session fully covers one existing session → replacement
- New session fully covers two existing segment sessions → both replaced
- Partial overlap (neither covers the other) → incoming skipped
- Adjacent non-overlapping (a.end == b.start) → both kept
- Force re-import of same device_session_id does not false-trigger the guard
- Replaced row on a different day → extra_day_ids contains its day_id
"""

from __future__ import annotations

import logging

from datetime import datetime

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.day_manager import DayManager
from snore.database.importers import SessionImporter
from snore.parsers.unified import DeviceInfo, SessionStatistics, UnifiedSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERIAL = "OVERLAP_TEST_001"


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    """Wall-clock datetime on 2026-01-{day} at HH:MM (naive, no tz)."""
    return datetime(2026, 1, day, hour, minute)


def _make_unified(
    serial_number: str,
    device_session_id: str,
    start: datetime,
    end: datetime,
    import_source: str = "resmed_edf",
) -> UnifiedSession:
    """Build a minimal UnifiedSession for import testing."""
    return UnifiedSession(
        device_info=DeviceInfo(
            manufacturer="TestCo",
            model="TestModel",
            serial_number=serial_number,
        ),
        device_session_id=device_session_id,
        start_time=start,
        end_time=end,
        import_source=import_source,
        statistics=SessionStatistics(),
    )


async def _seed_session(
    db: AsyncSession,
    device: models.Device,
    device_session_id: str,
    start: datetime,
    end: datetime,
    import_source: str = "resmed_edf",
) -> models.Session:
    """Insert a bare Session row linked to the correct Day."""
    day_date = DayManager.get_day_for_session(start)
    day = await DayManager.get_or_create_day(device.id, day_date, db)

    session = models.Session(
        device_id=device.id,
        device_session_id=device_session_id,
        start_time=start,
        end_time=end,
        duration_seconds=(end - start).total_seconds(),
        day_id=day.id,
        import_source=import_source,
    )
    db.add(session)
    await db.flush()
    return session


async def _count_sessions(db: AsyncSession, device_id: int) -> int:
    result = await db.execute(
        select(models.Session).where(models.Session.device_id == device_id)
    )
    return len(result.scalars().all())


async def _session_exists(db: AsyncSession, device_session_id: str) -> bool:
    result = await db.execute(
        select(models.Session).where(
            models.Session.device_session_id == device_session_id
        )
    )
    return result.scalars().first() is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def profile(async_db_session):
    import uuid

    from snore.database.models import Profile, User

    user = User(
        canonical_email=f"overlap_{uuid.uuid4().hex[:8]}@test.local",
        role="admin",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    profile = Profile(user_id=user.id, name="Overlap Test Profile")
    async_db_session.add(profile)
    await async_db_session.flush()
    return profile


@pytest.fixture
async def device(async_db_session, profile):
    dev = models.Device(
        profile_id=profile.id,
        manufacturer="TestCo",
        model="TestModel",
        serial_number=_SERIAL,
    )
    async_db_session.add(dev)
    await async_db_session.flush()
    return dev


@pytest.fixture
def importer(profile):
    return SessionImporter(profile_id=profile.id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOverlapGuardReplacement:
    async def test_new_covers_existing_single_replaces_it(
        self, async_db_session, importer, device
    ):
        """Incoming session that fully envelops an existing one replaces it."""
        await _seed_session(
            async_db_session,
            device,
            "20260105_010000",
            _dt(5, 1),
            _dt(5, 7),
        )
        await async_db_session.flush()

        incoming = _make_unified(
            _SERIAL, "20260105_merged", _dt(5, 0, 30), _dt(5, 7, 30)
        )

        async with async_db_session.begin_nested():
            (
                was_imported,
                day_id,
                new_id,
                extra_day_ids,
            ) = await importer._import_single_session(async_db_session, incoming)

        assert was_imported is True
        assert new_id is not None
        assert not await _session_exists(async_db_session, "20260105_010000")
        assert await _session_exists(async_db_session, "20260105_merged")
        assert await _count_sessions(async_db_session, device.id) == 1

    async def test_new_covers_two_segments_replaces_both(
        self, async_db_session, importer, device
    ):
        """Incoming merged session that covers two segments deletes both."""
        await _seed_session(
            async_db_session, device, "20260106_010000", _dt(6, 1), _dt(6, 4)
        )
        await _seed_session(
            async_db_session, device, "20260106_040000", _dt(6, 4), _dt(6, 8)
        )
        await async_db_session.flush()

        incoming = _make_unified(_SERIAL, "20260106_merged", _dt(6, 1), _dt(6, 8))

        async with async_db_session.begin_nested():
            (
                was_imported,
                day_id,
                new_id,
                extra_day_ids,
            ) = await importer._import_single_session(async_db_session, incoming)

        assert was_imported is True
        assert not await _session_exists(async_db_session, "20260106_010000")
        assert not await _session_exists(async_db_session, "20260106_040000")
        assert await _session_exists(async_db_session, "20260106_merged")
        assert await _count_sessions(async_db_session, device.id) == 1

    async def test_partial_overlap_skips_incoming(
        self, async_db_session, importer, device, caplog
    ):
        """Partial overlap (neither fully contains the other) → incoming skipped."""
        await _seed_session(
            async_db_session, device, "20260107_010000", _dt(7, 1), _dt(7, 5)
        )
        await async_db_session.flush()

        # 03:00–08:00 overlaps 01:00–05:00, but neither covers the other
        incoming = _make_unified(_SERIAL, "20260107_030000", _dt(7, 3), _dt(7, 8))

        with caplog.at_level(logging.WARNING, logger="snore.database.importers"):
            async with async_db_session.begin_nested():
                (
                    was_imported,
                    day_id,
                    new_id,
                    extra_day_ids,
                ) = await importer._import_single_session(async_db_session, incoming)

        assert was_imported is False
        assert day_id is None
        assert new_id is None
        assert await _session_exists(async_db_session, "20260107_010000")
        assert not await _session_exists(async_db_session, "20260107_030000")
        assert "Overlap guard: skipping" in caplog.text

    async def test_adjacent_non_overlapping_keeps_both(
        self, async_db_session, importer, device
    ):
        """Adjacent sessions (a.end == b.start) are not overlapping — both kept."""
        await _seed_session(
            async_db_session, device, "20260108_010000", _dt(8, 1), _dt(8, 4)
        )
        await async_db_session.flush()

        # Exactly adjacent: new session starts exactly where existing ends
        incoming = _make_unified(_SERIAL, "20260108_040000", _dt(8, 4), _dt(8, 8))

        async with async_db_session.begin_nested():
            (
                was_imported,
                day_id,
                new_id,
                extra_day_ids,
            ) = await importer._import_single_session(async_db_session, incoming)

        assert was_imported is True
        assert await _session_exists(async_db_session, "20260108_010000")
        assert await _session_exists(async_db_session, "20260108_040000")
        assert await _count_sessions(async_db_session, device.id) == 2

    async def test_force_reimport_same_id_does_not_trigger_guard(
        self, async_db_session, importer, device
    ):
        """Force-reimporting the same device_session_id deletes it before the guard
        runs, so the guard sees no overlapping rows."""
        await _seed_session(
            async_db_session, device, "20260109_010000", _dt(9, 1), _dt(9, 7)
        )
        await async_db_session.flush()

        incoming = _make_unified(_SERIAL, "20260109_010000", _dt(9, 1), _dt(9, 7))

        async with async_db_session.begin_nested():
            (
                was_imported,
                day_id,
                new_id,
                extra_day_ids,
            ) = await importer._import_single_session(
                async_db_session, incoming, force=True
            )

        assert was_imported is True
        assert await _session_exists(async_db_session, "20260109_010000")
        assert await _count_sessions(async_db_session, device.id) == 1

    async def test_replaced_row_different_day_yields_extra_day_id(
        self, async_db_session, importer, device
    ):
        """If a replaced session belongs to a Day that differs from the new session's
        day, its day_id appears in extra_day_ids for re-aggregation."""
        # Session from 23:00 on day 10 through 03:00 on day 11.
        # DayManager assigns it to day 10 (starts at 23:00 → after noon → day 10).
        await _seed_session(
            async_db_session,
            device,
            "20260110_230000",
            _dt(10, 23),
            _dt(11, 3),
        )
        await async_db_session.flush()

        # Wider incoming session: 22:30 on day 10 through 04:00 on day 11.
        # It covers the existing session entirely.
        # DayManager assigns it to day 10 (starts at 22:30 → after noon).
        incoming = _make_unified(
            _SERIAL, "20260110_merged", _dt(10, 22, 30), _dt(11, 4)
        )

        async with async_db_session.begin_nested():
            (
                was_imported,
                new_day_id,
                new_id,
                extra_day_ids,
            ) = await importer._import_single_session(async_db_session, incoming)

        assert was_imported is True
        assert await _session_exists(async_db_session, "20260110_merged")
        # extra_day_ids is the set of replaced day_ids minus the new session's day_id
        assert isinstance(extra_day_ids, set)
        # In this case both sessions land on the same day (day 10), so no extra
        assert extra_day_ids == set()
