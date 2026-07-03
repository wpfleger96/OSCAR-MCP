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
    calculate_trends_extended,
)
from snore.database.day_manager import DayManager
from snore.database.models import Day


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


class TestDayGranularity:
    """Test 'day' period type: boundaries, bucketing, and multi-device merging."""

    def test_day_boundaries_contiguous(
        self, db_session, test_device, test_session_factory
    ):
        """Day granularity emits contiguous (d, d) tuples, one per day, inclusive."""
        device = test_device
        days_orm = []
        for i in range(3):
            d = date(2024, 3, 1) + timedelta(days=i)
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(d.year, d.month, d.day, 22, 0, 0),
                duration_hours=7.0,
                ahi=3.0,
            )
            day = DayManager.link_session_to_day(session, device.id, db_session)
            days_orm.append(day)

        result = calculate_period_statistics(days_orm, "day")

        assert len(result) == 3
        for i, period in enumerate(result):
            expected = date(2024, 3, 1) + timedelta(days=i)
            assert period.period_start == expected
            assert period.period_end == expected
            assert period.days_in_period == 1

    def test_day_bucketing_skips_gap_days(
        self, db_session, test_device, test_session_factory
    ):
        """Gap days with no therapy records are absent from the results."""
        device = test_device
        # Create sessions on day 1 and day 3, leaving day 2 as a gap
        for d in [date(2024, 5, 1), date(2024, 5, 3)]:
            session = test_session_factory(
                device_id=device.id,
                start_time=datetime(d.year, d.month, d.day, 22, 0, 0),
                duration_hours=7.0,
                ahi=2.0,
            )
            DayManager.link_session_to_day(session, device.id, db_session)

        day_records = db_session.query(Day).filter(Day.device_id == device.id).all()
        result = calculate_period_statistics(day_records, "day")

        result_dates = [p.period_start for p in result]
        assert date(2024, 5, 1) in result_dates
        assert date(2024, 5, 3) in result_dates
        # The gap day must not appear as a zero-AHI bucket
        assert date(2024, 5, 2) not in result_dates
        assert len(result) == 2

    def test_day_multidevice_same_date_merges(self, db_session, test_session_factory):
        """Multiple Day rows sharing a date (one per device) merge into one bucket."""
        import uuid

        from snore.database.models import Device

        # Create two distinct devices
        devices = []
        for _ in range(2):
            dev = Device(
                manufacturer="Mfr",
                model="M1",
                serial_number=f"SN_{uuid.uuid4().hex[:8]}",
            )
            db_session.add(dev)
        db_session.flush()
        devices = db_session.query(Device).all()

        target_date = date(2024, 6, 15)
        for dev in devices:
            session = test_session_factory(
                device_id=dev.id,
                start_time=datetime(2024, 6, 15, 22, 0, 0),
                duration_hours=8.0,
                ahi=4.0,
            )
            DayManager.link_session_to_day(session, dev.id, db_session)

        day_records = db_session.query(Day).all()
        # Sanity: two Day rows exist for the same date
        same_date_days = [d for d in day_records if d.date == target_date]
        assert len(same_date_days) == 2

        result = calculate_period_statistics(day_records, "day")

        matching = [p for p in result if p.period_start == target_date]
        assert len(matching) == 1, "two devices on same date must merge to one bucket"
        assert matching[0].days_used == 2


class TestNewPeriodFields:
    """Test avg_oai, avg_cai, avg_hi, avg_rera population."""

    def _make_day(
        self,
        db_session: object,
        device: object,
        day_date: date,
        oai: float | None = None,
        cai: float | None = None,
        hi: float | None = None,
        reras: int = 0,
        total_therapy_hours: float = 8.0,
    ) -> Day:
        from snore.database.models import Day as DayModel

        day = DayModel(
            device_id=device.id,
            date=day_date,
            total_therapy_hours=total_therapy_hours,
            oai=oai,
            cai=cai,
            hi=hi,
            reras=reras,
        )
        db_session.add(day)
        db_session.flush()
        return day

    def test_oai_cai_hi_populated(self, db_session, test_device):
        """avg_oai, avg_cai, avg_hi are averaged across days in the period."""
        days = [
            self._make_day(
                db_session,
                test_device,
                date(2024, 7, i + 1),
                oai=float(i + 1),
                cai=float(i + 2),
                hi=float(i + 3),
            )
            for i in range(3)
        ]

        result = calculate_period_statistics(days, "month")

        assert len(result) == 1
        p = result[0]
        assert p.avg_oai == pytest.approx(2.0, abs=0.01)  # mean of 1,2,3
        assert p.avg_cai == pytest.approx(3.0, abs=0.01)  # mean of 2,3,4
        assert p.avg_hi == pytest.approx(4.0, abs=0.01)  # mean of 3,4,5

    def test_avg_rera_hand_computed(self, db_session, test_device):
        """avg_rera = mean(reras / total_therapy_hours) across days with usage."""
        days = [
            self._make_day(
                db_session,
                test_device,
                date(2024, 8, 1),
                reras=8,
                total_therapy_hours=8.0,  # rate = 1.0
            ),
            self._make_day(
                db_session,
                test_device,
                date(2024, 8, 2),
                reras=6,
                total_therapy_hours=6.0,  # rate = 1.0
            ),
            self._make_day(
                db_session,
                test_device,
                date(2024, 8, 3),
                reras=12,
                total_therapy_hours=4.0,  # rate = 3.0
            ),
        ]

        result = calculate_period_statistics(days, "month")

        assert len(result) == 1
        # mean(1.0, 1.0, 3.0) = 5/3 ≈ 1.667
        assert result[0].avg_rera == pytest.approx(5.0 / 3.0, abs=0.01)

    def test_avg_rera_none_when_no_qualifying_days(self, db_session, test_device):
        """avg_rera is None when all days in a bucket have total_therapy_hours == 0."""
        days = [
            self._make_day(
                db_session,
                test_device,
                date(2024, 9, i + 1),
                reras=5,
                total_therapy_hours=0.0,
            )
            for i in range(2)
        ]

        result = calculate_period_statistics(days, "month")

        # Days exist so the bucket is NOT skipped; only avg_hours_per_day and avg_rera are None.
        assert len(result) == 1
        assert result[0].avg_hours_per_day is None
        assert result[0].avg_rera is None

    def test_avg_rera_zero_when_reras_zero_with_usage(self, db_session, test_device):
        """avg_rera is 0.0 when reras=0 and therapy hours are positive."""
        days2 = [
            self._make_day(
                db_session,
                test_device,
                date(2024, 10, i + 1),
                reras=0,
                total_therapy_hours=8.0,
            )
            for i in range(2)
        ]
        result2 = calculate_period_statistics(days2, "month")
        assert len(result2) == 1
        assert result2[0].avg_rera == pytest.approx(0.0, abs=0.01)

    def test_new_fields_none_when_no_oai_cai_hi(self, db_session, test_device):
        """avg_oai/cai/hi are None when the underlying Day fields are all None."""
        day = self._make_day(
            db_session,
            test_device,
            date(2024, 11, 1),
            oai=None,
            cai=None,
            hi=None,
            total_therapy_hours=8.0,
        )

        result = calculate_period_statistics([day], "month")

        assert len(result) == 1
        assert result[0].avg_oai is None
        assert result[0].avg_cai is None
        assert result[0].avg_hi is None


class TestCalculateTrends:
    """Test trend data extraction."""

    def test_trends_structure(self, db_session, test_device, test_session_factory):
        """Trends returns dict with all 13 expected keys."""
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

        expected_keys = {
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
        assert set(result.keys()) == expected_keys
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
        """Empty period statistics returns empty lists for all 13 keys."""
        result = calculate_trends([])

        expected_keys = {
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
        assert set(result.keys()) == expected_keys
        for key in expected_keys:
            assert result[key] == []


class TestCalculateTrendsExtended:
    """Test the extended trends function with session extras."""

    def _make_period_stats(self, db_session: object, test_device: object) -> list:
        """Create two monthly period stats for testing."""
        from snore.database.models import Day as DayModel

        for month in [1, 2]:
            day = DayModel(
                device_id=test_device.id,
                date=date(2024, month, 15),
                total_therapy_hours=8.0,
                ahi=float(month),
            )
            db_session.add(day)
        db_session.flush()

        day_records = (
            db_session.query(DayModel)
            .filter(DayModel.device_id == test_device.id)
            .all()
        )
        return calculate_period_statistics(day_records, "month")

    def test_thirteen_keys_always_present(self, db_session, test_device):
        """calculate_trends_extended always returns exactly 13 keys."""
        period_stats = self._make_period_stats(db_session, test_device)
        result = calculate_trends_extended(period_stats, {})

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

    def test_extras_absent_session_keys_are_none(self, db_session, test_device):
        """When session_extras is empty, epap/rr/pulse/mv entries are all None."""
        period_stats = self._make_period_stats(db_session, test_device)
        result = calculate_trends_extended(period_stats, {})

        for key in ("epap", "rr", "pulse", "mv"):
            for _, value in result[key]:
                assert value is None

    def test_extras_values_land_on_correct_period_start(self, db_session, test_device):
        """Values from session_extras are associated with the correct period_start."""
        period_stats = self._make_period_stats(db_session, test_device)
        jan_start = date(2024, 1, 1)
        feb_start = date(2024, 2, 1)

        extras = {
            jan_start: {"epap": 5.0, "rr": 14.0, "pulse": 62.0, "mv": 7.5},
            feb_start: {"epap": 6.0, "rr": 15.0, "pulse": 65.0, "mv": 8.0},
        }

        result = calculate_trends_extended(period_stats, extras)

        epap_by_date = dict(result["epap"])
        assert epap_by_date[jan_start] == pytest.approx(5.0)
        assert epap_by_date[feb_start] == pytest.approx(6.0)

        pulse_by_date = dict(result["pulse"])
        assert pulse_by_date[jan_start] == pytest.approx(62.0)
        assert pulse_by_date[feb_start] == pytest.approx(65.0)

    def test_empty_period_stats_returns_empty_lists(self):
        """Empty period_stats yields empty lists for all 13 keys."""
        result = calculate_trends_extended([], {})

        assert all(v == [] for v in result.values())
        assert len(result) == 13
