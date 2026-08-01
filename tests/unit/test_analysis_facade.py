"""Unit tests for AnalysisFacade."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import AnalysisResult, Day, Device, Session
from snore.services.analysis_facade import AnalysisFacade


async def _create_session_with_analysis(
    db_session: AsyncSession, device: Device, day_date: date, num_analyses: int = 1
) -> tuple[Day, Session]:
    """Helper to create a session with analysis results."""
    day = Day(device_id=device.id, date=day_date, total_therapy_hours=8.0)
    db_session.add(day)
    await db_session.flush()
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"test_{day_date.isoformat()}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time()) + timedelta(hours=8),
        duration_seconds=28800,
    )
    db_session.add(sess)
    await db_session.flush()
    for i in range(num_analyses):
        ar = AnalysisResult(
            session_id=sess.id,
            timestamp_start=sess.start_time,
            timestamp_end=sess.end_time,
            programmatic_result_json={"version": i + 1},
            created_at=datetime.now(UTC) + timedelta(minutes=i),
        )
        db_session.add(ar)
    await db_session.flush()
    return day, sess


class TestListSessionsWithStatus:
    """Tests for list_sessions_with_status method."""

    async def test_list_empty(self, async_db_session):
        """Empty database returns empty list."""
        service = AnalysisFacade(async_db_session)
        results = await service.list_sessions_with_status()

        assert results == []

    async def test_list_with_analysis(self, async_db_session, async_test_device):
        """Sessions with and without analysis show correct has_analysis flags."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        day1, sess1 = await _create_session_with_analysis(async_db_session, async_test_device, yesterday)
        day2 = Day(device_id=async_test_device.id, date=today, total_therapy_hours=7.0)
        async_db_session.add(day2)
        await async_db_session.flush()

        sess2 = Session(
            device_id=async_test_device.id,
            day_id=day2.id,
            device_session_id="test_no_analysis",
            start_time=datetime.combine(today, datetime.min.time()),
            end_time=datetime.combine(today, datetime.min.time()) + timedelta(hours=7),
            duration_seconds=25200,
        )
        async_db_session.add(sess2)
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        results = await service.list_sessions_with_status()

        assert len(results) == 2
        assert results[0].session_id == sess2.id
        assert results[0].has_analysis is False
        assert results[0].analysis_id is None

        assert results[1].session_id == sess1.id
        assert results[1].has_analysis is True
        assert results[1].analysis_id is not None

    async def test_list_analyzed_only(self, async_db_session, async_test_device):
        """Only returns sessions with analysis when analyzed_only is True."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        await _create_session_with_analysis(async_db_session, async_test_device, yesterday)

        day2 = Day(device_id=async_test_device.id, date=today, total_therapy_hours=7.0)
        async_db_session.add(day2)
        await async_db_session.flush()

        sess2 = Session(
            device_id=async_test_device.id,
            day_id=day2.id,
            device_session_id="test_no_analysis",
            start_time=datetime.combine(today, datetime.min.time()),
            end_time=datetime.combine(today, datetime.min.time()) + timedelta(hours=7),
            duration_seconds=25200,
        )
        async_db_session.add(sess2)
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        results = await service.list_sessions_with_status(analyzed_only=True)

        assert len(results) == 1
        assert results[0].has_analysis is True

    async def test_list_date_filter(self, async_db_session, async_test_device):
        """Filter by start and end dates works correctly."""
        d1 = date(2024, 1, 1)
        d2 = date(2024, 1, 5)
        d3 = date(2024, 1, 10)

        await _create_session_with_analysis(async_db_session, async_test_device, d1)
        await _create_session_with_analysis(async_db_session, async_test_device, d2)
        await _create_session_with_analysis(async_db_session, async_test_device, d3)
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        results = await service.list_sessions_with_status(
            start=datetime(2024, 1, 2), end=datetime(2024, 1, 9)
        )

        assert len(results) == 1
        assert results[0].session_date == d2


class TestGetDeletePreview:
    """Tests for get_delete_preview method."""

    async def test_delete_preview_no_filters(self, async_db_session):
        """Raises ValueError when no filters are specified."""
        service = AnalysisFacade(async_db_session)

        with pytest.raises(ValueError) as exc:
            await service.get_delete_preview()

        assert "at least one filter" in str(exc.value).lower()

    async def test_delete_preview_with_data(self, async_db_session, async_test_device):
        """Preview shows correct counts for sessions with analysis."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        _, sess1 = await _create_session_with_analysis(
            async_db_session, async_test_device, yesterday, num_analyses=2
        )
        _, sess2 = await _create_session_with_analysis(async_db_session, async_test_device, today)
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        preview = await service.get_delete_preview(delete_all=True)

        assert preview.sessions_with_analysis == 2
        assert preview.total_analysis_records == 3
        assert preview.records_to_delete == 2
        assert len(preview.session_details) == 2

        detail1 = next(d for d in preview.session_details if d.id == sess1.id)
        assert detail1.version_count == 2

        detail2 = next(d for d in preview.session_details if d.id == sess2.id)
        assert detail2.version_count == 1

    async def test_delete_preview_latest_only(self, async_db_session, async_test_device):
        """Preview with all_versions=False only counts latest analysis."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        await _create_session_with_analysis(
            async_db_session, async_test_device, yesterday, num_analyses=3
        )
        await _create_session_with_analysis(async_db_session, async_test_device, today, num_analyses=2)
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        preview = await service.get_delete_preview(delete_all=True, all_versions=False)

        assert preview.sessions_with_analysis == 2
        assert preview.total_analysis_records == 5
        assert preview.records_to_delete == 2


class TestDeleteAnalysis:
    """Tests for delete_analysis method."""

    async def test_delete_all_versions(self, async_db_session, async_test_device):
        """Deletes all analysis records when all_versions=True."""
        from sqlalchemy import func, select as sa_select  # noqa: PLC0415

        today = date.today()
        _, sess = await _create_session_with_analysis(
            async_db_session, async_test_device, today, num_analyses=3
        )
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        deleted = await service.delete_analysis([sess.id], all_versions=True)

        assert deleted == 3

        remaining = (
            await async_db_session.execute(
                sa_select(func.count()).select_from(AnalysisResult).filter_by(session_id=sess.id)
            )
        ).scalar()
        assert remaining == 0

    async def test_delete_latest_only(self, async_db_session, async_test_device):
        """Only deletes most recent analysis per session when all_versions=False."""
        from sqlalchemy import func, select as sa_select  # noqa: PLC0415

        today = date.today()
        _, sess = await _create_session_with_analysis(
            async_db_session, async_test_device, today, num_analyses=3
        )
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        deleted = await service.delete_analysis([sess.id], all_versions=False)

        assert deleted == 1

        remaining = (
            await async_db_session.execute(
                sa_select(func.count()).select_from(AnalysisResult).filter_by(session_id=sess.id)
            )
        ).scalar()
        assert remaining == 2

        latest = (
            (
                await async_db_session.execute(
                    sa_select(AnalysisResult)
                    .filter_by(session_id=sess.id)
                    .order_by(AnalysisResult.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert latest.programmatic_result_json["version"] == 2

    async def test_delete_empty(self, async_db_session):
        """Returns 0 when no matching sessions exist."""
        service = AnalysisFacade(async_db_session)
        deleted = await service.delete_analysis([999], all_versions=False)

        assert deleted == 0

    async def test_delete_multiple_sessions(self, async_db_session, async_test_device):
        """Deletes analysis for multiple sessions."""
        from sqlalchemy import func, select as sa_select  # noqa: PLC0415

        today = date.today()
        yesterday = today - timedelta(days=1)

        _, sess1 = await _create_session_with_analysis(async_db_session, async_test_device, yesterday)
        _, sess2 = await _create_session_with_analysis(async_db_session, async_test_device, today)
        await async_db_session.commit()

        service = AnalysisFacade(async_db_session)
        deleted = await service.delete_analysis([sess1.id, sess2.id], all_versions=True)

        assert deleted == 2

        remaining = (
            await async_db_session.execute(
                sa_select(func.count()).select_from(AnalysisResult)
            )
        ).scalar()
        assert remaining == 0


class TestRunBatchAnalysis:
    async def test_no_sessions_returns_empty_result(self, async_db_session):
        facade = AnalysisFacade(async_db_session)
        result = await facade.run_batch_analysis(
            from_date=datetime(2099, 1, 1), to_date=datetime(2099, 12, 31)
        )
        assert result.total == 0
        assert result.successful == 0
        assert result.failed == 0
        assert result.results == []

    async def test_successful_analysis(self, async_db_session, async_test_device, monkeypatch):
        """Mock load/compute phases to succeed; verify successful count."""
        from unittest.mock import MagicMock

        day, sess = await _create_session_with_analysis(
            async_db_session, async_test_device, date(2025, 1, 10), num_analyses=0
        )
        await async_db_session.commit()

        @contextmanager
        def fake_scope():
            yield async_db_session

        mock_raw = MagicMock()
        mock_inputs = MagicMock()
        mock_result = MagicMock()

        monkeypatch.setattr("snore.database.session.session_scope", fake_scope)
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.load_session_inputs_raw",
            lambda *a, **kw: mock_raw,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.prepare_inputs",
            lambda *a, **kw: mock_inputs,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.compute_analysis",
            lambda *a, **kw: mock_result,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.store_result",
            lambda *a, **kw: None,
        )
        facade = AnalysisFacade(async_db_session)
        result = await facade.run_batch_analysis(
            from_date=datetime(2025, 1, 1), to_date=datetime(2025, 1, 31)
        )
        assert result.total == 1
        assert result.successful == 1
        assert result.failed == 0

    async def test_failed_analysis(self, async_db_session, async_test_device, monkeypatch):
        """Mock load phase to raise; verify failed count."""
        day, sess = await _create_session_with_analysis(
            async_db_session, async_test_device, date(2025, 1, 10), num_analyses=0
        )
        await async_db_session.commit()

        @contextmanager
        def fake_scope():
            yield async_db_session

        def raise_error(*a, **kw):
            raise RuntimeError("test error")

        monkeypatch.setattr("snore.database.session.session_scope", fake_scope)
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.load_session_inputs_raw",
            raise_error,
        )
        facade = AnalysisFacade(async_db_session)
        result = await facade.run_batch_analysis(
            from_date=datetime(2025, 1, 1), to_date=datetime(2025, 1, 31)
        )
        assert result.total == 1
        assert result.failed == 1
        assert result.results[0].success is False

    async def test_progress_callback_called(self, async_db_session, async_test_device, monkeypatch):
        """Progress callback fires once per session regardless of success/failure."""
        from unittest.mock import MagicMock

        day, sess = await _create_session_with_analysis(
            async_db_session, async_test_device, date(2025, 1, 10), num_analyses=0
        )
        await async_db_session.commit()

        @contextmanager
        def fake_scope():
            yield async_db_session

        mock_raw = MagicMock()
        mock_inputs = MagicMock()
        mock_result = MagicMock()

        monkeypatch.setattr("snore.database.session.session_scope", fake_scope)
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.load_session_inputs_raw",
            lambda *a, **kw: mock_raw,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.prepare_inputs",
            lambda *a, **kw: mock_inputs,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.compute_analysis",
            lambda *a, **kw: mock_result,
        )
        monkeypatch.setattr(
            "snore.analysis.service.AnalysisService.store_result",
            lambda *a, **kw: None,
        )
        calls = []
        facade = AnalysisFacade(async_db_session)
        await facade.run_batch_analysis(
            from_date=datetime(2025, 1, 1),
            to_date=datetime(2025, 1, 31),
            progress_callback=lambda completed, total: calls.append((completed, total)),
        )
        assert len(calls) == 1
        assert calls[0] == (1, None)  # total is None until exhausted (unknown)
