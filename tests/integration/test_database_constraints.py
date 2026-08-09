"""
Tests for database constraints and foreign key behavior.

These tests verify:
- Foreign key constraints are enabled
- CASCADE delete behavior works correctly
- Orphaned record cleanup functionality
- Data integrity is maintained
"""

import sqlite3
import uuid

from datetime import datetime, timedelta
from typing import Any

import pytest

from sqlalchemy import select, text

from snore.database import models
from snore.database.importers import SessionImporter
from snore.database.session import init_database, session_scope


async def _create_profile(session: Any) -> models.Profile:
    """Create a User + Profile in *session* and return the Profile.

    Needed because devices.profile_id is NOT NULL after the multiuser migration.
    """
    user = models.User(
        canonical_email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        role="admin",
    )
    session.add(user)
    await session.flush()

    profile = models.Profile(user_id=user.id, name="Test Profile")
    session.add(profile)
    await session.flush()
    return profile


class TestForeignKeyConstraints:
    """Test that foreign key constraints are properly enabled and enforced."""

    async def test_foreign_keys_enabled(self, temp_db):
        """Test that foreign keys are enabled on connection."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            result = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
            assert result == 1, "Foreign keys should be enabled"

    async def test_cascade_delete_session_to_events(self, temp_db):
        """Test that deleting a session cascades to events."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            profile = await _create_profile(session)
            device = models.Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="Test",
                serial_number="TEST123",
            )
            session.add(device)
            await session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            test_session = models.Session(
                device_id=device.id,
                device_session_id="test_session",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
            )
            session.add(test_session)
            await session.flush()

            event = models.Event(
                session_id=test_session.id,
                event_type="Apnea",
                start_time=start_time + timedelta(hours=2),
                duration_seconds=15.0,
            )
            session.add(event)
            await session.commit()

            session_id = test_session.id

        async with session_scope() as session:
            events_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM events WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert events_before == 1

            sess = await session.get(models.Session, session_id)
            await session.delete(sess)
            await session.commit()

        async with session_scope() as session:
            events_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM events WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert events_after == 0, "Events should be deleted via CASCADE"

    async def test_cascade_delete_session_to_statistics(self, temp_db):
        """Test that deleting a session cascades to statistics."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            profile = await _create_profile(session)
            device = models.Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="Test",
                serial_number="TEST123",
            )
            session.add(device)
            await session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            test_session = models.Session(
                device_id=device.id,
                device_session_id="test_session",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
                has_statistics=True,
            )
            session.add(test_session)
            await session.flush()

            stats = models.Statistics(session_id=test_session.id, ahi=5.2)
            session.add(stats)
            await session.commit()

            session_id = test_session.id

        async with session_scope() as session:
            stats_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM statistics WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert stats_before == 1

            sess = await session.get(models.Session, session_id)
            await session.delete(sess)
            await session.commit()

        async with session_scope() as session:
            stats_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM statistics WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert stats_after == 0, "Statistics should be deleted via CASCADE"

    async def test_cascade_delete_session_to_settings(self, temp_db):
        """Test that deleting a session cascades to settings."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            profile = await _create_profile(session)
            device = models.Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="Test",
                serial_number="TEST123",
            )
            session.add(device)
            await session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            test_session = models.Session(
                device_id=device.id,
                device_session_id="test_session",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
            )
            session.add(test_session)
            await session.flush()

            setting = models.Setting(
                session_id=test_session.id, key="mode", value="CPAP"
            )
            session.add(setting)
            await session.commit()

            session_id = test_session.id

        async with session_scope() as session:
            settings_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM settings WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert settings_before == 1

            sess = await session.get(models.Session, session_id)
            await session.delete(sess)
            await session.commit()

        async with session_scope() as session:
            settings_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM settings WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert settings_after == 0, "Settings should be deleted via CASCADE"

    async def test_cascade_delete_session_to_waveforms(self, temp_db):
        """Test that deleting a session cascades to waveforms."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            profile = await _create_profile(session)
            device = models.Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="Test",
                serial_number="TEST123",
            )
            session.add(device)
            await session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            test_session = models.Session(
                device_id=device.id,
                device_session_id="test_session",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
                has_waveform_data=True,
            )
            session.add(test_session)
            await session.flush()

            waveform = models.Waveform(
                session_id=test_session.id,
                waveform_type="FlowRate",
                sample_rate=25.0,
                data_blob=b"\x00\x01\x02\x03",
            )
            session.add(waveform)
            await session.commit()

            session_id = test_session.id

        async with session_scope() as session:
            waveforms_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM waveforms WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert waveforms_before == 1

            sess = await session.get(models.Session, session_id)
            await session.delete(sess)
            await session.commit()

        async with session_scope() as session:
            waveforms_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM waveforms WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).scalar()
            assert waveforms_after == 0, "Waveforms should be deleted via CASCADE"


@pytest.mark.asyncio
class TestOrphanedRecordCleanup:
    """Test orphaned record detection and cleanup functionality."""

    async def test_cleanup_orphaned_events(self, temp_db):
        """Test cleanup of orphaned event records."""
        await init_database(str(temp_db))

        # Insert orphaned data using a raw sqlite3 connection with FK off.
        # SQLite requires PRAGMA foreign_keys changes to happen outside a
        # transaction; use a direct connection with autocommit semantics.
        raw = sqlite3.connect(str(temp_db), isolation_level=None)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute(
                "INSERT INTO events (session_id, event_type, start_time, duration_seconds)"
                " VALUES (999, 'Apnea', '2025-10-01 22:00:00', 15.0)"
            )
            raw.execute("PRAGMA foreign_keys = ON")
        finally:
            raw.close()

        async with session_scope() as session:
            orphaned_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM events WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_before == 1

            cleaned = await SessionImporter.cleanup_orphaned_records(session)
            assert sum(cleaned.values()) >= 1

        async with session_scope() as session:
            orphaned_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM events WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_after == 0

    async def test_cleanup_orphaned_statistics(self, temp_db):
        """Test cleanup of orphaned statistics records."""
        await init_database(str(temp_db))

        raw = sqlite3.connect(str(temp_db), isolation_level=None)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute(
                "INSERT INTO statistics (session_id, obstructive_apneas, central_apneas,"
                " mixed_apneas, hypopneas, reras, flow_limitations, ahi)"
                " VALUES (999, 0, 0, 0, 0, 0, 0, 5.2)"
            )
            raw.execute("PRAGMA foreign_keys = ON")
        finally:
            raw.close()

        async with session_scope() as session:
            orphaned_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM statistics WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_before == 1

            cleaned = await SessionImporter.cleanup_orphaned_records(session)
            assert sum(cleaned.values()) >= 1

        async with session_scope() as session:
            orphaned_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM statistics WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_after == 0

    async def test_cleanup_orphaned_settings(self, temp_db):
        """Test cleanup of orphaned settings records."""
        await init_database(str(temp_db))

        raw = sqlite3.connect(str(temp_db), isolation_level=None)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute(
                "INSERT INTO settings (session_id, key, value) VALUES (999, 'mode', 'CPAP')"
            )
            raw.execute("PRAGMA foreign_keys = ON")
        finally:
            raw.close()

        async with session_scope() as session:
            orphaned_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM settings WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_before == 1

            cleaned = await SessionImporter.cleanup_orphaned_records(session)
            assert sum(cleaned.values()) >= 1

        async with session_scope() as session:
            orphaned_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM settings WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_after == 0

    async def test_cleanup_orphaned_waveforms(self, temp_db):
        """Test cleanup of orphaned waveform records."""
        await init_database(str(temp_db))

        raw = sqlite3.connect(str(temp_db), isolation_level=None)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute(
                "INSERT INTO waveforms (session_id, waveform_type, sample_rate, data_blob)"
                " VALUES (999, 'FlowRate', 25.0, X'00010203')"
            )
            raw.execute("PRAGMA foreign_keys = ON")
        finally:
            raw.close()

        async with session_scope() as session:
            orphaned_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM waveforms WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_before == 1

            cleaned = await SessionImporter.cleanup_orphaned_records(session)
            assert sum(cleaned.values()) >= 1

        async with session_scope() as session:
            orphaned_after = (
                await session.execute(
                    text("SELECT COUNT(*) FROM waveforms WHERE session_id = 999")
                )
            ).scalar()
            assert orphaned_after == 0

    async def test_cleanup_multiple_orphaned_records(self, temp_db):
        """Test cleanup of multiple orphaned records across tables."""
        await init_database(str(temp_db))

        raw = sqlite3.connect(str(temp_db), isolation_level=None)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute(
                "INSERT INTO events (session_id, event_type, start_time, duration_seconds)"
                " VALUES (999, 'Apnea', '2025-10-01 22:00:00', 15.0)"
            )
            raw.execute(
                "INSERT INTO statistics (session_id, obstructive_apneas, central_apneas,"
                " mixed_apneas, hypopneas, reras, flow_limitations, ahi)"
                " VALUES (999, 0, 0, 0, 0, 0, 0, 5.2)"
            )
            raw.execute(
                "INSERT INTO settings (session_id, key, value) VALUES (999, 'mode', 'CPAP')"
            )
            raw.execute("PRAGMA foreign_keys = ON")
        finally:
            raw.close()

        async with session_scope() as session:
            cleaned = await SessionImporter.cleanup_orphaned_records(session)
            assert sum(cleaned.values()) == 3

        async with session_scope() as session:
            total_orphaned = (
                await session.execute(
                    text("SELECT COUNT(*) FROM events WHERE session_id = 999")
                )
            ).scalar()
            total_orphaned += (
                await session.execute(
                    text("SELECT COUNT(*) FROM statistics WHERE session_id = 999")
                )
            ).scalar()
            total_orphaned += (
                await session.execute(
                    text("SELECT COUNT(*) FROM settings WHERE session_id = 999")
                )
            ).scalar()

            assert total_orphaned == 0

    async def test_cleanup_no_orphaned_records(self, temp_db):
        """Test cleanup returns 0 when no orphaned records exist."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            cleaned = await SessionImporter.cleanup_orphaned_records(session)
            assert sum(cleaned.values()) == 0


class TestDataIntegrity:
    """Test database constraints and data integrity."""

    async def test_unique_constraint_device_session(self, temp_db):
        """Test unique constraint on (device_id, device_session_id)."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            profile = await _create_profile(session)
            device = models.Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="Test",
                serial_number="TEST123",
            )
            session.add(device)
            await session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            session1 = models.Session(
                device_id=device.id,
                device_session_id="duplicate_id",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
            )
            session.add(session1)
            await session.commit()

        async with session_scope() as session:
            device = (await session.execute(select(models.Device))).scalars().first()
            session2 = models.Session(
                device_id=device.id,
                device_session_id="duplicate_id",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
            )
            session.add(session2)

            with pytest.raises(Exception) as exc_info:
                await session.commit()

            await session.rollback()
            assert "UNIQUE constraint" in str(exc_info.value)

    async def test_unique_constraint_setting_key(self, temp_db):
        """Test unique constraint on (session_id, key) for settings."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            profile = await _create_profile(session)
            device = models.Device(
                profile_id=profile.id,
                manufacturer="Test",
                model="Test",
                serial_number="TEST123",
            )
            session.add(device)
            await session.flush()

            start_time = datetime(2025, 10, 1, 22, 0, 0)
            test_session = models.Session(
                device_id=device.id,
                device_session_id="test_session",
                start_time=start_time,
                end_time=start_time + timedelta(hours=8),
                duration_seconds=8 * 3600,
            )
            session.add(test_session)
            await session.flush()

            setting1 = models.Setting(
                session_id=test_session.id, key="mode", value="CPAP"
            )
            session.add(setting1)
            await session.commit()

        async with session_scope() as session:
            test_session = (
                (await session.execute(select(models.Session))).scalars().first()
            )
            setting2 = models.Setting(
                session_id=test_session.id, key="mode", value="APAP"
            )
            session.add(setting2)

            with pytest.raises(Exception) as exc_info:
                await session.commit()

            await session.rollback()
            assert "UNIQUE constraint" in str(exc_info.value)
