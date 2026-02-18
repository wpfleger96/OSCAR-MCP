"""Unit tests for RxService."""

from datetime import date, datetime, timedelta

import pytest

from sqlalchemy.orm import Session as DbSession

from snore.database.models import Day, Device, Session, Setting
from snore.services.rx_service import RxService


def _create_day_with_session(
    db_session: DbSession,
    device: Device,
    day_date: date,
    ahi: float | None = None,
    total_therapy_hours: float = 8.0,
    settings: dict | None = None,
) -> Day:
    """Helper to create a Day with a linked Session and optional RX settings."""
    day = Day(
        device_id=device.id,
        date=day_date,
        session_count=1,
        total_therapy_hours=total_therapy_hours,
        ahi=ahi,
        leak_median=5.0,
    )
    db_session.add(day)
    db_session.flush()

    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"rx_test_{day_date.isoformat()}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time())
        + timedelta(hours=total_therapy_hours),
        duration_seconds=total_therapy_hours * 3600,
    )
    db_session.add(sess)
    db_session.flush()

    if settings:
        for key, value in settings.items():
            db_session.add(Setting(session_id=sess.id, key=key, value=value))
        db_session.flush()

    return day


RX_SETTINGS = {
    "mode": "APAP",
    "pressure_min": "4.0",
    "pressure_max": "20.0",
}


class TestRxServiceHistory:
    def test_history_empty_db(self, db_session):
        """Empty database returns empty list."""
        service = RxService(db_session)
        result = service.get_history()
        assert result == []

    def test_history_single_period(self, db_session, test_device):
        """Single cohesive RX period is returned as one entry."""
        base = date(2025, 1, 1)
        for i in range(10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=3.0 + i * 0.1,
                settings=RX_SETTINGS,
            )
        db_session.flush()

        service = RxService(db_session)
        result = service.get_history()

        assert len(result) == 1
        assert result[0].days_count == 10
        assert result[0].settings == RX_SETTINGS
        assert result[0].start_date == base
        assert result[0].end_date == base + timedelta(days=9)

    def test_history_two_periods_different_settings(self, db_session, test_device):
        """Settings change creates two distinct periods."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "6.0", "pressure_max": "20.0"}

        for i in range(5):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=5.0,
                settings=settings_a,
            )
        for i in range(5, 10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=3.0,
                settings=settings_b,
            )
        db_session.flush()

        service = RxService(db_session)
        result = service.get_history()

        assert len(result) == 2
        assert result[0].days_count == 5
        assert result[1].days_count == 5

    def test_history_stats_computed(self, db_session, test_device):
        """Stats fields are populated for periods with data."""
        base = date(2025, 3, 1)
        for i in range(7):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=4.0,
                total_therapy_hours=7.5,
                settings=RX_SETTINGS,
            )
        db_session.flush()

        service = RxService(db_session)
        result = service.get_history()

        assert len(result) == 1
        assert result[0].avg_ahi == pytest.approx(4.0)
        assert result[0].median_ahi == pytest.approx(4.0)
        assert result[0].avg_hours == pytest.approx(7.5)
        assert result[0].total_hours == pytest.approx(7.5 * 7)


class TestRxServiceCurrent:
    def test_current_empty_db(self, db_session):
        """Empty database returns None."""
        service = RxService(db_session)
        result = service.get_current()
        assert result is None

    def test_current_returns_last_period(self, db_session, test_device):
        """Returns the most recent RX period when multiple exist."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "6.0", "pressure_max": "20.0"}

        for i in range(5):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=settings_a,
            )
        for i in range(5, 10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                settings=settings_b,
            )
        db_session.flush()

        service = RxService(db_session)
        result = service.get_current()

        assert result is not None
        assert result.settings == settings_b
        assert result.end_date == base + timedelta(days=9)


class TestRxServiceComparison:
    def test_comparison_empty_db(self, db_session):
        """Empty database returns empty comparison."""
        service = RxService(db_session)
        result = service.get_comparison()
        assert result.periods == []
        assert result.best_index is None
        assert result.worst_index is None

    def test_comparison_insufficient_days(self, db_session, test_device):
        """Periods with fewer days than min_days are excluded from best/worst."""
        base = date(2025, 1, 1)
        for i in range(3):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=2.0,
                settings=RX_SETTINGS,
            )
        db_session.flush()

        service = RxService(db_session)
        result = service.get_comparison(min_days=7)

        assert len(result.periods) == 1
        # Period has fewer days than min_days threshold, so no best/worst
        assert result.best_index is None
        assert result.worst_index is None

    def test_comparison_best_worst_identified(self, db_session, test_device):
        """Best (lowest AHI) and worst (highest AHI) periods identified correctly."""
        base = date(2025, 1, 1)
        settings_a = {"mode": "CPAP", "pressure_fixed": "8.0"}
        settings_b = {"mode": "APAP", "pressure_min": "5.0", "pressure_max": "20.0"}
        settings_c = {"mode": "APAP", "pressure_min": "8.0", "pressure_max": "20.0"}

        for i in range(10):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=5.0,
                settings=settings_a,
            )
        for i in range(10, 20):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=1.5,
                settings=settings_b,
            )
        for i in range(20, 30):
            _create_day_with_session(
                db_session,
                test_device,
                base + timedelta(days=i),
                ahi=8.0,
                settings=settings_c,
            )
        db_session.flush()

        service = RxService(db_session)
        result = service.get_comparison(min_days=7)

        assert len(result.periods) == 3
        assert result.best_index == 1
        assert result.worst_index == 2
