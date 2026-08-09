"""Unit tests for DatabaseService."""

from datetime import datetime, timedelta

from snore.database.models import (
    AnalysisResult,
    Device,
    Profile,
    Session,
    Statistics,
    User,
    Waveform,
)
from snore.services.database_service import DatabaseService
from snore.services.device_service import DeviceService


class TestDatabaseService:
    """Tests for DatabaseService.get_stats()."""

    async def test_empty_database_stats(self, async_db_session, temp_db):
        """Empty database returns zeros for all counts."""
        service = DatabaseService(async_db_session, profile_id=1)
        stats = await service.get_stats(str(temp_db))

        assert stats.profile_count == 0
        assert stats.device_count == 0
        assert stats.session_count == 0
        assert stats.day_count == 0
        assert stats.event_count == 0
        assert stats.waveform_count == 0
        assert stats.analysis_count == 0
        assert stats.pattern_count == 0
        assert stats.sessions_with_waveforms == 0
        assert stats.sessions_with_events == 0
        assert stats.sessions_with_analysis == 0
        assert stats.analyzable_session_count == 0
        assert stats.waveform_coverage_pct == 0
        assert stats.event_coverage_pct == 0
        assert stats.analysis_coverage_pct == 0
        assert stats.first_session is None
        assert stats.last_session is None
        assert stats.size_mb > 0

    async def test_stats_with_data(self, async_db_session, async_test_device, temp_db):
        """Database with data returns correct counts."""
        now = datetime.now()
        session1 = Session(
            device_id=async_test_device.id,
            device_session_id="test_1",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
            has_waveform_data=True,
            has_event_data=True,
        )
        session2 = Session(
            device_id=async_test_device.id,
            device_session_id="test_2",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=7),
            duration_seconds=25200,
            has_waveform_data=False,
            has_event_data=True,
        )
        async_db_session.add(session1)
        async_db_session.add(session2)
        await async_db_session.flush()

        stats1 = Statistics(session_id=session1.id, ahi=2.5, usage_hours=8.0)
        async_db_session.add(stats1)
        await async_db_session.commit()

        service = DatabaseService(async_db_session, profile_id=1)
        stats = await service.get_stats(str(temp_db))

        assert stats.device_count == 1
        assert stats.session_count == 2
        assert stats.sessions_with_waveforms == 1
        assert stats.sessions_with_events == 2
        assert stats.sessions_with_analysis == 0
        assert stats.analyzable_session_count == 0
        assert stats.waveform_coverage_pct == 50.0
        assert stats.event_coverage_pct == 100.0
        assert stats.analysis_coverage_pct == 0.0
        assert stats.first_session is not None
        assert stats.last_session is not None

    async def test_coverage_percentages(
        self, async_db_session, async_test_device, temp_db
    ):
        """Coverage percentages computed correctly."""
        now = datetime.now()
        for i in range(10):
            has_wf = i < 3
            has_ev = i < 7
            session = Session(
                device_id=async_test_device.id,
                device_session_id=f"test_{i}",
                start_time=now + timedelta(days=i),
                end_time=now + timedelta(days=i, hours=8),
                duration_seconds=28800,
                has_waveform_data=has_wf,
                has_event_data=has_ev,
            )
            async_db_session.add(session)
        await async_db_session.commit()

        service = DatabaseService(async_db_session, profile_id=1)
        stats = await service.get_stats(str(temp_db))

        assert stats.session_count == 10
        assert stats.sessions_with_waveforms == 3
        assert stats.sessions_with_events == 7
        assert stats.sessions_with_analysis == 0
        assert stats.analyzable_session_count == 0
        assert stats.waveform_coverage_pct == 30.0
        assert stats.event_coverage_pct == 70.0
        assert stats.analysis_coverage_pct == 0.0

    async def test_file_size_calculation(
        self, async_db_session, async_test_device, temp_db
    ):
        """Database file size is computed correctly."""
        service = DatabaseService(async_db_session, profile_id=1)
        stats = await service.get_stats(str(temp_db))

        assert stats.size_mb > 0
        assert stats.db_path == str(temp_db)

    async def test_nonexistent_file_size_zero(self, async_db_session):
        """Nonexistent database path returns 0 size."""
        service = DatabaseService(async_db_session, profile_id=1)
        fake_path = "/nonexistent/path/database.db"
        stats = await service.get_stats(fake_path)

        assert stats.size_mb == 0
        assert stats.db_path == fake_path

    async def test_analyzed_twice_counts_once_coverage_below_100(
        self, async_db_session, async_test_device, async_test_profile, temp_db
    ):
        """A session re-analyzed twice counts as one analyzed session; coverage stays ≤ 100%."""
        now = datetime.now()
        session = Session(
            device_id=async_test_device.id,
            device_session_id="reanalyzed",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        async_db_session.add(session)
        await async_db_session.flush()

        waveform = Waveform(
            session_id=session.id,
            waveform_type="flow",
            sample_rate=25.0,
            data_blob=b"\x00",
        )
        async_db_session.add(waveform)
        await async_db_session.flush()

        # Two analysis results for the same session (re-analysis scenario).
        for _ in range(2):
            result = AnalysisResult(
                session_id=session.id,
                timestamp_start=now,
                timestamp_end=now + timedelta(hours=8),
            )
            async_db_session.add(result)
        await async_db_session.commit()

        service = DatabaseService(async_db_session, profile_id=async_test_profile.id)
        stats = await service.get_stats(str(temp_db))

        assert stats.sessions_with_analysis == 1
        assert stats.analyzable_session_count == 1
        assert stats.analysis_coverage_pct == 100.0

    async def test_non_flow_session_excluded_from_denominator_shows_full_coverage(
        self, async_db_session, async_test_device, async_test_profile, temp_db
    ):
        """Session with no flow waveform is excluded from the analyzable denominator."""
        now = datetime.now()
        # session1 has a flow waveform and an analysis result.
        session1 = Session(
            device_id=async_test_device.id,
            device_session_id="with_flow",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        # session2 has only a pressure waveform — not analyzable.
        session2 = Session(
            device_id=async_test_device.id,
            device_session_id="pressure_only",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=8),
            duration_seconds=28800,
        )
        async_db_session.add_all([session1, session2])
        await async_db_session.flush()

        flow_waveform = Waveform(
            session_id=session1.id,
            waveform_type="flow",
            sample_rate=25.0,
            data_blob=b"\x00",
        )
        pressure_waveform = Waveform(
            session_id=session2.id,
            waveform_type="pressure",
            sample_rate=2.0,
            data_blob=b"\x00",
        )
        async_db_session.add_all([flow_waveform, pressure_waveform])
        await async_db_session.flush()

        analysis = AnalysisResult(
            session_id=session1.id,
            timestamp_start=now,
            timestamp_end=now + timedelta(hours=8),
        )
        async_db_session.add(analysis)
        await async_db_session.commit()

        service = DatabaseService(async_db_session, profile_id=async_test_profile.id)
        stats = await service.get_stats(str(temp_db))

        assert stats.session_count == 2
        assert stats.sessions_with_analysis == 1
        # Only session1 is analyzable; session2 (pressure waveform only) is excluded.
        assert stats.analyzable_session_count == 1
        assert stats.analysis_coverage_pct == 100.0

    async def test_zero_analyzable_sessions_coverage_is_zero(
        self, async_db_session, async_test_device, async_test_profile, temp_db
    ):
        """Zero analyzable sessions produces coverage of 0, not a division error."""
        now = datetime.now()
        # Sessions exist but none have a flow waveform.
        session = Session(
            device_id=async_test_device.id,
            device_session_id="no_flow",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        async_db_session.add(session)
        await async_db_session.commit()

        service = DatabaseService(async_db_session, profile_id=async_test_profile.id)
        stats = await service.get_stats(str(temp_db))

        assert stats.session_count == 1
        assert stats.analyzable_session_count == 0
        assert stats.sessions_with_analysis == 0
        assert stats.analysis_coverage_pct == 0

    async def test_profile_isolation_new_fields(
        self, async_db_session, async_test_device, async_test_profile, temp_db
    ):
        """sessions_with_analysis and analyzable_session_count are scoped to the active profile."""
        import uuid

        now = datetime.now()

        # Profile 1 session: has flow waveform + analysis result.
        session1 = Session(
            device_id=async_test_device.id,
            device_session_id="profile1_session",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        async_db_session.add(session1)
        await async_db_session.flush()

        waveform1 = Waveform(
            session_id=session1.id,
            waveform_type="flow",
            sample_rate=25.0,
            data_blob=b"\x00",
        )
        async_db_session.add(waveform1)
        await async_db_session.flush()

        analysis1 = AnalysisResult(
            session_id=session1.id,
            timestamp_start=now,
            timestamp_end=now + timedelta(hours=8),
        )
        async_db_session.add(analysis1)
        await async_db_session.flush()

        # Profile 2: separate user, profile, device, session with its own waveform/result.
        user2 = User(
            canonical_email=f"other_{uuid.uuid4().hex[:8]}@example.com",
            role="member",
        )
        async_db_session.add(user2)
        await async_db_session.flush()

        profile2 = Profile(user_id=user2.id, name="Other Profile")
        async_db_session.add(profile2)
        await async_db_session.flush()

        device2 = Device(
            profile_id=profile2.id,
            manufacturer="Other",
            model="Other Model",
            serial_number=f"OTHER_{uuid.uuid4().hex[:8]}",
        )
        async_db_session.add(device2)
        await async_db_session.flush()

        session2 = Session(
            device_id=device2.id,
            device_session_id="profile2_session",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        async_db_session.add(session2)
        await async_db_session.flush()

        waveform2 = Waveform(
            session_id=session2.id,
            waveform_type="flow",
            sample_rate=25.0,
            data_blob=b"\x00",
        )
        async_db_session.add(waveform2)
        await async_db_session.flush()

        analysis2 = AnalysisResult(
            session_id=session2.id,
            timestamp_start=now,
            timestamp_end=now + timedelta(hours=8),
        )
        async_db_session.add(analysis2)
        await async_db_session.commit()

        service = DatabaseService(async_db_session, profile_id=async_test_profile.id)
        stats = await service.get_stats(str(temp_db))

        # Only profile 1's session is counted.
        assert stats.session_count == 1
        assert stats.sessions_with_analysis == 1
        assert stats.analyzable_session_count == 1
        assert stats.analysis_coverage_pct == 100.0


class TestDeviceServiceListDevices:
    """Tests for DeviceService.list_devices() — device listing moved from DatabaseService."""

    async def test_list_devices_empty(self, async_db_session):
        """Empty database returns empty list."""
        service = DeviceService(async_db_session, profile_id=1)
        result = await service.list_devices()

        assert len(result) == 0

    async def test_list_devices_with_data(self, async_db_session, async_test_profile):
        """Returns correct device data."""
        device1 = Device(
            profile_id=async_test_profile.id,
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="TEST001",
        )
        device2 = Device(
            profile_id=async_test_profile.id,
            manufacturer="Philips",
            model="DreamStation",
            serial_number="TEST002",
        )
        device3 = Device(
            profile_id=async_test_profile.id,
            manufacturer="ResMed",
            model="AirCurve 10",
            serial_number="TEST003",
        )
        async_db_session.add_all([device1, device2, device3])
        await async_db_session.commit()

        service = DeviceService(async_db_session, profile_id=1)
        result = await service.list_devices()

        assert len(result) == 3
        assert result[0].manufacturer == "Philips"
        assert result[0].model == "DreamStation"
        assert result[1].manufacturer == "ResMed"
        assert result[1].model == "AirCurve 10"
        assert result[2].manufacturer == "ResMed"
        assert result[2].model == "AirSense 10"

    async def test_list_devices_ordering(self, async_db_session, async_test_profile):
        """Devices are ordered by manufacturer then model."""
        device1 = Device(
            profile_id=async_test_profile.id,
            manufacturer="Philips",
            model="DreamStation 2",
            serial_number="TEST001",
        )
        device2 = Device(
            profile_id=async_test_profile.id,
            manufacturer="ResMed",
            model="AirSense 11",
            serial_number="TEST002",
        )
        device3 = Device(
            profile_id=async_test_profile.id,
            manufacturer="Philips",
            model="DreamStation",
            serial_number="TEST003",
        )
        device4 = Device(
            profile_id=async_test_profile.id,
            manufacturer="ResMed",
            model="AirCurve 10",
            serial_number="TEST004",
        )
        async_db_session.add_all([device1, device2, device3, device4])
        await async_db_session.commit()

        service = DeviceService(async_db_session, profile_id=1)
        result = await service.list_devices()

        assert len(result) == 4
        assert result[0].manufacturer == "Philips"
        assert result[0].model == "DreamStation"
        assert result[1].manufacturer == "Philips"
        assert result[1].model == "DreamStation 2"
        assert result[2].manufacturer == "ResMed"
        assert result[2].model == "AirCurve 10"
        assert result[3].manufacturer == "ResMed"
        assert result[3].model == "AirSense 11"

    async def test_list_devices_includes_all_fields(
        self, async_db_session, async_test_profile
    ):
        """Returns all identity fields including the new firmware/hardware/product_code."""
        device = Device(
            profile_id=async_test_profile.id,
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="12345ABC",
            firmware_version="SX567-0401",
            hardware_version="R003",
            product_code="37037",
        )
        async_db_session.add(device)
        await async_db_session.commit()

        service = DeviceService(async_db_session, profile_id=1)
        result = await service.list_devices()

        assert len(result) == 1
        assert result[0].id == device.id
        assert result[0].manufacturer == "ResMed"
        assert result[0].model == "AirSense 10"
        assert result[0].serial_number == "12345ABC"
        assert result[0].firmware_version == "SX567-0401"
        assert result[0].hardware_version == "R003"
        assert result[0].product_code == "37037"
        assert result[0].first_seen is not None


class TestVacuum:
    async def test_vacuum_returns_success_status(self, async_db_session, temp_db):
        service = DatabaseService(async_db_session, profile_id=1)
        result = service.vacuum_sqlite(str(temp_db))
        assert result.status == "success"
