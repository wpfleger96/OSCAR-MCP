"""
Tests for check_data_integrity in BatchValidator.

Covers:
- Clean DB → zero issues
- Session with day_id IS NULL detected
- Overlapping session pair detected
- Adjacent (a.end == b.start) NOT flagged as overlap
- Cross-parser same-day detected
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.day_manager import DayManager
from snore.validation.batch import BatchValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 2, day, hour, minute)


_SENTINEL = object()


async def _seed_session(
    db: AsyncSession,
    device: models.Device,
    device_session_id: str,
    start: datetime,
    end: datetime,
    import_source: str = "resmed_edf",
    day_id: int | None | object = _SENTINEL,
) -> models.Session:
    """Seed a Session row with optional explicit day_id override.

    Pass day_id=None to leave the day_id column NULL.  Omit day_id to have it
    auto-resolved via DayManager (the normal path).
    """
    if day_id is _SENTINEL:
        day_date = DayManager.get_day_for_session(start)
        day = await DayManager.get_or_create_day(device.id, day_date, db)
        resolved_day_id: int | None = day.id
    else:
        resolved_day_id = day_id if day_id is not None else None

    session = models.Session(
        device_id=device.id,
        device_session_id=device_session_id,
        start_time=start,
        end_time=end,
        duration_seconds=(end - start).total_seconds(),
        day_id=resolved_day_id,
        import_source=import_source,
    )
    db.add(session)
    await db.flush()
    return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def profile(async_db_session):
    import uuid

    from snore.database.models import Profile, User

    user = User(
        canonical_email=f"integrity_{uuid.uuid4().hex[:8]}@test.local",
        role="admin",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    profile = Profile(user_id=user.id, name="Integrity Test Profile")
    async_db_session.add(profile)
    await async_db_session.flush()
    return profile


@pytest.fixture
async def device(async_db_session, profile):
    dev = models.Device(
        profile_id=profile.id,
        manufacturer="TestCo",
        model="TestModel",
        serial_number="INTEGRITY_TEST_001",
    )
    async_db_session.add(dev)
    await async_db_session.flush()
    return dev


@pytest.fixture
def validator(async_db_session, profile):
    return BatchValidator(async_db_session, profile.id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIntegrityCheckClean:
    async def test_clean_db_zero_issues(self, async_db_session, validator, device):
        """A freshly seeded DB with no anomalies reports zero issues."""
        await _seed_session(
            async_db_session, device, "20260201_010000", _dt(1, 1), _dt(1, 7)
        )

        report = await validator.check_data_integrity()

        assert report.total_issues == 0
        assert report.null_day_id_sessions == []
        assert report.overlapping_session_pairs == []
        assert report.cross_parser_same_day == []


class TestIntegrityCheckNullDayId:
    async def test_null_day_id_detected(self, async_db_session, validator, device):
        """Session with day_id IS NULL is reported in null_day_id_sessions."""
        session = await _seed_session(
            async_db_session,
            device,
            "20260202_010000",
            _dt(2, 1),
            _dt(2, 7),
            day_id=None,
        )

        report = await validator.check_data_integrity()

        assert session.id in report.null_day_id_sessions
        assert report.total_issues >= 1


class TestIntegrityCheckOverlap:
    async def test_overlapping_pair_detected(self, async_db_session, validator, device):
        """Two sessions on the same device with overlapping time ranges are flagged."""
        await _seed_session(
            async_db_session, device, "20260203_010000", _dt(3, 1), _dt(3, 6)
        )
        await _seed_session(
            async_db_session, device, "20260203_040000", _dt(3, 4), _dt(3, 8)
        )

        report = await validator.check_data_integrity()

        assert len(report.overlapping_session_pairs) == 1
        pair = report.overlapping_session_pairs[0]
        assert pair.device_id == device.id
        assert report.total_issues >= 1

    async def test_adjacent_sessions_not_flagged(
        self, async_db_session, validator, device
    ):
        """Sessions that share an endpoint (a.end == b.start) are not overlapping."""
        await _seed_session(
            async_db_session, device, "20260204_010000", _dt(4, 1), _dt(4, 4)
        )
        await _seed_session(
            async_db_session, device, "20260204_040000", _dt(4, 4), _dt(4, 8)
        )

        report = await validator.check_data_integrity()

        assert report.overlapping_session_pairs == []
        assert report.total_issues == 0


class TestIntegrityCheckCrossParser:
    async def test_cross_parser_same_day_detected(
        self, async_db_session, validator, device
    ):
        """Sessions from two different import_sources on the same Day are flagged."""
        await _seed_session(
            async_db_session,
            device,
            "20260205_010000",
            _dt(5, 1),
            _dt(5, 4),
            import_source="resmed_edf",
        )
        await _seed_session(
            async_db_session,
            device,
            "20260205_040000",
            _dt(5, 4),
            _dt(5, 8),
            import_source="oscar_binary",
        )

        report = await validator.check_data_integrity()

        assert len(report.cross_parser_same_day) == 1
        entry = report.cross_parser_same_day[0]
        assert entry.device_id == device.id
        assert "resmed_edf" in entry.import_sources
        assert "oscar_binary" in entry.import_sources
        assert report.total_issues >= 1

    async def test_same_parser_same_day_not_flagged(
        self, async_db_session, validator, device
    ):
        """Multiple sessions from the same import_source on a Day are fine."""
        await _seed_session(
            async_db_session,
            device,
            "20260206_010000",
            _dt(6, 1),
            _dt(6, 4),
            import_source="resmed_edf",
        )
        await _seed_session(
            async_db_session,
            device,
            "20260206_040000",
            _dt(6, 4),
            _dt(6, 8),
            import_source="resmed_edf",
        )

        report = await validator.check_data_integrity()

        assert report.cross_parser_same_day == []


class TestIntegrityCheckDeviceFilter:
    async def test_device_id_filter_restricts_results(
        self, async_db_session, profile, device
    ):
        """device_id= filter limits the check to sessions on that device only."""
        # Second device on the same profile
        device2 = models.Device(
            profile_id=profile.id,
            manufacturer="OtherCo",
            model="OtherModel",
            serial_number="INTEGRITY_TEST_002",
        )
        async_db_session.add(device2)
        await async_db_session.flush()

        # Overlapping sessions on device2 only
        await _seed_session(
            async_db_session, device2, "20260207_010000", _dt(7, 1), _dt(7, 6)
        )
        await _seed_session(
            async_db_session, device2, "20260207_040000", _dt(7, 4), _dt(7, 8)
        )

        validator = BatchValidator(async_db_session, profile.id)
        # Filter to device (no anomalies) — should not see device2's issues
        report = await validator.check_data_integrity(device_id=device.id)

        assert report.device_id_filter == device.id
        assert report.overlapping_session_pairs == []
        assert report.total_issues == 0
