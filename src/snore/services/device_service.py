"""Device service for device listing and detailed device information."""

from datetime import date
from itertools import groupby

from sqlalchemy import select
from sqlalchemy.orm import Session

from snore.database import models
from snore.exceptions import NotFoundError
from snore.services.schemas import (
    DeviceDetail,
    DeviceInfo,
    DeviceUsageSummary,
    SettingChangeEntry,
    SettingsChange,
)

__all__ = ["DeviceService"]


class DeviceService:
    """Service for device listing and per-device detail with usage and settings history."""

    def __init__(self, db_session: Session):
        """
        Initialize device service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    def list_devices(self) -> list[DeviceInfo]:
        """List all devices ordered by manufacturer and model."""
        devices = (
            self.db_session.execute(
                select(models.Device).order_by(
                    models.Device.manufacturer, models.Device.model
                )
            )
            .scalars()
            .all()
        )
        return [DeviceInfo.model_validate(d) for d in devices]

    def get_device_detail(self, device_id: int) -> DeviceDetail:
        """
        Get full device detail including usage summary, current settings, and settings history.

        Settings history is computed by loading all settings for the device in a single
        joined query, grouping by session, then diffing consecutive sessions that have
        settings. Sessions with no settings rows are skipped (not treated as clearing all
        keys). The first session with settings is the baseline — no history entry is emitted
        for it.

        Args:
            device_id: Database device ID

        Returns:
            DeviceDetail with identity, usage, current settings, and settings history

        Raises:
            NotFoundError: If device_id is not found
        """
        device = (
            self.db_session.execute(
                select(models.Device).where(models.Device.id == device_id)
            )
            .scalars()
            .first()
        )
        if not device:
            raise NotFoundError(f"Device {device_id} not found")

        # Enabled sessions ordered chronologically for usage summary
        sessions = (
            self.db_session.execute(
                select(models.Session)
                .where(
                    models.Session.device_id == device_id,
                    models.Session.enabled.is_(True),
                )
                .order_by(models.Session.start_time)
            )
            .scalars()
            .all()
        )

        session_count = len(sessions)
        first_session_date: date | None = (
            sessions[0].start_time.date() if sessions else None
        )
        last_session_date: date | None = (
            sessions[-1].start_time.date() if sessions else None
        )
        total_therapy_hours = sum((s.duration_seconds or 0.0) for s in sessions) / 3600
        seen: set[str] = set()
        therapy_modes: list[str] = []
        for s in sessions:
            if s.therapy_mode and s.therapy_mode not in seen:
                seen.add(s.therapy_mode)
                therapy_modes.append(s.therapy_mode)

        # All settings for this device in one query, ordered by session start_time
        all_setting_rows = self.db_session.execute(
            select(models.Setting, models.Session)
            .join(models.Session, models.Setting.session_id == models.Session.id)
            .where(
                models.Session.device_id == device_id,
                models.Session.enabled.is_(True),
            )
            .order_by(models.Session.start_time, models.Setting.key)
        ).all()

        # Group into [(session_id, session_date, {key: value})] skipping sessions
        # that have no settings rows.
        sessions_with_settings: list[tuple[int, date, dict[str, str]]] = []
        for (_sid, _start), grp in groupby(
            all_setting_rows, key=lambda r: (r[1].id, r[1].start_time)
        ):
            rows = list(grp)
            session_obj: models.Session = rows[0][1]
            settings_dict = {r[0].key: (r[0].value or "") for r in rows}
            sessions_with_settings.append(
                (int(session_obj.id), session_obj.start_time.date(), settings_dict)
            )

        # Current settings: latest session's settings (last group)
        current_settings: dict[str, str] | None = None
        if sessions_with_settings:
            current_settings = sessions_with_settings[-1][2] or None

        # Diff consecutive sessions-with-settings; first is baseline, no entry
        settings_history: list[SettingsChange] = []
        for i in range(1, len(sessions_with_settings)):
            _prev_sid, _prev_date, prev_settings = sessions_with_settings[i - 1]
            curr_sid, curr_date, curr_settings = sessions_with_settings[i]

            changes: list[SettingChangeEntry] = []
            for key in sorted(set(prev_settings) | set(curr_settings)):
                old_val: str | None = prev_settings.get(key)
                new_val: str | None = curr_settings.get(key)
                if old_val != new_val:
                    changes.append(
                        SettingChangeEntry(
                            key=key,
                            old_value=old_val if key in prev_settings else None,
                            new_value=new_val if key in curr_settings else None,
                        )
                    )

            if changes:
                settings_history.append(
                    SettingsChange(
                        session_id=curr_sid,
                        date=curr_date,
                        changes=changes,
                    )
                )

        device_info = DeviceInfo.model_validate(device)
        return DeviceDetail(
            **device_info.model_dump(),
            usage=DeviceUsageSummary(
                session_count=session_count,
                first_session_date=first_session_date,
                last_session_date=last_session_date,
                total_therapy_hours=round(total_therapy_hours, 2),
                therapy_modes=therapy_modes,
            ),
            current_settings=current_settings,
            settings_history=settings_history,
        )
