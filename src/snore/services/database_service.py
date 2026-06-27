"""Database service for database statistics, metadata, and device queries."""

import os

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from snore.database import models
from snore.services.schemas import DatabaseStats, DeviceInfo, VacuumResult

__all__ = ["DatabaseService"]


class DatabaseService:
    """Service for database statistics, metadata, and device listing operations."""

    def __init__(self, db_session: Session):
        """
        Initialize database service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    def get_stats(self, db_path: str) -> DatabaseStats:
        """
        Query database statistics including table counts and coverage metrics.

        Args:
            db_path: Path to the database file (for file size calculation)

        Returns:
            DatabaseStats with all counts, percentages, and date range
        """
        profile_count = self.db_session.query(models.Profile).count()
        device_count = self.db_session.query(models.Device).count()
        session_count = self.db_session.query(models.Session).count()
        day_count = self.db_session.query(models.Day).count()
        event_count = (
            self.db_session.execute(text("SELECT COUNT(*) FROM events")).scalar() or 0
        )
        waveform_count = self.db_session.query(models.Waveform).count()
        analysis_count = self.db_session.query(models.AnalysisResult).count()
        pattern_count = self.db_session.query(models.DetectedPattern).count()

        sessions_with_waveforms = (
            self.db_session.query(models.Session)
            .filter(models.Session.has_waveform_data == True)
            .count()
        )
        sessions_with_events = (
            self.db_session.query(models.Session)
            .filter(models.Session.has_event_data == True)
            .count()
        )

        first_session_raw = self.db_session.execute(
            text("SELECT MIN(start_time) as first FROM sessions")
        ).scalar()

        last_session_raw = self.db_session.execute(
            text("SELECT MAX(start_time) as last FROM sessions")
        ).scalar()

        first_session = None
        last_session = None
        if first_session_raw:
            first_session = (
                datetime.fromisoformat(first_session_raw)
                if isinstance(first_session_raw, str)
                else first_session_raw
            )
        if last_session_raw:
            last_session = (
                datetime.fromisoformat(last_session_raw)
                if isinstance(last_session_raw, str)
                else last_session_raw
            )

        size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        size_mb = size_bytes / (1024 * 1024)

        waveform_coverage_pct = (
            (sessions_with_waveforms / session_count * 100) if session_count > 0 else 0
        )
        event_coverage_pct = (
            (sessions_with_events / session_count * 100) if session_count > 0 else 0
        )
        analysis_coverage_pct = (
            (analysis_count / session_count * 100) if session_count > 0 else 0
        )

        return DatabaseStats(
            db_path=db_path,
            size_mb=size_mb,
            profile_count=profile_count,
            device_count=device_count,
            session_count=session_count,
            day_count=day_count,
            event_count=event_count,
            waveform_count=waveform_count,
            analysis_count=analysis_count,
            pattern_count=pattern_count,
            sessions_with_waveforms=sessions_with_waveforms,
            sessions_with_events=sessions_with_events,
            waveform_coverage_pct=waveform_coverage_pct,
            event_coverage_pct=event_coverage_pct,
            analysis_coverage_pct=analysis_coverage_pct,
            first_session=first_session,
            last_session=last_session,
        )

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

    def vacuum(self, db_path: str) -> VacuumResult:
        """Vacuum the database to reclaim space after deletions."""
        size_before = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )

        self.db_session.execute(text("VACUUM"))
        self.db_session.commit()

        size_after = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )

        return VacuumResult(
            status="success",
            size_before_mb=size_before,
            size_after_mb=size_after,
        )
