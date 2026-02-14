"""Unit tests for StatsService."""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session as SASession

from snore.database.models import Day, Device, Event, Session, Statistics
from snore.services.stats_service import StatsService


def _create_day_with_session(
    db_session: SASession,
    device: Device,
    day_date: date,
    duration_hours: float = 8.0,
    ahi: float = 2.5,
    **day_kwargs: Any,
) -> tuple[Day, Session]:
    """Helper to create a Day with associated Session."""
    day = Day(
        device_id=device.id,
        date=day_date,
        total_therapy_hours=duration_hours,
        ahi=ahi,
        **day_kwargs,
    )
    db_session.add(day)
    db_session.flush()
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"test_{day_date.isoformat()}",
        start_time=datetime.combine(day_date, datetime.min.time()),
        end_time=datetime.combine(day_date, datetime.min.time())
        + timedelta(hours=duration_hours),
        duration_seconds=duration_hours * 3600,
    )
    db_session.add(sess)
    db_session.flush()
    return day, sess


class TestStatsService:
    """Tests for StatsService methods."""

    def test_empty_database_returns_none(self, db_session):
        """Empty database returns None for get_summary."""
        service = StatsService(db_session)
        summary = service.get_summary()

        assert summary is None

    def test_summary_with_data(self, db_session, test_device):
        """get_summary computes basic metrics correctly."""
        today = date.today()
        day1, sess1 = _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
            ahi=2.5,
        )
        day2, sess2 = _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=1),
            duration_hours=7.0,
            ahi=3.0,
        )
        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary()

        assert summary is not None
        assert summary.days_with_data == 2
        assert summary.total_hours == 15.0
        assert summary.avg_hours == 7.5
        assert summary.avg_ahi == 2.75
        assert summary.effectiveness == "excellent"
        assert summary.first_date == day1.date
        assert summary.last_date == day2.date

    def test_summary_days_limit(self, db_session, test_device):
        """days_limit parameter filters Day records correctly."""
        today = date.today()
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=10),
            duration_hours=8.0,
            ahi=2.5,
        )
        day2, _ = _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=2),
            duration_hours=7.0,
            ahi=3.0,
        )
        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary(days_limit=5)

        assert summary is not None
        assert summary.days_with_data == 1
        assert summary.first_date == day2.date
        assert summary.last_date == day2.date

    def test_summary_event_counts(self, db_session, test_device):
        """Event counts are aggregated correctly."""
        today = date.today()
        day, sess = _create_day_with_session(
            db_session, test_device, today, duration_hours=8.0, ahi=2.5
        )

        for i in range(5):
            event = Event(
                session_id=sess.id,
                event_type="ObstructiveApnea",
                start_time=datetime.combine(today, datetime.min.time())
                + timedelta(minutes=i * 10),
                duration_seconds=10.0,
            )
            db_session.add(event)

        for i in range(3):
            event = Event(
                session_id=sess.id,
                event_type="Hypopnea",
                start_time=datetime.combine(today, datetime.min.time())
                + timedelta(minutes=i * 15),
                duration_seconds=12.0,
            )
            db_session.add(event)

        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary()

        assert summary is not None
        assert len(summary.event_counts) == 2

        oa_count = next(
            e for e in summary.event_counts if e.event_type == "ObstructiveApnea"
        )
        assert oa_count.count == 5
        assert oa_count.percentage == 62.5

        h_count = next(e for e in summary.event_counts if e.event_type == "Hypopnea")
        assert h_count.count == 3
        assert h_count.percentage == 37.5

    def test_weighted_stats(self, db_session, test_device):
        """Weighted averages computed correctly based on usage_hours."""
        today = date.today()
        day1, sess1 = _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=1),
            duration_hours=8.0,
            ahi=2.5,
        )
        day2, sess2 = _create_day_with_session(
            db_session, test_device, today, duration_hours=4.0, ahi=3.0
        )

        stats1 = Statistics(
            session_id=sess1.id,
            usage_hours=8.0,
            pulse_mean=70.0,
            rei=1.5,
            epap_mean=5.0,
        )
        stats2 = Statistics(
            session_id=sess2.id,
            usage_hours=4.0,
            pulse_mean=80.0,
            rei=2.0,
            epap_mean=6.0,
        )
        db_session.add(stats1)
        db_session.add(stats2)
        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary()

        assert summary is not None
        assert summary.avg_pulse is not None
        assert abs(summary.avg_pulse - 73.33) < 0.1
        assert summary.avg_rei is not None
        assert abs(summary.avg_rei - 1.67) < 0.1
        assert summary.avg_epap is not None
        assert abs(summary.avg_epap - 5.33) < 0.1

    def test_period_statistics(self, db_session, test_device):
        """get_period_statistics returns correct list of PeriodStatistics."""
        today = date.today()
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=10),
            duration_hours=8.0,
            ahi=2.5,
        )
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=3),
            duration_hours=7.0,
            ahi=3.0,
        )
        db_session.commit()

        service = StatsService(db_session)
        period_stats = service.get_period_statistics("week")

        assert len(period_stats) > 0
        for stat in period_stats:
            assert stat.period_type == "week"
            assert stat.days_used >= 0

    def test_get_records(self, db_session, test_device):
        """get_records returns best/worst days for metrics."""
        today = date.today()
        for i in range(7):
            _create_day_with_session(
                db_session,
                test_device,
                today - timedelta(days=i),
                duration_hours=7.0 + i * 0.5,
                ahi=2.0 + i * 0.5,
                leak_median=10.0 + i,
                spo2_min=92.0 - i,
            )
        db_session.commit()

        service = StatsService(db_session)
        records = service.get_records(top_n=5)

        assert "ahi" in records
        assert "leak" in records
        assert "therapy_hours" in records
        assert "spo2_min" in records

        assert len(records["ahi"]["best"]) <= 5
        assert len(records["ahi"]["worst"]) <= 5

        best_ahi = records["ahi"]["best"][0]
        assert best_ahi[1] == 2.0

        worst_ahi = records["ahi"]["worst"][0]
        assert worst_ahi[1] == 5.0

    def test_pressure_aggregates(self, db_session, test_device):
        """Pressure min/max/avg computed correctly from Day.pressure_median."""
        today = date.today()
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
            ahi=2.5,
            pressure_median=10.0,
        )
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=1),
            duration_hours=7.0,
            ahi=3.0,
            pressure_median=12.0,
        )
        _create_day_with_session(
            db_session,
            test_device,
            today,
            duration_hours=7.5,
            ahi=2.0,
            pressure_median=9.0,
        )
        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary()

        assert summary is not None
        assert summary.avg_pressure is not None
        assert abs(summary.avg_pressure - 10.33) < 0.1
        assert summary.min_pressure == 9.0
        assert summary.max_pressure == 12.0

    def test_spo2_aggregates(self, db_session, test_device):
        """SpO2 min/avg computed correctly from Day records."""
        today = date.today()
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
            ahi=2.5,
            spo2_mean=96.0,
            spo2_min=90,
        )
        _create_day_with_session(
            db_session,
            test_device,
            today - timedelta(days=1),
            duration_hours=7.0,
            ahi=3.0,
            spo2_mean=95.0,
            spo2_min=88,
        )
        db_session.commit()

        service = StatsService(db_session)
        summary = service.get_summary()

        assert summary is not None
        assert summary.avg_spo2 is not None
        assert abs(summary.avg_spo2 - 95.5) < 0.1
        assert summary.min_spo2 == 88
