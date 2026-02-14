"""Session service for session listing, detail, deletion, and management operations."""

from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from snore.constants import DEFAULT_LIST_SESSIONS_LIMIT
from snore.database import models
from snore.database.day_manager import DayManager
from snore.services.schemas import (
    DeletePreview,
    SessionDetail,
    SessionListItem,
    SessionListResult,
    SessionSetting,
    SessionStatistics,
)

__all__ = ["SessionService"]


class SessionService:
    """Service for session listing, detail, deletion, and management."""

    def __init__(self, db_session: Session):
        """
        Initialize session service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    def list_sessions(
        self,
        device: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = DEFAULT_LIST_SESSIONS_LIMIT,
        sort_by: str = "date-desc",
        include_disabled: bool = False,
    ) -> SessionListResult:
        """
        List sessions with filters, sorting, and pagination.

        Args:
            device: Filter by device serial number
            from_date: Filter by start date (inclusive)
            to_date: Filter by end date (inclusive)
            limit: Maximum sessions to return (0 = unlimited)
            sort_by: Sort order (date-asc, date-desc, session-id, duration)
            include_disabled: Include disabled sessions

        Returns:
            SessionListResult with sessions and total count
        """
        where_clauses = []
        params = {}

        if not include_disabled:
            where_clauses.append("sessions.enabled = 1")

        if device:
            where_clauses.append("devices.serial_number = :device")
            params["device"] = device

        if from_date:
            where_clauses.append("sessions.start_time >= :from_date")
            params["from_date"] = from_date.isoformat()

        if to_date:
            where_clauses.append("sessions.start_time <= :to_date")
            params["to_date"] = to_date.isoformat()

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        sort_map = {
            "date-asc": "sessions.start_time ASC",
            "date-desc": "sessions.start_time DESC",
            "session-id": "sessions.id ASC",
            "duration": "sessions.duration_seconds DESC",
        }
        order_by = sort_map.get(sort_by, "sessions.start_time DESC")

        count_query = text(
            f"""
            SELECT COUNT(*)
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            WHERE {where_sql}
            """
        )
        total_count = self.db_session.execute(count_query, params).scalar() or 0

        limit_sql = f"LIMIT {limit}" if limit > 0 else ""

        list_query = text(
            f"""
            SELECT
                sessions.id,
                sessions.start_time,
                sessions.duration_seconds,
                sessions.enabled,
                devices.manufacturer,
                devices.model,
                devices.serial_number,
                statistics.ahi
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            LEFT JOIN statistics ON sessions.id = statistics.session_id
            WHERE {where_sql}
            ORDER BY {order_by}
            {limit_sql}
            """
        )

        results = self.db_session.execute(list_query, params).fetchall()

        sessions = []
        for row in results:
            start_time_parsed = (
                datetime.fromisoformat(row.start_time)
                if isinstance(row.start_time, str)
                else row.start_time
            )
            duration_hours = row.duration_seconds / 3600

            sessions.append(
                SessionListItem(
                    id=row.id,
                    start_time=start_time_parsed,
                    duration_hours=duration_hours,
                    enabled=bool(row.enabled),
                    manufacturer=row.manufacturer,
                    model=row.model,
                    serial_number=row.serial_number,
                    ahi=row.ahi,
                )
            )

        return SessionListResult(
            sessions=sessions, total_count=total_count, limit=limit
        )

    def get_session_detail(
        self, session_id: int, include_settings: bool = False
    ) -> SessionDetail:
        """
        Get detailed information for a single session.

        Args:
            session_id: Database session ID
            include_settings: Whether to load settings (can be slow)

        Returns:
            SessionDetail with all metadata

        Raises:
            ValueError: If session not found
        """
        session = (
            self.db_session.query(models.Session)
            .filter(models.Session.id == session_id)
            .first()
        )
        if not session:
            raise ValueError(f"Session {session_id} not found")

        device = (
            self.db_session.query(models.Device)
            .filter(models.Device.id == session.device_id)
            .first()
        )

        stats_record = (
            self.db_session.query(models.Statistics)
            .filter(models.Statistics.session_id == session.id)
            .first()
        )

        event_count = (
            self.db_session.query(models.Event)
            .filter(models.Event.session_id == session.id)
            .count()
        )

        waveform_count = (
            self.db_session.query(models.Waveform)
            .filter(models.Waveform.session_id == session.id)
            .count()
        )

        waveform_types = (
            self.db_session.query(models.Waveform.waveform_type)
            .filter(models.Waveform.session_id == session.id)
            .distinct()
            .all()
        )
        waveform_type_list = [wt[0] for wt in waveform_types]

        statistics = None
        if stats_record:
            statistics = SessionStatistics(
                usage_hours=stats_record.usage_hours,
                ahi=stats_record.ahi,
                rei=stats_record.rei,
                oai=stats_record.oai,
                cai=stats_record.cai,
                hi=stats_record.hi,
                obstructive_apneas=stats_record.obstructive_apneas,
                central_apneas=stats_record.central_apneas,
                mixed_apneas=stats_record.mixed_apneas,
                hypopneas=stats_record.hypopneas,
                reras=stats_record.reras,
                flow_limitations=stats_record.flow_limitations,
                pressure_mean=stats_record.pressure_mean,
                pressure_min=stats_record.pressure_min,
                pressure_max=stats_record.pressure_max,
                pressure_95th=stats_record.pressure_95th,
                epap_mean=stats_record.epap_mean,
                epap_min=stats_record.epap_min,
                epap_max=stats_record.epap_max,
                epap_95th=stats_record.epap_95th,
                leak_mean=stats_record.leak_mean,
                leak_percentile_70=stats_record.leak_percentile_70,
                leak_95th=stats_record.leak_95th,
                spo2_mean=stats_record.spo2_mean,
                spo2_min=stats_record.spo2_min,
                spo2_time_below_90=stats_record.spo2_time_below_90,
                pulse_mean=stats_record.pulse_mean,
                pulse_min=stats_record.pulse_min,
                pulse_max=stats_record.pulse_max,
                respiratory_rate_mean=stats_record.respiratory_rate_mean,
                tidal_volume_mean=stats_record.tidal_volume_mean,
                minute_ventilation_mean=stats_record.minute_ventilation_mean,
            )

        settings_list = None
        if include_settings:
            settings_records = (
                self.db_session.query(models.Setting)
                .filter(models.Setting.session_id == session.id)
                .order_by(models.Setting.key)
                .all()
            )
            settings_list = [
                SessionSetting(key=s.key, value=s.value) for s in settings_records
            ]

        duration_seconds = session.duration_seconds or 0.0
        return SessionDetail(
            id=session.id,
            device_session_id=session.device_session_id,
            device_manufacturer=device.manufacturer if device else None,
            device_model=device.model if device else None,
            device_serial=device.serial_number if device else None,
            start_time=session.start_time,
            end_time=session.end_time,
            duration_hours=duration_seconds / 3600,
            duration_seconds=duration_seconds,
            therapy_mode=session.therapy_mode,
            enabled=session.enabled,
            event_count=event_count,
            waveform_count=waveform_count,
            waveform_types=waveform_type_list,
            has_statistics=session.has_statistics,
            has_event_data=session.has_event_data,
            statistics=statistics,
            settings=settings_list,
        )

    def get_delete_preview(
        self,
        device: str | None = None,
        session_ids: list[int] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        delete_all: bool = False,
    ) -> DeletePreview:
        """
        Preview sessions and related data that would be deleted.

        Args:
            device: Filter by device serial number
            session_ids: Specific session IDs to delete
            from_date: Delete sessions from this date
            to_date: Delete sessions up to this date
            delete_all: Delete all sessions (dangerous)

        Returns:
            DeletePreview with sessions and counts of related data

        Raises:
            ValueError: If no filters specified
        """
        if not any([device, session_ids, from_date, to_date, delete_all]):
            raise ValueError(
                "At least one filter must be specified: "
                "--device, --session-id, --from, --to, or --all"
            )

        where_clauses = []
        params: dict[str, str | tuple[int, ...]] = {}

        if device:
            where_clauses.append("devices.serial_number = :device")
            params["device"] = device

        if session_ids:
            where_clauses.append("sessions.id IN :session_ids")
            params["session_ids"] = tuple(session_ids)

        if from_date:
            where_clauses.append("sessions.start_time >= :from_date")
            params["from_date"] = from_date.isoformat()

        if to_date:
            where_clauses.append("sessions.start_time <= :to_date")
            params["to_date"] = to_date.isoformat()

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        query = text(
            f"""
            SELECT
                sessions.id,
                sessions.device_session_id,
                sessions.start_time,
                sessions.duration_seconds,
                devices.manufacturer,
                devices.model,
                devices.serial_number
            FROM sessions
            JOIN devices ON sessions.device_id = devices.id
            WHERE {where_sql}
            ORDER BY sessions.start_time DESC
            """
        )

        if session_ids:
            query = query.bindparams(bindparam("session_ids", expanding=True))

        results = self.db_session.execute(query, params).fetchall()

        sessions = []
        session_ids_to_delete = []
        for row in results:
            start_time_parsed = (
                datetime.fromisoformat(row.start_time)
                if isinstance(row.start_time, str)
                else row.start_time
            )
            sessions.append(
                SessionListItem(
                    id=row.id,
                    start_time=start_time_parsed,
                    duration_hours=row.duration_seconds / 3600,
                    enabled=True,
                    manufacturer=row.manufacturer,
                    model=row.model,
                    serial_number=row.serial_number,
                    ahi=None,
                )
            )
            session_ids_to_delete.append(row.id)

        if not session_ids_to_delete:
            return DeletePreview(
                sessions=[], event_count=0, waveform_count=0, stats_count=0
            )

        event_count_query = text(
            "SELECT COUNT(*) FROM events WHERE session_id IN :session_ids"
        ).bindparams(bindparam("session_ids", expanding=True))
        event_count = (
            self.db_session.execute(
                event_count_query, {"session_ids": tuple(session_ids_to_delete)}
            ).scalar()
            or 0
        )

        waveform_count_query = text(
            "SELECT COUNT(*) FROM waveforms WHERE session_id IN :session_ids"
        ).bindparams(bindparam("session_ids", expanding=True))
        waveform_count = (
            self.db_session.execute(
                waveform_count_query, {"session_ids": tuple(session_ids_to_delete)}
            ).scalar()
            or 0
        )

        stats_count_query = text(
            "SELECT COUNT(*) FROM statistics WHERE session_id IN :session_ids"
        ).bindparams(bindparam("session_ids", expanding=True))
        stats_count = (
            self.db_session.execute(
                stats_count_query, {"session_ids": tuple(session_ids_to_delete)}
            ).scalar()
            or 0
        )

        return DeletePreview(
            sessions=sessions,
            event_count=event_count,
            waveform_count=waveform_count,
            stats_count=stats_count,
        )

    def delete_sessions(self, session_ids: list[int]) -> int:
        """
        Delete sessions by ID list.

        CASCADE foreign keys will automatically delete related:
        - Events, Waveforms, Statistics, Settings, AnalysisResults

        Args:
            session_ids: List of session IDs to delete

        Returns:
            Number of sessions deleted
        """
        if not session_ids:
            return 0

        # Use ORM delete for proper rowcount tracking
        count = (
            self.db_session.query(models.Session)
            .filter(models.Session.id.in_(session_ids))
            .delete(synchronize_session=False)
        )
        self.db_session.commit()

        return count

    def set_session_enabled(self, session_id: int, enabled: bool) -> None:
        """
        Toggle session enabled/disabled status.

        When enabled status changes, recalculates Day statistics via DayManager.

        Args:
            session_id: Database session ID
            enabled: New enabled state

        Raises:
            ValueError: If session not found
        """
        session = (
            self.db_session.query(models.Session)
            .filter(models.Session.id == session_id)
            .first()
        )
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if session.enabled == enabled:
            return

        session.enabled = enabled

        if session.day_id:
            day = (
                self.db_session.query(models.Day)
                .filter(models.Day.id == session.day_id)
                .first()
            )
            if day:
                DayManager.recalculate_day(day, self.db_session)

        self.db_session.commit()

    def resolve_session_id(self, session_id: int | None, date: datetime | None) -> int:
        """
        Resolve session ID from either explicit ID or date.

        Args:
            session_id: Explicit session ID (pass-through if provided)
            date: Date to find session for (via Day table join)

        Returns:
            Resolved session ID

        Raises:
            ValueError: If neither ID nor date provided, or no session found for date
        """
        if session_id is not None:
            return session_id

        if date is None:
            raise ValueError("Either session_id or date must be provided")

        session = (
            self.db_session.query(models.Session)
            .join(models.Day)
            .filter(models.Day.date == date.date())
            .first()
        )

        if not session:
            raise ValueError(f"No session found for date {date.date()}")

        return int(session.id)
