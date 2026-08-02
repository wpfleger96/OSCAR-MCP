"""Unit tests for StatsService."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import Day, Device, Event, Session, Statistics
from snore.services.stats_service import StatsService


async def _create_day_with_session(
    db_session: AsyncSession,
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
    await db_session.flush()
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
    await db_session.flush()
    return day, sess


class TestStatsService:
    """Tests for StatsService methods."""

    async def test_empty_database_returns_none(self, async_db_session):
        """Empty database returns None for get_summary."""
        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary()

        assert summary is None

    async def test_summary_with_data(self, async_db_session, async_test_device):
        """get_summary computes basic metrics correctly."""
        today = date.today()
        day1, sess1 = await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
            ahi=2.5,
        )
        day2, sess2 = await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=1),
            duration_hours=7.0,
            ahi=3.0,
        )

        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary()

        assert summary is not None
        assert summary.days_with_data == 2
        assert summary.total_hours == 15.0
        assert summary.avg_hours == 7.5
        assert summary.avg_ahi == 2.75
        assert summary.effectiveness == "excellent"
        assert summary.first_date == day1.date
        assert summary.last_date == day2.date

    async def test_summary_days_limit(self, async_db_session, async_test_device):
        """days_limit parameter filters Day records correctly."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=10),
            duration_hours=8.0,
            ahi=2.5,
        )
        day2, _ = await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=2),
            duration_hours=7.0,
            ahi=3.0,
        )

        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary(days_limit=5)

        assert summary is not None
        assert summary.days_with_data == 1
        assert summary.first_date == day2.date
        assert summary.last_date == day2.date

    async def test_summary_event_counts(self, async_db_session, async_test_device):
        """Event counts are aggregated correctly."""
        today = date.today()
        day, sess = await _create_day_with_session(
            async_db_session, async_test_device, today, duration_hours=8.0, ahi=2.5
        )

        for i in range(5):
            event = Event(
                session_id=sess.id,
                event_type="ObstructiveApnea",
                start_time=datetime.combine(today, datetime.min.time())
                + timedelta(minutes=i * 10),
                duration_seconds=10.0,
            )
            async_db_session.add(event)

        for i in range(3):
            event = Event(
                session_id=sess.id,
                event_type="Hypopnea",
                start_time=datetime.combine(today, datetime.min.time())
                + timedelta(minutes=i * 15),
                duration_seconds=12.0,
            )
            async_db_session.add(event)

        await async_db_session.flush()

        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary()

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

    async def test_weighted_stats(self, async_db_session, async_test_device):
        """Weighted averages computed correctly based on usage_hours."""
        today = date.today()
        day1, sess1 = await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=1),
            duration_hours=8.0,
            ahi=2.5,
        )
        day2, sess2 = await _create_day_with_session(
            async_db_session, async_test_device, today, duration_hours=4.0, ahi=3.0
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
        async_db_session.add(stats1)
        async_db_session.add(stats2)
        await async_db_session.flush()

        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary()

        assert summary is not None
        assert summary.avg_pulse is not None
        assert abs(summary.avg_pulse - 73.33) < 0.1
        assert summary.avg_rei is not None
        assert abs(summary.avg_rei - 1.67) < 0.1
        assert summary.avg_epap is not None
        assert abs(summary.avg_epap - 5.33) < 0.1

    async def test_period_statistics(self, async_db_session, async_test_device):
        """get_period_statistics returns correct list of PeriodStatistics."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=10),
            duration_hours=8.0,
            ahi=2.5,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=3),
            duration_hours=7.0,
            ahi=3.0,
        )

        service = StatsService(async_db_session, profile_id=1)
        period_stats = await service.get_period_statistics("week")

        assert len(period_stats) > 0
        for stat in period_stats:
            assert stat.period_type == "week"
            assert stat.days_used >= 0

    async def test_get_records(self, async_db_session, async_test_device):
        """get_records returns best/worst days for metrics."""
        today = date.today()
        for i in range(7):
            await _create_day_with_session(
                async_db_session,
                async_test_device,
                today - timedelta(days=i),
                duration_hours=7.0 + i * 0.5,
                ahi=2.0 + i * 0.5,
                leak_median=10.0 + i,
                spo2_min=92.0 - i,
            )

        service = StatsService(async_db_session, profile_id=1)
        records = await service.get_records(top_n=5)

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

    async def test_pressure_aggregates(self, async_db_session, async_test_device):
        """Pressure min/max/avg computed correctly from Day.pressure_median."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
            ahi=2.5,
            pressure_median=10.0,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=1),
            duration_hours=7.0,
            ahi=3.0,
            pressure_median=12.0,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today,
            duration_hours=7.5,
            ahi=2.0,
            pressure_median=9.0,
        )

        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary()

        assert summary is not None
        assert summary.avg_pressure is not None
        assert abs(summary.avg_pressure - 10.33) < 0.1
        assert summary.min_pressure == 9.0
        assert summary.max_pressure == 12.0

    async def test_spo2_aggregates(self, async_db_session, async_test_device):
        """SpO2 min/avg computed correctly from Day records."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
            ahi=2.5,
            spo2_mean=96.0,
            spo2_min=90,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=1),
            duration_hours=7.0,
            ahi=3.0,
            spo2_mean=95.0,
            spo2_min=88,
        )

        service = StatsService(async_db_session, profile_id=1)
        summary = await service.get_summary()

        assert summary is not None
        assert summary.avg_spo2 is not None
        assert abs(summary.avg_spo2 - 95.5) < 0.1
        assert summary.min_spo2 == 88


class TestQueryDaysFiltering:
    """Test _query_days with from_date and to_date parameters."""

    async def test_from_date_excludes_earlier_days(
        self, async_db_session, async_test_device
    ):
        """from_date filters out Day records before the cutoff."""
        await _create_day_with_session(
            async_db_session, async_test_device, date(2024, 1, 1), duration_hours=8.0
        )
        await _create_day_with_session(
            async_db_session, async_test_device, date(2024, 6, 1), duration_hours=8.0
        )

        service = StatsService(async_db_session, profile_id=1)
        result = await service._query_days(from_date=date(2024, 3, 1))

        assert len(result) == 1
        assert result[0].date == date(2024, 6, 1)

    async def test_to_date_excludes_later_days(
        self, async_db_session, async_test_device
    ):
        """to_date filters out Day records after the cutoff."""
        await _create_day_with_session(
            async_db_session, async_test_device, date(2024, 1, 1), duration_hours=8.0
        )
        await _create_day_with_session(
            async_db_session, async_test_device, date(2024, 6, 1), duration_hours=8.0
        )

        service = StatsService(async_db_session, profile_id=1)
        result = await service._query_days(to_date=date(2024, 3, 1))

        assert len(result) == 1
        assert result[0].date == date(2024, 1, 1)

    async def test_from_date_and_to_date_combined(
        self, async_db_session, async_test_device
    ):
        """Both from_date and to_date can be applied simultaneously."""
        for month in [1, 4, 7, 10]:
            await _create_day_with_session(
                async_db_session,
                async_test_device,
                date(2024, month, 15),
                duration_hours=8.0,
            )

        service = StatsService(async_db_session, profile_id=1)
        result = await service._query_days(
            from_date=date(2024, 3, 1), to_date=date(2024, 8, 1)
        )

        result_dates = {d.date for d in result}
        assert result_dates == {date(2024, 4, 15), date(2024, 7, 15)}

    async def test_days_limit_and_from_date_stack(
        self, async_db_session, async_test_device
    ):
        """days_limit and from_date both apply as AND conditions."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=100),
            duration_hours=8.0,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=5),
            duration_hours=8.0,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
        )

        service = StatsService(async_db_session, profile_id=1)
        # days_limit=30 excludes the oldest day; from_date also excludes days before 4 days ago
        result = await service._query_days(
            days_limit=30, from_date=today - timedelta(days=4)
        )

        assert len(result) == 1
        assert result[0].date == today - timedelta(days=2)


class TestAggregateSessionStatsPerPeriod:
    """Test _aggregate_session_stats_per_period with hand-computed weighted means."""

    async def test_weighted_means_per_period(self, async_db_session, async_test_device):
        """Usage-weighted epap/rr/pulse/mv are computed correctly per period bucket."""
        # Jan: two sessions with different usage_hours
        jan_day, jan_sess1 = await _create_day_with_session(
            async_db_session, async_test_device, date(2024, 1, 10), duration_hours=8.0
        )
        jan_day2 = Day(
            device_id=async_test_device.id,
            date=date(2024, 1, 20),
            total_therapy_hours=4.0,
        )
        async_db_session.add(jan_day2)
        await async_db_session.flush()
        jan_sess2 = Session(
            device_id=async_test_device.id,
            day_id=jan_day2.id,
            device_session_id="test_jan20",
            start_time=datetime(2024, 1, 20, 22, 0),
            end_time=datetime(2024, 1, 21, 2, 0),
            duration_seconds=4 * 3600,
        )
        async_db_session.add(jan_sess2)
        await async_db_session.flush()

        stats1 = Statistics(
            session_id=jan_sess1.id,
            usage_hours=8.0,
            epap_mean=5.0,
            respiratory_rate_mean=14.0,
            pulse_mean=60.0,
            minute_ventilation_mean=7.0,
        )
        stats2 = Statistics(
            session_id=jan_sess2.id,
            usage_hours=4.0,
            epap_mean=7.0,
            respiratory_rate_mean=16.0,
            pulse_mean=72.0,
            minute_ventilation_mean=9.0,
        )
        async_db_session.add(stats1)
        async_db_session.add(stats2)
        await async_db_session.flush()

        service = StatsService(async_db_session, profile_id=1)
        day_records = await service._query_days()
        period_stats = await service.get_period_statistics("month")

        extras = await service._aggregate_session_stats_per_period(
            day_records, period_stats
        )

        jan_start = date(2024, 1, 1)
        assert jan_start in extras

        # hand-computed: weighted mean = (5.0*8 + 7.0*4) / (8+4) = (40+28)/12 = 68/12 ≈ 5.667
        assert extras[jan_start]["epap"] == pytest.approx(68.0 / 12.0, abs=0.01)
        # rr: (14*8 + 16*4) / 12 = (112 + 64)/12 = 176/12 ≈ 14.667
        assert extras[jan_start]["rr"] == pytest.approx(176.0 / 12.0, abs=0.01)
        # pulse: (60*8 + 72*4) / 12 = (480 + 288)/12 = 768/12 = 64.0
        assert extras[jan_start]["pulse"] == pytest.approx(64.0, abs=0.01)

    async def test_missing_usage_hours_skipped(
        self, async_db_session, async_test_device
    ):
        """Statistics rows with None or 0 usage_hours are excluded from weighting."""
        day, sess = await _create_day_with_session(
            async_db_session, async_test_device, date(2024, 2, 10), duration_hours=8.0
        )
        # usage_hours = 0: should be excluded
        stats = Statistics(
            session_id=sess.id,
            usage_hours=0.0,
            epap_mean=99.0,
        )
        async_db_session.add(stats)
        await async_db_session.flush()

        service = StatsService(async_db_session, profile_id=1)
        day_records = await service._query_days()
        period_stats = await service.get_period_statistics("month")

        extras = await service._aggregate_session_stats_per_period(
            day_records, period_stats
        )

        feb_start = date(2024, 2, 1)
        assert extras[feb_start]["epap"] is None

    async def test_empty_day_records_returns_none_dict(self, async_db_session):
        """Empty day_records yields None values for all periods."""
        from snore.services.schemas import PeriodStatistics

        ps = PeriodStatistics(
            period_type="month",
            period_start=date(2024, 3, 1),
            period_end=date(2024, 3, 31),
        )
        service = StatsService(async_db_session, profile_id=1)
        extras = await service._aggregate_session_stats_per_period([], [ps])

        assert extras[date(2024, 3, 1)] == {
            "epap": None,
            "rr": None,
            "pulse": None,
            "mv": None,
        }


class TestGetTrends:
    """Test StatsService.get_trends with the new (period_type, days_limit) signature."""

    async def test_get_trends_returns_13_keys(
        self, async_db_session, async_test_device
    ):
        """get_trends returns all 13 metric keys."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=3),
            duration_hours=8.0,
            ahi=2.5,
        )

        service = StatsService(async_db_session, profile_id=1)
        result = await service.get_trends("week")

        expected = {
            "ahi",
            "usage",
            "spo2",
            "leak",
            "pressure",
            "oai",
            "cai",
            "hi",
            "rera",
            "epap",
            "rr",
            "pulse",
            "mv",
        }
        assert set(result.keys()) == expected

    async def test_get_trends_day_granularity(
        self, async_db_session, async_test_device
    ):
        """get_trends with period_type='day' produces one entry per therapy day."""
        base = date.today() - timedelta(days=5)
        for i in range(3):
            await _create_day_with_session(
                async_db_session,
                async_test_device,
                base + timedelta(days=i),
                duration_hours=7.0,
                ahi=float(i + 1),
            )

        service = StatsService(async_db_session, profile_id=1)
        result = await service.get_trends("day")

        assert len(result["ahi"]) == 3

    async def test_get_trends_days_limit_filters(
        self, async_db_session, async_test_device
    ):
        """days_limit parameter passed to get_trends restricts the Day records used."""
        today = date.today()
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=60),
            duration_hours=8.0,
        )
        await _create_day_with_session(
            async_db_session,
            async_test_device,
            today - timedelta(days=2),
            duration_hours=8.0,
        )

        service = StatsService(async_db_session, profile_id=1)
        result = await service.get_trends("month", days_limit=30)

        # Only the recent day should appear
        assert len(result["ahi"]) == 1

    async def test_get_trends_empty_returns_13_empty_lists(self, async_db_session):
        """Empty database returns 13-key dict with empty lists."""
        service = StatsService(async_db_session, profile_id=1)
        result = await service.get_trends("week")

        assert len(result) == 13
        for v in result.values():
            assert v == []
