"""Unit tests for SessionService."""

from datetime import datetime, timedelta

import pytest

from snore.database.models import Day, Session
from snore.services.session_service import SessionService


class TestSessionServiceList:
    """Tests for SessionService.list_sessions()."""

    async def test_list_sessions_empty(self, async_db_session):
        """Empty database returns empty list."""
        service = SessionService(async_db_session, profile_id=1)
        result = await service.list_sessions()

        assert len(result.sessions) == 0
        assert result.total_count == 0
        assert result.limit == 20

    async def test_list_sessions_with_data(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Returns correct session data."""
        now = datetime.now()
        await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0, ahi=2.5, usage_hours=8.0
        )
        await async_test_session_factory(
            async_test_device.id,
            now + timedelta(days=1),
            duration_hours=7.0,
            ahi=3.2,
            usage_hours=7.0,
        )

        service = SessionService(async_db_session, profile_id=1)
        result = await service.list_sessions()

        assert len(result.sessions) == 2
        assert result.total_count == 2
        assert result.sessions[0].ahi == 3.2
        assert result.sessions[1].ahi == 2.5

    async def test_list_sessions_filter_by_device(
        self,
        async_db_session,
        async_test_device,
        async_test_profile,
        async_test_session_factory,
    ):
        """Device filter works correctly."""
        now = datetime.now()
        await async_test_session_factory(async_test_device.id, now, duration_hours=8.0)

        from snore.database.models import Device

        other_device = Device(
            profile_id=async_test_profile.id,
            manufacturer="Other",
            model="Model",
            serial_number="OTHER123",
        )
        async_db_session.add(other_device)
        await async_db_session.flush()
        await async_test_session_factory(
            other_device.id, now + timedelta(days=1), duration_hours=7.0
        )

        service = SessionService(async_db_session, profile_id=1)
        result = await service.list_sessions(device=async_test_device.serial_number)

        assert len(result.sessions) == 1
        assert result.sessions[0].serial_number == async_test_device.serial_number

    async def test_list_sessions_date_range(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """From/to date filtering works."""
        base = datetime(2025, 1, 1, 12, 0, 0)
        await async_test_session_factory(async_test_device.id, base, duration_hours=8.0)
        await async_test_session_factory(
            async_test_device.id, base + timedelta(days=5), duration_hours=8.0
        )
        await async_test_session_factory(
            async_test_device.id, base + timedelta(days=10), duration_hours=8.0
        )

        service = SessionService(async_db_session, profile_id=1)
        result = await service.list_sessions(
            from_date=base + timedelta(days=2), to_date=base + timedelta(days=8)
        )

        assert len(result.sessions) == 1
        assert result.sessions[0].start_time.date() == (base + timedelta(days=5)).date()

    async def test_list_sessions_excludes_disabled(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Default excludes disabled sessions."""
        now = datetime.now()
        s1 = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )
        s2 = await async_test_session_factory(
            async_test_device.id, now + timedelta(days=1), duration_hours=8.0
        )
        s2.enabled = False
        await async_db_session.flush()

        service = SessionService(async_db_session, profile_id=1)
        result = await service.list_sessions()

        assert len(result.sessions) == 1
        assert result.sessions[0].id == s1.id

    async def test_list_sessions_includes_disabled(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """include_disabled=True shows disabled sessions."""
        now = datetime.now()
        await async_test_session_factory(async_test_device.id, now, duration_hours=8.0)
        s2 = await async_test_session_factory(
            async_test_device.id, now + timedelta(days=1), duration_hours=8.0
        )
        s2.enabled = False
        await async_db_session.flush()

        service = SessionService(async_db_session, profile_id=1)
        result = await service.list_sessions(include_disabled=True)

        assert len(result.sessions) == 2

    async def test_list_sessions_sorting(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Sort order works correctly."""
        now = datetime.now()
        await async_test_session_factory(async_test_device.id, now, duration_hours=6.0)
        await async_test_session_factory(
            async_test_device.id, now + timedelta(days=1), duration_hours=8.0
        )
        await async_test_session_factory(
            async_test_device.id, now + timedelta(days=2), duration_hours=7.0
        )

        service = SessionService(async_db_session, profile_id=1)
        result_asc = await service.list_sessions(sort_by="date-asc")
        assert result_asc.sessions[0].start_time < result_asc.sessions[1].start_time

        result_duration = await service.list_sessions(sort_by="duration")
        assert result_duration.sessions[0].duration_hours == 8.0


class TestSessionServiceDetail:
    """Tests for SessionService.get_session_detail()."""

    async def test_get_session_detail_found(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Returns full session detail."""
        now = datetime.now()
        session = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0, ahi=2.5, usage_hours=8.0
        )

        service = SessionService(async_db_session, profile_id=1)
        detail = await service.get_session_detail(session.id)

        assert detail.id == session.id
        assert detail.device_manufacturer == async_test_device.manufacturer
        assert detail.duration_hours == 8.0
        assert detail.statistics is not None
        assert detail.statistics.ahi == 2.5

    async def test_get_session_detail_not_found(self, async_db_session):
        """Raises ValueError if session not found."""
        service = SessionService(async_db_session, profile_id=1)

        with pytest.raises(ValueError, match="Session 999 not found"):
            await service.get_session_detail(999)

    async def test_get_session_detail_with_settings(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Includes settings when requested."""
        now = datetime.now()
        session = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )

        from snore.database.models import Setting

        setting = Setting(session_id=session.id, key="test_key", value="test_value")
        async_db_session.add(setting)
        await async_db_session.flush()

        service = SessionService(async_db_session, profile_id=1)
        detail = await service.get_session_detail(session.id, include_settings=True)

        assert detail.settings is not None
        assert len(detail.settings) == 1
        assert detail.settings[0].key == "test_key"


class TestSessionServiceDelete:
    """Tests for SessionService delete operations."""

    async def test_delete_preview(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Returns correct preview counts."""
        now = datetime.now()
        s1 = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )
        s2 = await async_test_session_factory(
            async_test_device.id, now + timedelta(days=1), duration_hours=7.0
        )

        from snore.database.models import Event, Waveform

        event = Event(
            session_id=s1.id,
            event_type="OA",
            start_time=now,
            duration_seconds=12.0,
        )
        waveform = Waveform(
            session_id=s1.id,
            waveform_type="flow",
            sample_rate=25.0,
            sample_count=1000,
            data_blob=b"test",
        )
        async_db_session.add(event)
        async_db_session.add(waveform)
        await async_db_session.flush()

        service = SessionService(async_db_session, profile_id=1)
        preview = await service.get_delete_preview(session_ids=[s1.id, s2.id])

        assert len(preview.sessions) == 2
        assert preview.event_count == 1
        assert preview.waveform_count == 1
        assert preview.stats_count == 0

    async def test_delete_preview_no_filters(self, async_db_session):
        """Raises ValueError when no filters specified."""
        service = SessionService(async_db_session, profile_id=1)

        with pytest.raises(ValueError, match="At least one filter must be specified"):
            await service.get_delete_preview()

    async def test_delete_sessions(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Actually deletes sessions."""
        now = datetime.now()
        s1 = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )
        s2 = await async_test_session_factory(
            async_test_device.id, now + timedelta(days=1), duration_hours=7.0
        )
        s1_id = s1.id
        s2_id = s2.id

        from sqlalchemy import func, select

        from snore.database.models import Event

        event = Event(
            session_id=s1_id,
            event_type="OA",
            start_time=now,
            duration_seconds=12.0,
        )
        async_db_session.add(event)
        await async_db_session.flush()

        service = SessionService(async_db_session, profile_id=1)
        deleted = await service.delete_sessions([s1_id])

        assert deleted == 1

        remaining = (
            await async_db_session.execute(
                select(func.count()).where(Session.id == s2_id)
            )
        ).scalar()
        assert remaining == 1

        deleted_session = (
            await async_db_session.execute(
                select(func.count()).where(Session.id == s1_id)
            )
        ).scalar()
        assert deleted_session == 0


class TestSessionServiceEnable:
    """Tests for SessionService.set_session_enabled()."""

    async def test_set_session_enabled_toggle(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Toggles enabled flag."""
        now = datetime.now()
        session = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )
        assert session.enabled is True

        service = SessionService(async_db_session, profile_id=1)
        await service.set_session_enabled(session.id, False)

        await async_db_session.refresh(session)
        assert session.enabled is False

    async def test_set_session_enabled_not_found(self, async_db_session):
        """Raises ValueError if session not found."""
        service = SessionService(async_db_session, profile_id=1)

        with pytest.raises(ValueError, match="Session 999 not found"):
            await service.set_session_enabled(999, False)

    async def test_set_session_enabled_idempotent(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Idempotent when already in desired state."""
        now = datetime.now()
        session = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )

        service = SessionService(async_db_session, profile_id=1)
        await service.set_session_enabled(session.id, True)

        await async_db_session.refresh(session)
        assert session.enabled is True


class TestSessionServiceResolve:
    """Tests for SessionService.resolve_session_id()."""

    async def test_resolve_session_id_by_id(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Resolving by explicit ID validates ownership and returns the same ID."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        session = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )
        service = SessionService(
            async_db_session, profile_id=async_test_device.profile_id
        )
        resolved = await service.resolve_session_id(session_id=session.id, date=None)

        assert resolved == session.id

    async def test_resolve_session_id_by_date(
        self, async_db_session, async_test_device, async_test_session_factory
    ):
        """Resolves via Day join when date provided."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        session = await async_test_session_factory(
            async_test_device.id, now, duration_hours=8.0
        )

        day = Day(
            device_id=async_test_device.id,
            date=now.date(),
            session_count=1,
            total_therapy_hours=8.0,
        )
        async_db_session.add(day)
        await async_db_session.flush()

        session.day_id = day.id
        await async_db_session.flush()

        service = SessionService(async_db_session, profile_id=1)
        resolved = await service.resolve_session_id(session_id=None, date=now)

        assert resolved == session.id

    async def test_resolve_session_id_not_found(self, async_db_session):
        """Raises ValueError when no session found for date."""
        service = SessionService(async_db_session, profile_id=1)

        with pytest.raises(ValueError, match="No session found for date"):
            await service.resolve_session_id(session_id=None, date=datetime(2025, 1, 1))

    async def test_resolve_session_id_no_params(self, async_db_session):
        """Raises ValueError when neither ID nor date provided."""
        service = SessionService(async_db_session, profile_id=1)

        with pytest.raises(
            ValueError, match="Either session_id or date must be provided"
        ):
            await service.resolve_session_id(session_id=None, date=None)
