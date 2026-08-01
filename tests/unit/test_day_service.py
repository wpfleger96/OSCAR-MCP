"""Unit tests for DayService."""

from datetime import date, datetime, timedelta

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.models import Day, Device, Session
from snore.exceptions import NotFoundError
from snore.services.day_service import DayService


async def _create_day(
    db_session: AsyncSession,
    device: Device,
    day_date: date,
    ahi: float | None = None,
    total_therapy_hours: float = 8.0,
    session_count: int = 1,
) -> Day:
    """Helper to create a Day record with an optional linked Session."""
    day = Day(
        device_id=device.id,
        date=day_date,
        session_count=session_count,
        total_therapy_hours=total_therapy_hours,
        ahi=ahi,
        oai=0.1,
        cai=0.2,
        hi=0.3,
        pressure_mean=10.0,
        leak_median=5.0,
        spo2_mean=96.5,
    )
    db_session.add(day)
    await db_session.flush()
    return day


async def _create_session_for_day(db_session: AsyncSession, device: Device, day: Day) -> Session:
    """Create a Session linked to a Day."""
    sess = Session(
        device_id=device.id,
        day_id=day.id,
        device_session_id=f"day_test_{day.date.isoformat()}",
        start_time=datetime.combine(day.date, datetime.min.time()),
        end_time=datetime.combine(day.date, datetime.min.time()) + timedelta(hours=8),
        duration_seconds=28800,
    )
    db_session.add(sess)
    await db_session.flush()
    return sess


class TestDayServiceList:
    async def test_list_empty(self, async_db_session, async_test_device):
        """Empty database returns empty list and zero total."""
        service = DayService(async_db_session)
        items, total = await service.list_days()
        assert items == []
        assert total == 0

    async def test_list_returns_all_days(self, async_db_session, async_test_device):
        """All days returned when no filters applied."""
        await _create_day(async_db_session, async_test_device, date(2025, 1, 1), ahi=2.0)
        await _create_day(async_db_session, async_test_device, date(2025, 1, 2), ahi=3.0)
        await _create_day(async_db_session, async_test_device, date(2025, 1, 3), ahi=4.0)
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, total = await service.list_days(limit=0)

        assert total == 3
        assert len(items) == 3

    async def test_list_sorted_desc_by_date(self, async_db_session, async_test_device):
        """Results are sorted by date descending."""
        await _create_day(async_db_session, async_test_device, date(2025, 1, 1))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 3))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 2))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, _ = await service.list_days(limit=0)

        assert items[0].date == date(2025, 1, 3)
        assert items[1].date == date(2025, 1, 2)
        assert items[2].date == date(2025, 1, 1)

    async def test_list_with_from_date_filter(self, async_db_session, async_test_device):
        """from_date filter excludes older records."""
        await _create_day(async_db_session, async_test_device, date(2025, 1, 1))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 5))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 10))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, total = await service.list_days(from_date=date(2025, 1, 5), limit=0)

        assert total == 2
        assert all(item.date >= date(2025, 1, 5) for item in items)

    async def test_list_with_to_date_filter(self, async_db_session, async_test_device):
        """to_date filter excludes newer records."""
        await _create_day(async_db_session, async_test_device, date(2025, 1, 1))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 5))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 10))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, total = await service.list_days(to_date=date(2025, 1, 5), limit=0)

        assert total == 2
        assert all(item.date <= date(2025, 1, 5) for item in items)

    async def test_list_with_date_range(self, async_db_session, async_test_device):
        """Combined from_date/to_date range filters correctly."""
        await _create_day(async_db_session, async_test_device, date(2025, 1, 1))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 5))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 10))
        await _create_day(async_db_session, async_test_device, date(2025, 1, 15))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, total = await service.list_days(
            from_date=date(2025, 1, 4), to_date=date(2025, 1, 11), limit=0
        )

        assert total == 2
        assert {item.date for item in items} == {date(2025, 1, 5), date(2025, 1, 10)}

    async def test_list_with_device_id_filter(self, async_db_session, async_test_device):
        """device_id filter restricts to that device only."""
        from snore.database.models import Device

        other_device = Device(
            manufacturer="Other", model="Model", serial_number="OTHER_RX_999"
        )
        async_db_session.add(other_device)
        await async_db_session.flush()

        await _create_day(async_db_session, async_test_device, date(2025, 1, 1))
        await _create_day(async_db_session, other_device, date(2025, 1, 2))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, total = await service.list_days(device_id=async_test_device.id, limit=0)

        assert total == 1
        assert items[0].device_id == async_test_device.id

    async def test_list_limit_applied(self, async_db_session, async_test_device):
        """limit parameter restricts number of items returned."""
        for i in range(10):
            await _create_day(async_db_session, async_test_device, date(2025, 1, 1) + timedelta(days=i))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, total = await service.list_days(limit=3)

        assert total == 10
        assert len(items) == 3

    async def test_list_offset_applied(self, async_db_session, async_test_device):
        """offset parameter skips records."""
        for i in range(5):
            await _create_day(async_db_session, async_test_device, date(2025, 1, 1) + timedelta(days=i))
        await async_db_session.flush()

        service = DayService(async_db_session)
        items_page1, total = await service.list_days(limit=2, offset=0)
        items_page2, _ = await service.list_days(limit=2, offset=2)

        assert total == 5
        assert len(items_page1) == 2
        assert len(items_page2) == 2
        # Pages should not overlap
        dates_page1 = {item.date for item in items_page1}
        dates_page2 = {item.date for item in items_page2}
        assert dates_page1.isdisjoint(dates_page2)

    async def test_list_item_fields(self, async_db_session, async_test_device):
        """DayListItem contains expected fields with correct values."""
        await _create_day(
            async_db_session, async_test_device, date(2025, 6, 15), ahi=2.5, total_therapy_hours=7.0
        )
        await async_db_session.flush()

        service = DayService(async_db_session)
        items, _ = await service.list_days()

        assert len(items) == 1
        item = items[0]
        assert item.date == date(2025, 6, 15)
        assert item.device_id == async_test_device.id
        assert item.session_count == 1
        assert item.total_therapy_hours == pytest.approx(7.0)
        assert item.ahi == pytest.approx(2.5)


class TestDayServiceGet:
    async def test_get_nonexistent(self, async_db_session):
        """Raises NotFoundError for a date with no Day record."""
        service = DayService(async_db_session)
        with pytest.raises(NotFoundError):
            await service.get_day(date(2024, 1, 1))

    async def test_get_existing_day(self, async_db_session, async_test_device):
        """Returns full DayDetail with all stats fields."""
        await _create_day(
            async_db_session, async_test_device, date(2025, 4, 10), ahi=3.5, total_therapy_hours=8.0
        )
        await async_db_session.flush()

        service = DayService(async_db_session)
        result = await service.get_day(date(2025, 4, 10))

        assert result is not None
        assert result.date == date(2025, 4, 10)
        assert result.device_id == async_test_device.id
        assert result.ahi == pytest.approx(3.5)
        assert result.oai == pytest.approx(0.1)
        assert result.cai == pytest.approx(0.2)
        assert result.hi == pytest.approx(0.3)
        assert result.avg_pressure == pytest.approx(10.0)
        assert result.avg_leak == pytest.approx(5.0)
        assert result.avg_spo2 == pytest.approx(96.5)

    async def test_get_day_with_session_ids(self, async_db_session, async_test_device):
        """DayDetail includes linked session IDs."""
        day = await _create_day(async_db_session, async_test_device, date(2025, 5, 20))
        sess1 = await _create_session_for_day(async_db_session, async_test_device, day)
        # Second session same day
        sess2 = Session(
            device_id=async_test_device.id,
            day_id=day.id,
            device_session_id="day_test_2025-05-20_b",
            start_time=datetime(2025, 5, 20, 4, 0),
            end_time=datetime(2025, 5, 20, 10, 0),
            duration_seconds=21600,
        )
        async_db_session.add(sess2)
        await async_db_session.flush()

        service = DayService(async_db_session)
        result = await service.get_day(date(2025, 5, 20))

        assert result is not None
        assert set(result.session_ids) == {sess1.id, sess2.id}

    async def test_get_day_empty_session_ids(self, async_db_session, async_test_device):
        """DayDetail has empty session_ids when no sessions linked."""
        await _create_day(async_db_session, async_test_device, date(2025, 7, 1))
        await async_db_session.flush()

        service = DayService(async_db_session)
        result = await service.get_day(date(2025, 7, 1))

        assert result is not None
        assert result.session_ids == []
