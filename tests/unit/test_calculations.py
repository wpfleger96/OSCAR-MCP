"""
Tests for statistical calculations module.

Tests the calculation functions for AHI statistics, period-based grouping,
and trend analysis used in the stats CLI command.
"""

from datetime import date, datetime, timedelta

import pytest

from snore.analysis.calculations import (
    calculate_median_ahi,
    calculate_period_statistics,
    calculate_trends,
)
from snore.database.day_manager import DayManager


class TestCalculateMedianAhi:
    """Test median AHI calculation."""

    def test_median_odd_count(self, db_session, test_device, test_session_factory):
        """Median with odd number of days returns middle value."""
        device = test_device

        days = []
        for i, ahi in enumerate([2.0, 5.0, 8.0]):
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(2024, 11, i + 1, 12, 0, 0),
                duration_hours=8.0,
                ahi=ahi,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_median_ahi(days)

        assert result == pytest.approx(5.0, abs=0.01)

    def test_median_even_count(self, db_session, test_device, test_session_factory):
        """Median with even number of days returns average of two middle values."""
        device = test_device

        days = []
        for i, ahi in enumerate([2.0, 4.0, 6.0, 8.0]):
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(2024, 11, i + 1, 12, 0, 0),
                duration_hours=8.0,
                ahi=ahi,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_median_ahi(days)

        assert result == pytest.approx(5.0, abs=0.01)

    def test_median_empty(self):
        """Median of empty list returns None."""
        result = calculate_median_ahi([])

        assert result is None

    def test_median_single(self, db_session, test_device, test_session_factory):
        """Median with single day returns that day's AHI."""
        device = test_device

        session = test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 1, 12, 0, 0),
            duration_hours=8.0,
            ahi=7.5,
        )
        day = DayManager.link_session_to_day(session, device.id, db_session)

        result = calculate_median_ahi([day])

        assert result == pytest.approx(7.5, abs=0.01)

    def test_median_all_none(self, db_session, test_device, test_session_factory):
        """Median when all days have no AHI returns None."""
        device = test_device

        days = []
        for i in range(3):
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(2024, 11, i + 1, 12, 0, 0),
                duration_hours=8.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_median_ahi(days)

        assert result is None


class TestCalculatePeriodStatistics:
    """Test period-based statistics grouping."""

    def test_monthly_buckets(self, db_session, test_device, test_session_factory):
        """Monthly bucketing creates correct number of periods."""
        device = test_device
        start_date = date(2024, 10, 1)

        days = []
        for i in range(60):
            session_date = start_date + timedelta(days=i)
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(
                    session_date.year, session_date.month, session_date.day, 12, 0, 0
                ),
                duration_hours=8.0,
                ahi=5.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_period_statistics(days, "month")

        assert len(result) == 2
        assert result[0].period_start == date(2024, 10, 1)
        assert result[0].period_end == date(2024, 10, 31)
        assert result[1].period_start == date(2024, 11, 1)
        assert result[1].period_end == date(2024, 11, 30)

    def test_weekly_buckets(self, db_session, test_device, test_session_factory):
        """Weekly bucketing aligns to week boundaries."""
        device = test_device
        start_date = date(2024, 11, 4)

        days = []
        for i in range(14):
            session_date = start_date + timedelta(days=i)
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(
                    session_date.year, session_date.month, session_date.day, 12, 0, 0
                ),
                duration_hours=8.0,
                ahi=5.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_period_statistics(days, "week")

        assert len(result) >= 2

    def test_yearly_buckets(self, db_session, test_device, test_session_factory):
        """Yearly bucketing spans multiple years."""
        device = test_device

        days = []
        for year in [2024, 2025]:
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(year, 6, 15, 12, 0, 0),
                duration_hours=8.0,
                ahi=5.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_period_statistics(days, "year")

        assert len(result) == 2
        assert result[0].period_start == date(2024, 1, 1)
        assert result[0].period_end == date(2024, 12, 31)
        assert result[1].period_start == date(2025, 1, 1)
        assert result[1].period_end == date(2025, 12, 31)

    def test_6month_buckets(self, db_session, test_device, test_session_factory):
        """6-month bucketing creates half-year periods."""
        device = test_device

        days = []
        for month in [2, 8]:
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(2024, month, 15, 12, 0, 0),
                duration_hours=8.0,
                ahi=5.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_period_statistics(days, "6month")

        assert len(result) == 2
        assert result[0].period_start == date(2024, 1, 1)
        assert result[0].period_end == date(2024, 6, 30)
        assert result[1].period_start == date(2024, 7, 1)
        assert result[1].period_end == date(2024, 12, 31)

    def test_empty_days(self):
        """Empty day list returns empty result."""
        result = calculate_period_statistics([], "month")

        assert result == []

    def test_period_metrics(self, db_session, test_device, test_session_factory):
        """Period statistics compute correct metrics."""
        device = test_device
        start_date = date(2024, 11, 1)

        days = []
        for i in range(10):
            session_date = start_date + timedelta(days=i)
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(
                    session_date.year, session_date.month, session_date.day, 12, 0, 0
                ),
                duration_hours=7.5,
                ahi=4.0 + i * 0.5,
                pressure_median=10.0,
                leak_median=8.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        result = calculate_period_statistics(days, "month")

        assert len(result) == 1
        period = result[0]
        assert period.days_used == 10
        assert period.avg_hours_per_day == pytest.approx(7.5, abs=0.01)
        assert period.avg_ahi == pytest.approx(6.25, abs=0.5)
        assert period.median_ahi == pytest.approx(6.25, abs=0.5)
        assert period.avg_pressure == pytest.approx(10.0, abs=0.01)
        assert period.avg_leak == pytest.approx(8.0, abs=0.01)

    def test_invalid_period_type(self, db_session, test_device, test_session_factory):
        """Invalid period type raises ValueError."""
        device = test_device

        session = test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 1, 12, 0, 0),
            duration_hours=8.0,
            ahi=5.0,
        )
        day = DayManager.link_session_to_day(session, device.id, db_session)

        with pytest.raises(ValueError, match="Invalid period_type: invalid"):
            calculate_period_statistics([day], "invalid")


class TestCalculateTrends:
    """Test trend data extraction."""

    def test_trends_structure(self, db_session, test_device, test_session_factory):
        """Trends returns dict with expected keys."""
        device = test_device
        start_date = date(2024, 11, 1)

        days = []
        for i in range(10):
            session_date = start_date + timedelta(days=i)
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(
                    session_date.year, session_date.month, session_date.day, 12, 0, 0
                ),
                duration_hours=7.5,
                ahi=5.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        period_stats = calculate_period_statistics(days, "week")
        result = calculate_trends(period_stats)

        assert set(result.keys()) == {"ahi", "usage", "spo2", "leak"}
        assert isinstance(result["ahi"], list)
        assert isinstance(result["usage"], list)

    def test_trends_values(self, db_session, test_device, test_session_factory):
        """Trend values match period statistics."""
        device = test_device
        start_date = date(2024, 11, 1)

        days = []
        for i in range(10):
            session_date = start_date + timedelta(days=i)
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(
                    session_date.year, session_date.month, session_date.day, 12, 0, 0
                ),
                duration_hours=7.5,
                ahi=5.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days.append(day)

        period_stats = calculate_period_statistics(days, "week")
        result = calculate_trends(period_stats)

        assert len(result["ahi"]) == len(period_stats)
        for i, (trend_date, trend_ahi) in enumerate(result["ahi"]):
            assert trend_date == period_stats[i].period_start
            assert trend_ahi == period_stats[i].avg_ahi

    def test_trends_empty(self):
        """Empty period statistics returns empty trend lists."""
        result = calculate_trends([])

        assert result == {"ahi": [], "usage": [], "spo2": [], "leak": []}
