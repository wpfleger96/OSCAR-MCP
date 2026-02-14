"""Unit tests for DatabaseService."""

from datetime import datetime, timedelta

from snore.database.models import Session, Statistics
from snore.services.database_service import DatabaseService


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
