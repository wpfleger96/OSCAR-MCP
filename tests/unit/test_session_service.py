"""Unit tests for SessionService."""

from datetime import datetime, timedelta

import pytest

from snore.database.models import Day, Session
from snore.services.session_service import SessionService


class TestSessionServiceList:
    """Tests for SessionService.list_sessions()."""

    def test_list_sessions_empty(self, db_session):
        """Empty database returns empty list."""
        service = SessionService(db_session)
        result = service.list_sessions()

        assert len(result.sessions) == 0
        assert result.total_count == 0
        assert result.limit == 20

    def test_list_sessions_with_data(
        self, db_session, test_device, test_session_factory
    ):
        """Returns correct session data."""
        now = datetime.now()
        test_session_factory(
            test_device.id, now, duration_hours=8.0, ahi=2.5, usage_hours=8.0
        )
        test_session_factory(
            test_device.id,
            now + timedelta(days=1),
            duration_hours=7.0,
            ahi=3.2,
            usage_hours=7.0,
        )

        service = SessionService(db_session)
        result = service.list_sessions()

        assert len(result.sessions) == 2
        assert result.total_count == 2
        assert result.sessions[0].ahi == 3.2
        assert result.sessions[1].ahi == 2.5

    def test_list_sessions_filter_by_device(
        self, db_session, test_device, test_session_factory
    ):
        """Device filter works correctly."""
        now = datetime.now()
        test_session_factory(test_device.id, now, duration_hours=8.0)

        from snore.database.models import Device

        other_device = Device(
            manufacturer="Other", model="Model", serial_number="OTHER123"
        )
        db_session.add(other_device)
        db_session.flush()
        test_session_factory(
            other_device.id, now + timedelta(days=1), duration_hours=7.0
        )

        service = SessionService(db_session)
        result = service.list_sessions(device=test_device.serial_number)

        assert len(result.sessions) == 1
        assert result.sessions[0].serial_number == test_device.serial_number

    def test_list_sessions_date_range(
        self, db_session, test_device, test_session_factory
    ):
        """From/to date filtering works."""
        base = datetime(2025, 1, 1, 12, 0, 0)
        test_session_factory(test_device.id, base, duration_hours=8.0)
        test_session_factory(
            test_device.id, base + timedelta(days=5), duration_hours=8.0
        )
        test_session_factory(
            test_device.id, base + timedelta(days=10), duration_hours=8.0
        )

        service = SessionService(db_session)
        result = service.list_sessions(
            from_date=base + timedelta(days=2), to_date=base + timedelta(days=8)
        )

        assert len(result.sessions) == 1
        assert result.sessions[0].start_time.date() == (base + timedelta(days=5)).date()

    def test_list_sessions_excludes_disabled(
        self, db_session, test_device, test_session_factory
    ):
        """Default excludes disabled sessions."""
        now = datetime.now()
        s1 = test_session_factory(test_device.id, now, duration_hours=8.0)
        s2 = test_session_factory(
            test_device.id, now + timedelta(days=1), duration_hours=8.0
        )
        s2.enabled = False
        db_session.commit()

        service = SessionService(db_session)
        result = service.list_sessions()

        assert len(result.sessions) == 1
        assert result.sessions[0].id == s1.id

    def test_list_sessions_includes_disabled(
        self, db_session, test_device, test_session_factory
    ):
        """include_disabled=True shows disabled sessions."""
        now = datetime.now()
        test_session_factory(test_device.id, now, duration_hours=8.0)
        s2 = test_session_factory(
            test_device.id, now + timedelta(days=1), duration_hours=8.0
        )
        s2.enabled = False
        db_session.commit()

        service = SessionService(db_session)
        result = service.list_sessions(include_disabled=True)

        assert len(result.sessions) == 2

    def test_list_sessions_sorting(self, db_session, test_device, test_session_factory):
        """Sort order works correctly."""
        now = datetime.now()
        test_session_factory(test_device.id, now, duration_hours=6.0)
        test_session_factory(
            test_device.id, now + timedelta(days=1), duration_hours=8.0
        )
        test_session_factory(
            test_device.id, now + timedelta(days=2), duration_hours=7.0
        )

        service = SessionService(db_session)
        result_asc = service.list_sessions(sort_by="date-asc")
        assert result_asc.sessions[0].start_time < result_asc.sessions[1].start_time

        result_duration = service.list_sessions(sort_by="duration")
        assert result_duration.sessions[0].duration_hours == 8.0


class TestSessionServiceDetail:
    """Tests for SessionService.get_session_detail()."""

    def test_get_session_detail_found(
        self, db_session, test_device, test_session_factory
    ):
        """Returns full session detail."""
        now = datetime.now()
        session = test_session_factory(
            test_device.id, now, duration_hours=8.0, ahi=2.5, usage_hours=8.0
        )

        service = SessionService(db_session)
        detail = service.get_session_detail(session.id)

        assert detail.id == session.id
        assert detail.device_manufacturer == test_device.manufacturer
        assert detail.duration_hours == 8.0
        assert detail.statistics is not None
        assert detail.statistics.ahi == 2.5

    def test_get_session_detail_not_found(self, db_session):
        """Raises ValueError if session not found."""
        service = SessionService(db_session)

        with pytest.raises(ValueError, match="Session 999 not found"):
            service.get_session_detail(999)

    def test_get_session_detail_with_settings(
        self, db_session, test_device, test_session_factory
    ):
        """Includes settings when requested."""
        now = datetime.now()
        session = test_session_factory(test_device.id, now, duration_hours=8.0)

        from snore.database.models import Setting

        setting = Setting(session_id=session.id, key="test_key", value="test_value")
        db_session.add(setting)
        db_session.commit()

        service = SessionService(db_session)
        detail = service.get_session_detail(session.id, include_settings=True)

        assert detail.settings is not None
        assert len(detail.settings) == 1
        assert detail.settings[0].key == "test_key"


class TestSessionServiceDelete:
    """Tests for SessionService delete operations."""

    def test_delete_preview(self, db_session, test_device, test_session_factory):
        """Returns correct preview counts."""
        now = datetime.now()
        s1 = test_session_factory(test_device.id, now, duration_hours=8.0)
        s2 = test_session_factory(
            test_device.id, now + timedelta(days=1), duration_hours=7.0
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
        db_session.add(event)
        db_session.add(waveform)
        db_session.commit()

        service = SessionService(db_session)
        preview = service.get_delete_preview(session_ids=[s1.id, s2.id])

        assert len(preview.sessions) == 2
        assert preview.event_count == 1
        assert preview.waveform_count == 1
        assert preview.stats_count == 0

    def test_delete_preview_no_filters(self, db_session):
        """Raises ValueError when no filters specified."""
        service = SessionService(db_session)

        with pytest.raises(ValueError, match="At least one filter must be specified"):
            service.get_delete_preview()

    def test_delete_sessions(self, db_session, test_device, test_session_factory):
        """Actually deletes sessions."""
        now = datetime.now()
        s1 = test_session_factory(test_device.id, now, duration_hours=8.0)
        s2 = test_session_factory(
            test_device.id, now + timedelta(days=1), duration_hours=7.0
        )
        s1_id = s1.id
        s2_id = s2.id

        from snore.database.models import Event

        event = Event(
            session_id=s1_id,
            event_type="OA",
            start_time=now,
            duration_seconds=12.0,
        )
        db_session.add(event)
        db_session.commit()

        service = SessionService(db_session)
        deleted = service.delete_sessions([s1_id])

        assert deleted == 1

        remaining = db_session.query(Session).filter(Session.id == s2_id).count()
        assert remaining == 1

        deleted_session = db_session.query(Session).filter(Session.id == s1_id).count()
        assert deleted_session == 0


class TestSessionServiceEnable:
    """Tests for SessionService.set_session_enabled()."""

    def test_set_session_enabled_toggle(
        self, db_session, test_device, test_session_factory
    ):
        """Toggles enabled flag."""
        now = datetime.now()
        session = test_session_factory(test_device.id, now, duration_hours=8.0)
        assert session.enabled is True

        service = SessionService(db_session)
        service.set_session_enabled(session.id, False)

        db_session.refresh(session)
        assert session.enabled is False

    def test_set_session_enabled_not_found(self, db_session):
        """Raises ValueError if session not found."""
        service = SessionService(db_session)

        with pytest.raises(ValueError, match="Session 999 not found"):
            service.set_session_enabled(999, False)

    def test_set_session_enabled_idempotent(
        self, db_session, test_device, test_session_factory
    ):
        """Idempotent when already in desired state."""
        now = datetime.now()
        session = test_session_factory(test_device.id, now, duration_hours=8.0)

        service = SessionService(db_session)
        service.set_session_enabled(session.id, True)

        db_session.refresh(session)
        assert session.enabled is True


class TestSessionServiceResolve:
    """Tests for SessionService.resolve_session_id()."""

    def test_resolve_session_id_by_id(self, db_session):
        """Pass-through when ID provided."""
        service = SessionService(db_session)
        resolved = service.resolve_session_id(session_id=123, date=None)

        assert resolved == 123

    def test_resolve_session_id_by_date(
        self, db_session, test_device, test_session_factory
    ):
        """Resolves via Day join when date provided."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        session = test_session_factory(test_device.id, now, duration_hours=8.0)

        day = Day(
            device_id=test_device.id,
            date=now.date(),
            session_count=1,
            total_therapy_hours=8.0,
        )
        db_session.add(day)
        db_session.flush()

        session.day_id = day.id
        db_session.commit()

        service = SessionService(db_session)
        resolved = service.resolve_session_id(session_id=None, date=now)

        assert resolved == session.id

    def test_resolve_session_id_not_found(self, db_session):
        """Raises ValueError when no session found for date."""
        service = SessionService(db_session)

        with pytest.raises(ValueError, match="No session found for date"):
            service.resolve_session_id(session_id=None, date=datetime(2025, 1, 1))

    def test_resolve_session_id_no_params(self, db_session):
        """Raises ValueError when neither ID nor date provided."""
        service = SessionService(db_session)

        with pytest.raises(
            ValueError, match="Either session_id or date must be provided"
        ):
            service.resolve_session_id(session_id=None, date=None)
