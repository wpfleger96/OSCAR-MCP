"""Unit tests for DeviceService settings-history diffing and detail retrieval."""

from datetime import datetime

import pytest

from sqlalchemy.orm import Session as OrmSession

from snore.database.models import Setting
from snore.exceptions import NotFoundError
from snore.services.device_service import DeviceService


def _add_settings(
    db_session: OrmSession, session_id: int, settings: dict[str, str]
) -> None:
    """Insert Setting rows for a session."""
    for key, value in settings.items():
        db_session.add(Setting(session_id=session_id, key=key, value=value))
    db_session.flush()


class TestSettingsHistoryDiffing:
    def test_no_sessions_returns_empty_history(self, db_session, test_device):
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.settings_history == []

    def test_single_session_with_settings_is_baseline_no_history(
        self, db_session, test_device, test_session_factory
    ):
        s = test_session_factory(test_device.id, start_time=datetime(2024, 1, 1, 22, 0))
        _add_settings(db_session, s.id, {"mode": "AutoSet", "pressure_min": "4.0"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.settings_history == []

    def test_two_sessions_identical_settings_no_history(
        self, db_session, test_device, test_session_factory
    ):
        s1 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        s2 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 2, 22, 0)
        )
        _add_settings(db_session, s1.id, {"mode": "AutoSet", "pressure_min": "4.0"})
        _add_settings(db_session, s2.id, {"mode": "AutoSet", "pressure_min": "4.0"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.settings_history == []

    def test_changed_key_emits_entry(
        self, db_session, test_device, test_session_factory
    ):
        s1 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        s2 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 2, 22, 0)
        )
        _add_settings(db_session, s1.id, {"pressure_max": "12.0"})
        _add_settings(db_session, s2.id, {"pressure_max": "10.0"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert len(detail.settings_history) == 1
        entry = detail.settings_history[0]
        assert entry.session_id == s2.id
        assert len(entry.changes) == 1
        change = entry.changes[0]
        assert change.key == "pressure_max"
        assert change.old_value == "12.0"
        assert change.new_value == "10.0"

    def test_added_key_has_null_old_value(
        self, db_session, test_device, test_session_factory
    ):
        s1 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        s2 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 2, 22, 0)
        )
        _add_settings(db_session, s1.id, {"mode": "CPAP"})
        _add_settings(db_session, s2.id, {"mode": "CPAP", "epr_level": "2"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert len(detail.settings_history) == 1
        changes = {c.key: c for c in detail.settings_history[0].changes}
        assert "epr_level" in changes
        assert changes["epr_level"].old_value is None
        assert changes["epr_level"].new_value == "2"

    def test_removed_key_has_null_new_value(
        self, db_session, test_device, test_session_factory
    ):
        s1 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        s2 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 2, 22, 0)
        )
        _add_settings(db_session, s1.id, {"mode": "CPAP", "epr_level": "2"})
        _add_settings(db_session, s2.id, {"mode": "CPAP"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert len(detail.settings_history) == 1
        changes = {c.key: c for c in detail.settings_history[0].changes}
        assert "epr_level" in changes
        assert changes["epr_level"].old_value == "2"
        assert changes["epr_level"].new_value is None

    def test_session_without_settings_between_two_is_skipped(
        self, db_session, test_device, test_session_factory
    ):
        s1 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        # s2 has no settings rows — should be skipped entirely
        test_session_factory(test_device.id, start_time=datetime(2024, 1, 2, 22, 0))
        s3 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 3, 22, 0)
        )
        _add_settings(db_session, s1.id, {"mode": "AutoSet"})
        _add_settings(db_session, s3.id, {"mode": "CPAP"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        # s2 is skipped; diff is s1 vs s3
        assert len(detail.settings_history) == 1
        assert detail.settings_history[0].session_id == s3.id
        assert detail.settings_history[0].changes[0].key == "mode"
        assert detail.settings_history[0].changes[0].old_value == "AutoSet"
        assert detail.settings_history[0].changes[0].new_value == "CPAP"


class TestGetDeviceDetail:
    def test_raises_not_found_for_unknown_id(self, db_session):
        svc = DeviceService(db_session)
        with pytest.raises(NotFoundError):
            svc.get_device_detail(99999)

    def test_usage_summary_counts_enabled_sessions_only(
        self, db_session, test_device, test_session_factory
    ):
        test_session_factory(test_device.id, start_time=datetime(2024, 1, 1, 22, 0))
        disabled = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 2, 22, 0)
        )
        disabled.enabled = False
        db_session.flush()
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.usage.session_count == 1

    def test_current_settings_from_latest_session(
        self, db_session, test_device, test_session_factory
    ):
        s1 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 1, 22, 0)
        )
        s2 = test_session_factory(
            test_device.id, start_time=datetime(2024, 1, 2, 22, 0)
        )
        _add_settings(db_session, s1.id, {"mode": "CPAP"})
        _add_settings(db_session, s2.id, {"mode": "AutoSet"})
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.current_settings == {"mode": "AutoSet"}

    def test_current_settings_none_when_no_settings(
        self, db_session, test_device, test_session_factory
    ):
        test_session_factory(test_device.id, start_time=datetime(2024, 1, 1, 22, 0))
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.current_settings is None

    def test_no_sessions_gives_zero_usage(self, db_session, test_device):
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.usage.session_count == 0
        assert detail.usage.first_session_date is None
        assert detail.usage.last_session_date is None
        assert detail.usage.total_therapy_hours == 0.0

    def test_identity_fields_included(self, db_session, test_device):
        svc = DeviceService(db_session)
        detail = svc.get_device_detail(test_device.id)
        assert detail.id == test_device.id
        assert detail.manufacturer == test_device.manufacturer
        assert detail.model == test_device.model
        assert detail.serial_number == test_device.serial_number


class TestListDevices:
    def test_list_empty(self, db_session):
        svc = DeviceService(db_session)
        assert svc.list_devices() == []

    def test_list_returns_device_info(self, db_session, test_device):
        svc = DeviceService(db_session)
        devices = svc.list_devices()
        assert len(devices) == 1
        assert devices[0].id == test_device.id
        assert devices[0].serial_number == test_device.serial_number
