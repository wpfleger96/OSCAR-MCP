"""
Tests for DayManager business logic.

Tests the critical day-splitting algorithm and statistical aggregation logic
that determines which calendar day sessions belong to and how statistics
are aggregated across multiple sessions.
"""

from datetime import date, datetime

import pytest

from snore.database.day_manager import DayManager


class TestDaySplitLogic:
    """Test day-splitting algorithm (hardcoded noon boundary logic).

    These tests exercise a pure classmethod with no DB access — the
    async_db_session fixture is included for consistency but not used.
    """

    def test_default_noon_split_before(self):
        """Session before noon belongs to previous calendar day."""
        session_start = datetime(2024, 11, 5, 11, 59, 0)
        result = DayManager.get_day_for_session(session_start)
        assert result == date(2024, 11, 4)

    def test_default_noon_split_at_boundary(self):
        """Session exactly at noon belongs to same calendar day."""
        session_start = datetime(2024, 11, 5, 12, 0, 0)
        result = DayManager.get_day_for_session(session_start)
        assert result == date(2024, 11, 5)

    def test_default_noon_split_after(self):
        """Session after noon belongs to same calendar day."""
        session_start = datetime(2024, 11, 5, 12, 1, 0)
        result = DayManager.get_day_for_session(session_start)
        assert result == date(2024, 11, 5)

    def test_midnight_session_with_noon_split(self):
        """Midnight session (00:00) with noon split belongs to previous day."""
        session_start = datetime(2024, 11, 5, 0, 0, 0)
        result = DayManager.get_day_for_session(session_start)
        assert result == date(2024, 11, 4)

    def test_late_night_session_23_59(self):
        """Late night session (23:59) with noon split belongs to same day."""
        session_start = datetime(2024, 11, 5, 23, 59, 0)
        result = DayManager.get_day_for_session(session_start)
        assert result == date(2024, 11, 5)


class TestStatisticalAggregation:
    """Test statistical aggregation across multiple sessions."""

    async def test_single_session_aggregation(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Single session aggregation should copy statistics directly."""
        device = async_test_device

        session_start = datetime(2024, 11, 5, 12, 0, 0)
        session = await async_test_session_factory(
            device_id=device.id,
            start_time=session_start,
            duration_hours=8.0,
            obstructive_apneas=10,
            central_apneas=5,
            hypopneas=8,
            reras=3,
            ahi=5.0,
            oai=1.25,
            cai=0.625,
            hi=1.0,
            pressure_min=8.0,
            pressure_max=15.0,
            pressure_median=11.0,
            pressure_mean=11.5,
            leak_min=0.0,
            leak_max=24.0,
            leak_median=5.0,
        )

        day = await DayManager.link_session_to_day(session, device.id, async_db_session)

        assert day.session_count == 1
        assert day.total_therapy_hours == pytest.approx(8.0, abs=0.01)
        assert day.obstructive_apneas == 10
        assert day.central_apneas == 5
        assert day.hypopneas == 8
        assert day.reras == 3
        assert day.ahi == pytest.approx(5.0, abs=0.01)
        assert day.pressure_min == pytest.approx(8.0, abs=0.01)
        assert day.pressure_max == pytest.approx(15.0, abs=0.01)

    async def test_multi_session_event_counts_sum(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Event counts should sum across sessions."""
        device = async_test_device

        session1 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=4.0,
            obstructive_apneas=10,
            central_apneas=5,
            hypopneas=8,
            reras=3,
        )

        session2 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 22, 0, 0),
            duration_hours=6.0,
            obstructive_apneas=15,
            central_apneas=3,
            hypopneas=12,
            reras=5,
        )

        await DayManager.link_session_to_day(session1, device.id, async_db_session)
        day = await DayManager.link_session_to_day(session2, device.id, async_db_session)

        assert day.session_count == 2
        assert day.obstructive_apneas == 25
        assert day.central_apneas == 8
        assert day.hypopneas == 20
        assert day.reras == 8

    async def test_weighted_average_ahi(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """AHI should be weighted by session duration."""
        device = async_test_device

        session1 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=4.0,
            ahi=10.0,
        )

        session2 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 22, 0, 0),
            duration_hours=2.0,
            ahi=4.0,
        )

        await DayManager.link_session_to_day(session1, device.id, async_db_session)
        day = await DayManager.link_session_to_day(session2, device.id, async_db_session)

        assert day.ahi == pytest.approx(8.0, abs=0.01)

    async def test_pressure_min_max_across_sessions(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Pressure min/max should be extremes across all sessions."""
        device = async_test_device

        session1 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=4.0,
            pressure_min=8.0,
            pressure_max=15.0,
            pressure_median=11.0,
        )

        session2 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 22, 0, 0),
            duration_hours=4.0,
            pressure_min=6.0,
            pressure_max=12.0,
            pressure_median=9.0,
        )

        await DayManager.link_session_to_day(session1, device.id, async_db_session)
        day = await DayManager.link_session_to_day(session2, device.id, async_db_session)

        assert day.pressure_min == pytest.approx(6.0, abs=0.01)
        assert day.pressure_max == pytest.approx(15.0, abs=0.01)

    async def test_empty_day_resets_statistics(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Day with no sessions should have reset statistics."""
        device = async_test_device

        session = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=8.0,
            obstructive_apneas=10,
            ahi=5.0,
        )

        day = await DayManager.link_session_to_day(session, device.id, async_db_session)
        assert day.session_count == 1
        assert day.obstructive_apneas == 10

        session.day_id = None
        await async_db_session.flush()
        await DayManager._aggregate_day_statistics(day, async_db_session)

        assert day.session_count == 0
        assert day.total_therapy_hours == 0.0
        assert day.obstructive_apneas == 0
        assert day.ahi is None

    async def test_partial_data_null_values(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Sessions with missing statistics should be handled gracefully."""
        device = async_test_device

        session1 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=4.0,
            ahi=8.0,
        )

        session2 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 22, 0, 0),
            duration_hours=4.0,
        )

        await DayManager.link_session_to_day(session1, device.id, async_db_session)
        day = await DayManager.link_session_to_day(session2, device.id, async_db_session)

        assert day.ahi == pytest.approx(8.0, abs=0.01)

    async def test_zero_duration_session_handling(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Sessions with zero duration should not cause division by zero."""
        device = async_test_device

        session = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=0.0,
            ahi=5.0,
        )

        day = await DayManager.link_session_to_day(session, device.id, async_db_session)

        assert day.total_therapy_hours == pytest.approx(0.0, abs=0.01)

    async def test_total_therapy_hours_sums_durations(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Total therapy hours should sum all session durations."""
        device = async_test_device

        session1 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 12, 0, 0),
            duration_hours=4.0,
        )

        session2 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 18, 0, 0),
            duration_hours=2.5,
        )

        session3 = await async_test_session_factory(
            device_id=device.id,
            start_time=datetime(2024, 11, 5, 22, 0, 0),
            duration_hours=1.5,
        )

        await DayManager.link_session_to_day(session1, device.id, async_db_session)
        await DayManager.link_session_to_day(session2, device.id, async_db_session)
        day = await DayManager.link_session_to_day(session3, device.id, async_db_session)

        assert day.total_therapy_hours == pytest.approx(8.0, abs=0.01)
        assert day.session_count == 3
