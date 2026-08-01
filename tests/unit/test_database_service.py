"""Unit tests for DatabaseService."""

from datetime import datetime, timedelta

from snore.database.models import Device, Session, Statistics
from snore.services.database_service import DatabaseService
from snore.services.device_service import DeviceService


class TestDatabaseService:
    """Tests for DatabaseService.get_stats()."""

    def test_empty_database_stats(self, db_session, temp_db):
        """Empty database returns zeros for all counts."""
        service = DatabaseService(db_session)
        stats = service.get_stats(str(temp_db))

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
        assert stats.waveform_coverage_pct == 0
        assert stats.event_coverage_pct == 0
        assert stats.analysis_coverage_pct == 0
        assert stats.first_session is None
        assert stats.last_session is None
        assert stats.size_mb > 0

    def test_stats_with_data(self, db_session, test_device, temp_db):
        """Database with data returns correct counts."""
        now = datetime.now()
        session1 = Session(
            device_id=test_device.id,
            device_session_id="test_1",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
            has_waveform_data=True,
            has_event_data=True,
        )
        session2 = Session(
            device_id=test_device.id,
            device_session_id="test_2",
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=7),
            duration_seconds=25200,
            has_waveform_data=False,
            has_event_data=True,
        )
        db_session.add(session1)
        db_session.add(session2)
        db_session.flush()

        stats1 = Statistics(session_id=session1.id, ahi=2.5, usage_hours=8.0)
        db_session.add(stats1)
        db_session.commit()

        service = DatabaseService(db_session)
        stats = service.get_stats(str(temp_db))

        assert stats.device_count == 1
        assert stats.session_count == 2
        assert stats.sessions_with_waveforms == 1
        assert stats.sessions_with_events == 2
        assert stats.waveform_coverage_pct == 50.0
        assert stats.event_coverage_pct == 100.0
        assert stats.analysis_coverage_pct == 0.0
        assert stats.first_session is not None
        assert stats.last_session is not None

    def test_coverage_percentages(self, db_session, test_device, temp_db):
        """Coverage percentages computed correctly."""
        now = datetime.now()
        for i in range(10):
            has_wf = i < 3
            has_ev = i < 7
            session = Session(
                device_id=test_device.id,
                device_session_id=f"test_{i}",
                start_time=now + timedelta(days=i),
                end_time=now + timedelta(days=i, hours=8),
                duration_seconds=28800,
                has_waveform_data=has_wf,
                has_event_data=has_ev,
            )
            db_session.add(session)
        db_session.commit()

        service = DatabaseService(db_session)
        stats = service.get_stats(str(temp_db))

        assert stats.session_count == 10
        assert stats.sessions_with_waveforms == 3
        assert stats.sessions_with_events == 7
        assert stats.waveform_coverage_pct == 30.0
        assert stats.event_coverage_pct == 70.0
        assert stats.analysis_coverage_pct == 0.0

    def test_file_size_calculation(self, db_session, test_device, temp_db):
        """Database file size is computed correctly."""
        service = DatabaseService(db_session)
        stats = service.get_stats(str(temp_db))

        assert stats.size_mb > 0
        assert stats.db_path == str(temp_db)

    def test_nonexistent_file_size_zero(self, db_session):
        """Nonexistent database path returns 0 size."""
        service = DatabaseService(db_session)
        fake_path = "/nonexistent/path/database.db"
        stats = service.get_stats(fake_path)

        assert stats.size_mb == 0
        assert stats.db_path == fake_path


class TestDeviceServiceListDevices:
    """Tests for DeviceService.list_devices() — device listing moved from DatabaseService."""

    def test_list_devices_empty(self, db_session):
        """Empty database returns empty list."""
        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 0

    def test_list_devices_with_data(self, db_session):
        """Returns correct device data."""
        device1 = Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="TEST001",
        )
        device2 = Device(
            manufacturer="Philips",
            model="DreamStation",
            serial_number="TEST002",
        )
        device3 = Device(
            manufacturer="ResMed",
            model="AirCurve 10",
            serial_number="TEST003",
        )
        db_session.add_all([device1, device2, device3])
        db_session.commit()

        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 3
        assert result[0].manufacturer == "Philips"
        assert result[0].model == "DreamStation"
        assert result[1].manufacturer == "ResMed"
        assert result[1].model == "AirCurve 10"
        assert result[2].manufacturer == "ResMed"
        assert result[2].model == "AirSense 10"

    def test_list_devices_ordering(self, db_session):
        """Devices are ordered by manufacturer then model."""
        device1 = Device(
            manufacturer="Philips",
            model="DreamStation 2",
            serial_number="TEST001",
        )
        device2 = Device(
            manufacturer="ResMed",
            model="AirSense 11",
            serial_number="TEST002",
        )
        device3 = Device(
            manufacturer="Philips",
            model="DreamStation",
            serial_number="TEST003",
        )
        device4 = Device(
            manufacturer="ResMed",
            model="AirCurve 10",
            serial_number="TEST004",
        )
        db_session.add_all([device1, device2, device3, device4])
        db_session.commit()

        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 4
        assert result[0].manufacturer == "Philips"
        assert result[0].model == "DreamStation"
        assert result[1].manufacturer == "Philips"
        assert result[1].model == "DreamStation 2"
        assert result[2].manufacturer == "ResMed"
        assert result[2].model == "AirCurve 10"
        assert result[3].manufacturer == "ResMed"
        assert result[3].model == "AirSense 11"

    def test_list_devices_includes_all_fields(self, db_session):
        """Returns all identity fields including the new firmware/hardware/product_code."""
        device = Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="12345ABC",
            firmware_version="SX567-0401",
            hardware_version="R003",
            product_code="37037",
        )
        db_session.add(device)
        db_session.commit()

        service = DeviceService(db_session)
        result = service.list_devices()

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
    def test_vacuum_returns_success_status(self, db_session, temp_db):
        service = DatabaseService(db_session)
        result = service.vacuum_sqlite(str(temp_db))
        assert result.status == "success"


class TestReset:
    """Test the split reset_rows() + vacuum_sqlite() API."""

    def _do_reset(self, service: object, db_session: object, temp_db: object) -> object:
        """Helper: reset_rows() + commit + vacuum_sqlite() — the composed reset."""
        import os  # noqa: PLC0415

        from snore.services.schemas import ResetResult  # noqa: PLC0415

        db_path = str(temp_db)
        size_before = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )
        tables_cleared = service.reset_rows()
        total = sum(tables_cleared.values())
        db_session.commit()
        if DatabaseService.is_sqlite_target(db_path):
            service.vacuum_sqlite(db_path)
        size_after = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )
        return ResetResult(
            status="success",
            tables_cleared=tables_cleared,
            total_rows_deleted=total,
            size_before_mb=size_before,
            size_after_mb=size_after,
        )

    def test_empty_db_returns_zeros(self, db_session, temp_db):
        service = DatabaseService(db_session)
        result = self._do_reset(service, db_session, temp_db)
        assert result.status == "success"
        assert result.total_rows_deleted == 0
        assert all(v == 0 for v in result.tables_cleared.values())

    def test_includes_all_tables(self, db_session, temp_db):
        service = DatabaseService(db_session)
        result = self._do_reset(service, db_session, temp_db)
        from snore.database.models import Base

        assert set(result.tables_cleared.keys()) == set(Base.metadata.tables.keys())

    def test_deletes_data(self, db_session, test_device, temp_db):
        from datetime import datetime, timedelta

        from snore.database.models import Session as DbSession

        now = datetime.now()
        session = DbSession(
            device_id=test_device.id,
            device_session_id="reset_test",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.commit()

        service = DatabaseService(db_session)
        result = self._do_reset(service, db_session, temp_db)

        assert result.tables_cleared["sessions"] == 1
        assert result.tables_cleared["devices"] >= 1
        assert result.total_rows_deleted >= 2

    def test_tables_empty_after_reset(self, db_session, test_device, temp_db):
        from datetime import datetime, timedelta

        from snore.database.models import Session as DbSession

        now = datetime.now()
        session = DbSession(
            device_id=test_device.id,
            device_session_id="reset_test_2",
            start_time=now,
            end_time=now + timedelta(hours=8),
            duration_seconds=28800,
        )
        db_session.add(session)
        db_session.commit()

        service = DatabaseService(db_session)
        self._do_reset(service, db_session, temp_db)

        stats = service.get_stats(str(temp_db))
        assert stats.session_count == 0
        assert stats.device_count == 0

    def test_size_reported(self, db_session, temp_db):
        service = DatabaseService(db_session)
        result = self._do_reset(service, db_session, temp_db)
        assert result.size_before_mb >= 0
        assert result.size_after_mb >= 0
