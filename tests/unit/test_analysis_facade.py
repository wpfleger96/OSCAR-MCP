"""Unit tests for AnalysisFacade."""

from datetime import date, datetime, timedelta

import pytest

from sqlalchemy.orm import Session as SQLSession

from snore.database.models import AnalysisResult, Day, Device, Session
from snore.services.analysis_facade import AnalysisFacade


def _create_session_with_analysis(
    db_session: SQLSession, device: Device, day_date: date, num_analyses: int = 1
) -> tuple[Day, Session]:
    """Helper to create a session with analysis results."""
    day = Day(device_id=device.id, date=day_date, total_therapy_hours=8.0)
    db_session.add(day)
    db_session.flush()
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"test_{day_date.isoformat()}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time()) + timedelta(hours=8),
        duration_seconds=28800,
    )
    db_session.add(sess)
    db_session.flush()
    for i in range(num_analyses):
        ar = AnalysisResult(
            session_id=sess.id,
            timestamp_start=sess.start_time,
            timestamp_end=sess.end_time,
            programmatic_result_json={"version": i + 1},
            created_at=datetime.now() + timedelta(minutes=i),
        )
        db_session.add(ar)
    db_session.flush()
    return day, sess


class TestListSessionsWithStatus:
    """Tests for list_sessions_with_status method."""

    def test_list_empty(self, db_session):
        """Empty database returns empty list."""
        service = AnalysisFacade(db_session)
        results = service.list_sessions_with_status()

        assert results == []

    def test_list_with_analysis(self, db_session, test_device):
        """Sessions with and without analysis show correct has_analysis flags."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        day1, sess1 = _create_session_with_analysis(db_session, test_device, yesterday)
        day2 = Day(device_id=test_device.id, date=today, total_therapy_hours=7.0)
        db_session.add(day2)
        db_session.flush()

        sess2 = Session(
            device_id=test_device.id,
            day_id=day2.id,
            device_session_id="test_no_analysis",
            start_time=datetime.combine(today, datetime.min.time()),
            end_time=datetime.combine(today, datetime.min.time()) + timedelta(hours=7),
            duration_seconds=25200,
        )
        db_session.add(sess2)
        db_session.commit()

        service = AnalysisFacade(db_session)
        results = service.list_sessions_with_status()

        assert len(results) == 2
        assert results[0].session_id == sess2.id
        assert results[0].has_analysis is False
        assert results[0].analysis_id is None

        assert results[1].session_id == sess1.id
        assert results[1].has_analysis is True
        assert results[1].analysis_id is not None

    def test_list_analyzed_only(self, db_session, test_device):
        """Only returns sessions with analysis when analyzed_only is True."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        _create_session_with_analysis(db_session, test_device, yesterday)

        day2 = Day(device_id=test_device.id, date=today, total_therapy_hours=7.0)
        db_session.add(day2)
        db_session.flush()

        sess2 = Session(
            device_id=test_device.id,
            day_id=day2.id,
            device_session_id="test_no_analysis",
            start_time=datetime.combine(today, datetime.min.time()),
            end_time=datetime.combine(today, datetime.min.time()) + timedelta(hours=7),
            duration_seconds=25200,
        )
        db_session.add(sess2)
        db_session.commit()

        service = AnalysisFacade(db_session)
        results = service.list_sessions_with_status(analyzed_only=True)

        assert len(results) == 1
        assert results[0].has_analysis is True

    def test_list_date_filter(self, db_session, test_device):
        """Filter by start and end dates works correctly."""
        d1 = date(2024, 1, 1)
        d2 = date(2024, 1, 5)
        d3 = date(2024, 1, 10)

        _create_session_with_analysis(db_session, test_device, d1)
        _create_session_with_analysis(db_session, test_device, d2)
        _create_session_with_analysis(db_session, test_device, d3)
        db_session.commit()

        service = AnalysisFacade(db_session)
        results = service.list_sessions_with_status(
            start=datetime(2024, 1, 2), end=datetime(2024, 1, 9)
        )

        assert len(results) == 1
        assert results[0].session_date == d2


class TestGetDeletePreview:
    """Tests for get_delete_preview method."""

    def test_delete_preview_no_filters(self, db_session):
        """Raises ValueError when no filters are specified."""
        service = AnalysisFacade(db_session)

        with pytest.raises(ValueError) as exc:
            service.get_delete_preview()

        assert "at least one filter" in str(exc.value).lower()

    def test_delete_preview_with_data(self, db_session, test_device):
        """Preview shows correct counts for sessions with analysis."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        _, sess1 = _create_session_with_analysis(
            db_session, test_device, yesterday, num_analyses=2
        )
        _, sess2 = _create_session_with_analysis(db_session, test_device, today)
        db_session.commit()

        service = AnalysisFacade(db_session)
        preview = service.get_delete_preview(delete_all=True)

        assert preview.sessions_with_analysis == 2
        assert preview.total_analysis_records == 3
        assert preview.records_to_delete == 2
        assert len(preview.session_details) == 2

        detail1 = next(d for d in preview.session_details if d.id == sess1.id)
        assert detail1.version_count == 2

        detail2 = next(d for d in preview.session_details if d.id == sess2.id)
        assert detail2.version_count == 1

    def test_delete_preview_latest_only(self, db_session, test_device):
        """Preview with all_versions=False only counts latest analysis."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        _create_session_with_analysis(
            db_session, test_device, yesterday, num_analyses=3
        )
        _create_session_with_analysis(db_session, test_device, today, num_analyses=2)
        db_session.commit()

        service = AnalysisFacade(db_session)
        preview = service.get_delete_preview(delete_all=True, all_versions=False)

        assert preview.sessions_with_analysis == 2
        assert preview.total_analysis_records == 5
        assert preview.records_to_delete == 2


class TestDeleteAnalysis:
    """Tests for delete_analysis method."""

    def test_delete_all_versions(self, db_session, test_device):
        """Deletes all analysis records when all_versions=True."""
        today = date.today()
        _, sess = _create_session_with_analysis(
            db_session, test_device, today, num_analyses=3
        )
        db_session.commit()

        service = AnalysisFacade(db_session)
        deleted = service.delete_analysis([sess.id], all_versions=True)

        assert deleted == 3

        remaining = (
            db_session.query(AnalysisResult).filter_by(session_id=sess.id).count()
        )
        assert remaining == 0

    def test_delete_latest_only(self, db_session, test_device):
        """Only deletes most recent analysis per session when all_versions=False."""
        today = date.today()
        _, sess = _create_session_with_analysis(
            db_session, test_device, today, num_analyses=3
        )
        db_session.commit()

        service = AnalysisFacade(db_session)
        deleted = service.delete_analysis([sess.id], all_versions=False)

        assert deleted == 1

        remaining = (
            db_session.query(AnalysisResult).filter_by(session_id=sess.id).count()
        )
        assert remaining == 2

        latest = (
            db_session.query(AnalysisResult)
            .filter_by(session_id=sess.id)
            .order_by(AnalysisResult.created_at.desc())
            .first()
        )
        assert latest.programmatic_result_json["version"] == 2

    def test_delete_empty(self, db_session):
        """Returns 0 when no matching sessions exist."""
        service = AnalysisFacade(db_session)
        deleted = service.delete_analysis([999], all_versions=False)

        assert deleted == 0

    def test_delete_multiple_sessions(self, db_session, test_device):
        """Deletes analysis for multiple sessions."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        _, sess1 = _create_session_with_analysis(db_session, test_device, yesterday)
        _, sess2 = _create_session_with_analysis(db_session, test_device, today)
        db_session.commit()

        service = AnalysisFacade(db_session)
        deleted = service.delete_analysis([sess1.id, sess2.id], all_versions=True)

        assert deleted == 2

        remaining = db_session.query(AnalysisResult).count()
        assert remaining == 0
