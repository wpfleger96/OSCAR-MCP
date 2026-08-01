"""
Tests for session import to day linking functionality.

These tests verify that:
- Imported sessions are properly linked to Day records
- Day records are created/updated during import
- Day-splitting logic works correctly
- list-profiles shows correct counts after import
"""

from datetime import datetime, timedelta

import pytest

from sqlalchemy import select

from snore.database import models
from snore.database.day_manager import DayManager
from snore.database.session import init_database, session_scope


@pytest.fixture
async def test_device_fixture(temp_db):
    """Create a device for testing."""
    await init_database(str(temp_db))

    async with session_scope() as session:
        device = models.Device(
            manufacturer="Test",
            model="Test Model",
            serial_number="TEST123",
        )
        session.add(device)
        await session.flush()

        return device.id


class TestDayRecordCreation:
    """Test that Day records are created and linked properly."""

    async def test_create_session_with_day_record(self, temp_db, test_device_fixture):
        """Test that creating a session with day linking works."""
        device_id = test_device_fixture

        start_time = datetime(2025, 10, 15, 22, 0, 0)

        async with session_scope() as session:
            day_date = DayManager.get_day_for_session(start_time)
            day = await DayManager.create_or_update_day(device_id, day_date, session)

            new_session = models.Session(
                device_id=device_id,
                device_session_id="test_session_1",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
                day_id=day.id,
            )
            session.add(new_session)
            await session.flush()

            assert new_session.day_id is not None, "Session should have day_id set"

            day = (
                (
                    await session.execute(
                        select(models.Day).where(models.Day.id == new_session.day_id)
                    )
                )
                .scalars()
                .first()
            )
            assert day is not None, "Day record should exist"
            assert day.device_id == device_id
            assert day.date == datetime(2025, 10, 15).date()

    async def test_link_sessions_to_same_day(self, temp_db, test_device_fixture):
        """Test that multiple sessions on same day link to same Day record."""
        device_id = test_device_fixture

        start_time_1 = datetime(2025, 10, 15, 22, 0, 0)
        start_time_2 = datetime(2025, 10, 16, 0, 30, 0)

        async with session_scope() as session:
            day_date_1 = DayManager.get_day_for_session(start_time_1)
            day_date_2 = DayManager.get_day_for_session(start_time_2)

            assert day_date_1 == day_date_2, "Both sessions should map to same day"

            day = await DayManager.create_or_update_day(device_id, day_date_1, session)

            sess1 = models.Session(
                device_id=device_id,
                device_session_id="test_session_1",
                start_time=start_time_1,
                end_time=start_time_1 + timedelta(hours=4),
                duration_seconds=4 * 3600,
                day_id=day.id,
            )
            sess2 = models.Session(
                device_id=device_id,
                device_session_id="test_session_2",
                start_time=start_time_2,
                end_time=start_time_2 + timedelta(hours=4),
                duration_seconds=4 * 3600,
                day_id=day.id,
            )
            session.add(sess1)
            session.add(sess2)
            await session.flush()

            assert sess1.day_id is not None
            assert sess2.day_id is not None

            assert sess1.day_id == sess2.day_id, (
                "Sessions on same day should link to same Day record"
            )


class TestDeviceDayIntegration:
    """Test that device-day relationships work correctly."""

    async def test_device_shows_correct_session_count(
        self, temp_db, test_device_fixture
    ):
        """Test that queries work correctly with day-linked sessions."""
        device_id = test_device_fixture

        async with session_scope() as session:
            for i in range(3):
                start_time = datetime(2025, 10, 15 + i, 22, 0, 0)

                day_date = DayManager.get_day_for_session(start_time)
                day = await DayManager.create_or_update_day(
                    device_id, day_date, session
                )

                sess = models.Session(
                    device_id=device_id,
                    device_session_id=f"test_session_{i}",
                    start_time=start_time,
                    end_time=start_time + timedelta(hours=8),
                    duration_seconds=8 * 3600,
                    day_id=day.id,
                )
                session.add(sess)

            await session.flush()

            total_sessions = len(
                (
                    await session.execute(
                        select(models.Session)
                        .join(models.Day, models.Session.day_id == models.Day.id)
                        .where(models.Day.device_id == device_id)
                    )
                )
                .scalars()
                .all()
            )

            assert total_sessions == 3, (
                "Should find all 3 sessions through Day relationship"
            )

            days_count = len(
                (
                    await session.execute(
                        select(models.Day).where(models.Day.device_id == device_id)
                    )
                )
                .scalars()
                .all()
            )
            assert days_count == 3, "Should have 3 separate days"

    async def test_sessions_without_day_id_not_counted(
        self, temp_db, test_device_fixture
    ):
        """Test that sessions without day_id are not counted (tests the bug we fixed)."""
        device_id = test_device_fixture

        async with session_scope() as session:
            orphan_session = models.Session(
                device_id=device_id,
                device_session_id="orphan_session",
                start_time=datetime(2025, 10, 15, 22, 0, 0),
                end_time=datetime(2025, 10, 16, 6, 0, 0),
                duration_seconds=8 * 3600,
                day_id=None,
            )
            session.add(orphan_session)
            await session.flush()

            sessions_through_day = len(
                (
                    await session.execute(
                        select(models.Session)
                        .join(models.Day, models.Session.day_id == models.Day.id)
                        .where(models.Day.device_id == device_id)
                    )
                )
                .scalars()
                .all()
            )

            assert sessions_through_day == 0, (
                "Sessions without day_id should not be counted"
            )

            direct_count = len(
                (
                    await session.execute(
                        select(models.Session).where(
                            models.Session.device_id == device_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert direct_count == 1, "Direct query should still find the session"


class TestDayManagerFunctions:
    """Test DayManager utility functions directly."""

    def test_get_day_for_session_after_split(self, temp_db):
        """Test get_day_for_session with time after split."""
        session_time = datetime(2025, 10, 15, 22, 0, 0)
        day_date = DayManager.get_day_for_session(session_time)

        assert day_date == datetime(2025, 10, 15).date()

    def test_get_day_for_session_before_split(self, temp_db):
        """Test get_day_for_session with time before split."""
        session_time = datetime(2025, 10, 16, 9, 0, 0)
        day_date = DayManager.get_day_for_session(session_time)

        assert day_date == datetime(2025, 10, 15).date()

    async def test_create_or_update_day_creates_new(self, temp_db, test_device_fixture):
        """Test that create_or_update_day creates new Day when none exists."""
        device_id = test_device_fixture

        async with session_scope() as session:
            day_date = datetime(2025, 10, 16).date()

            existing = (
                (
                    await session.execute(
                        select(models.Day).where(
                            models.Day.device_id == device_id,
                            models.Day.date == day_date,
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert existing is None

            day = await DayManager.create_or_update_day(device_id, day_date, session)
            await session.flush()

            assert day.id is not None
            assert day.device_id == device_id
            assert day.date == day_date

    async def test_create_or_update_day_updates_existing(
        self, temp_db, test_device_fixture
    ):
        """Test that create_or_update_day returns existing Day."""
        device_id = test_device_fixture

        async with session_scope() as session:
            day_date = datetime(2025, 10, 16).date()

            day1 = await DayManager.create_or_update_day(device_id, day_date, session)
            await session.flush()
            day1_id = day1.id

            day2 = await DayManager.create_or_update_day(device_id, day_date, session)

            assert day2.id == day1_id

            count = len(
                (
                    await session.execute(
                        select(models.Day).where(
                            models.Day.device_id == device_id,
                            models.Day.date == day_date,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert count == 1
