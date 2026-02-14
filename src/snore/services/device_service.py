"""Device service for device listing operations."""

from sqlalchemy.orm import Session

from snore.database import models
from snore.services.schemas import DeviceInfo

__all__ = ["DeviceService"]


class DeviceService:
    """Service for device listing operations."""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def list_devices(self) -> list[DeviceInfo]:
        """List all devices ordered by manufacturer."""
        devices = (
            self.db_session.query(models.Device)
            .order_by(models.Device.manufacturer, models.Device.model)
            .all()
        )

        return [
            DeviceInfo(
                id=d.id,
                manufacturer=d.manufacturer,
                model=d.model,
                serial_number=d.serial_number,
            )
            for d in devices
        ]
