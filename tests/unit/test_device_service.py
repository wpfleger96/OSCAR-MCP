"""Unit tests for DeviceService."""

from snore.database.models import Device
from snore.services.device_service import DeviceService


class TestDeviceServiceList:
    """Tests for DeviceService.list_devices()."""

    def test_list_devices_empty(self, db_session):
        """Empty database returns empty list."""
        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 0

    def test_list_devices_with_data(self, db_session):
        """Returns correct device data."""
        device1 = Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="TEST001",
        )
        device2 = Device(
            manufacturer="Philips",
            model="DreamStation",
            serial_number="TEST002",
        )
        device3 = Device(
            manufacturer="ResMed",
            model="AirCurve 10",
            serial_number="TEST003",
        )
        db_session.add_all([device1, device2, device3])
        db_session.commit()

        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 3
        assert result[0].manufacturer == "Philips"
        assert result[0].model == "DreamStation"
        assert result[1].manufacturer == "ResMed"
        assert result[1].model == "AirCurve 10"
        assert result[2].manufacturer == "ResMed"
        assert result[2].model == "AirSense 10"

    def test_list_devices_ordering(self, db_session):
        """Devices are ordered by manufacturer then model."""
        device1 = Device(
            manufacturer="Philips",
            model="DreamStation 2",
            serial_number="TEST001",
        )
        device2 = Device(
            manufacturer="ResMed",
            model="AirSense 11",
            serial_number="TEST002",
        )
        device3 = Device(
            manufacturer="Philips",
            model="DreamStation",
            serial_number="TEST003",
        )
        device4 = Device(
            manufacturer="ResMed",
            model="AirCurve 10",
            serial_number="TEST004",
        )
        db_session.add_all([device1, device2, device3, device4])
        db_session.commit()

        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 4
        assert result[0].manufacturer == "Philips"
        assert result[0].model == "DreamStation"
        assert result[1].manufacturer == "Philips"
        assert result[1].model == "DreamStation 2"
        assert result[2].manufacturer == "ResMed"
        assert result[2].model == "AirCurve 10"
        assert result[3].manufacturer == "ResMed"
        assert result[3].model == "AirSense 11"

    def test_list_devices_includes_all_fields(self, db_session):
        """Returns all required fields."""
        device = Device(
            manufacturer="ResMed",
            model="AirSense 10",
            serial_number="12345ABC",
        )
        db_session.add(device)
        db_session.commit()

        service = DeviceService(db_session)
        result = service.list_devices()

        assert len(result) == 1
        assert result[0].id == device.id
        assert result[0].manufacturer == "ResMed"
        assert result[0].model == "AirSense 10"
        assert result[0].serial_number == "12345ABC"
