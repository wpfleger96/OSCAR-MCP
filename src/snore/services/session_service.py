"""Session service for session listing, detail, deletion, and management operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, UnaryExpression, delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from snore.constants import DEFAULT_LIST_SESSIONS_LIMIT
from snore.database import models
from snore.database.day_manager import DayManager
from snore.exceptions import NotFoundError
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

    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self.db_session = db_session
        self.profile_id = profile_id

    def _profile_filter(self) -> ColumnElement[bool]:
        """WHERE predicate: limit sessions to this profile via device ownership."""
        return models.Device.profile_id == self.profile_id

    @staticmethod
    def _session_filters(
        device: str | None,
        from_date: datetime | None,
        to_date: datetime | None,
        include_disabled: bool,
    ) -> list[ColumnElement[bool]]:
        """Build shared WHERE conditions for session list and count queries."""
        filters: list[ColumnElement[bool]] = []

        if not include_disabled:
            filters.append(models.Session.enabled.is_(True))

        if device:
            filters.append(models.Device.serial_number == device)

        if from_date:
            filters.append(models.Session.start_time >= from_date)

        if to_date:
            filters.append(models.Session.start_time <= to_date)

        return filters

    async def list_sessions(
        self,
        device: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = DEFAULT_LIST_SESSIONS_LIMIT,
        offset: int = 0,
        sort_by: str = "date-desc",
        include_disabled: bool = False,
    ) -> SessionListResult:
        """List sessions with filters, sorting, and pagination."""
        filters = self._session_filters(device, from_date, to_date, include_disabled)

        sort_map: dict[str, UnaryExpression[Any]] = {
            "date-asc": models.Session.start_time.asc(),
            "date-desc": models.Session.start_time.desc(),
            "session-id": models.Session.id.asc(),
            "duration": models.Session.duration_seconds.desc(),
        }
        order_by = sort_map.get(sort_by, models.Session.start_time.desc())

        count_query = (
            select(func.count())
            .select_from(models.Session)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(self._profile_filter(), *filters)
        )
        total_count = (await self.db_session.execute(count_query)).scalar() or 0

        list_query = (
            select(models.Session, models.Device, models.Statistics.ahi)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .outerjoin(
                models.Statistics, models.Session.id == models.Statistics.session_id
            )
            .where(self._profile_filter(), *filters)
            .order_by(order_by)
        )

        if limit > 0:
            list_query = list_query.limit(limit)

        if offset > 0:
            list_query = list_query.offset(offset)

        sessions = []
        for session, dev, ahi in await self.db_session.execute(list_query):
            sessions.append(
                SessionListItem(
                    id=session.id,
                    start_time=session.start_time,
                    duration_hours=(session.duration_seconds or 0.0) / 3600,
                    enabled=bool(session.enabled),
                    manufacturer=dev.manufacturer,
                    model=dev.model,
                    serial_number=dev.serial_number,
                    ahi=ahi,
                )
            )

        return SessionListResult(
            sessions=sessions, total_count=total_count, limit=limit
        )

    async def get_session_detail(
        self, session_id: int, include_settings: bool = False
    ) -> SessionDetail:
        """Get detailed information for a single session.

        Raises NotFoundError if the session doesn't exist or belongs to a
        different profile (foreign ID → 404, not 403, to avoid oracle attacks).
        """
        session = (
            (
                await self.db_session.execute(
                    select(models.Session)
                    .join(models.Device, models.Session.device_id == models.Device.id)
                    .where(
                        models.Session.id == session_id,
                        self._profile_filter(),
                    )
                )
            )
            .scalars()
            .first()
        )
        if not session:
            raise NotFoundError(f"Session {session_id} not found")

        device = (
            (
                await self.db_session.execute(
                    select(models.Device).where(models.Device.id == session.device_id)
                )
            )
            .scalars()
            .first()
        )

        stats_record = (
            (
                await self.db_session.execute(
                    select(models.Statistics).where(
                        models.Statistics.session_id == session.id
                    )
                )
            )
            .scalars()
            .first()
        )

        event_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Event)
                .where(models.Event.session_id == session.id)
            )
        ).scalar() or 0

        waveform_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Waveform)
                .where(models.Waveform.session_id == session.id)
            )
        ).scalar() or 0

        waveform_types = (
            (
                await self.db_session.execute(
                    select(models.Waveform.waveform_type)
                    .where(models.Waveform.session_id == session.id)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        waveform_type_list = list(waveform_types)

        statistics = None
        if stats_record:
            statistics = SessionStatistics.model_validate(stats_record)

        settings_list = None
        if include_settings:
            settings_records = (
                (
                    await self.db_session.execute(
                        select(models.Setting)
                        .where(models.Setting.session_id == session.id)
                        .order_by(models.Setting.key)
                    )
                )
                .scalars()
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
            import_source=session.import_source,
            parser_version=session.parser_version,
            data_quality_notes=(
                session.data_quality_notes
                if isinstance(session.data_quality_notes, list)
                else []
            ),
            statistics=statistics,
            settings=settings_list,
        )

    async def get_delete_preview(
        self,
        device: str | None = None,
        session_ids: list[int] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        delete_all: bool = False,
    ) -> DeletePreview:
        """Preview sessions and related data that would be deleted."""
        if not any([device, session_ids, from_date, to_date, delete_all]):
            raise ValueError(
                "At least one filter must be specified: "
                "--device, --session-id, --from, --to, or --all"
            )

        filters: list[ColumnElement[bool]] = []

        if device:
            filters.append(models.Device.serial_number == device)

        if session_ids:
            filters.append(models.Session.id.in_(session_ids))

        if from_date:
            filters.append(models.Session.start_time >= from_date)

        if to_date:
            filters.append(models.Session.start_time <= to_date)

        query = (
            select(models.Session, models.Device)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(self._profile_filter(), *filters)
            .order_by(models.Session.start_time.desc())
        )

        sessions = []
        session_ids_to_delete: list[int] = []
        for session, dev in await self.db_session.execute(query):
            sessions.append(
                SessionListItem(
                    id=session.id,
                    start_time=session.start_time,
                    duration_hours=(session.duration_seconds or 0.0) / 3600,
                    enabled=True,
                    manufacturer=dev.manufacturer,
                    model=dev.model,
                    serial_number=dev.serial_number,
                    ahi=None,
                )
            )
            session_ids_to_delete.append(session.id)

        if not session_ids_to_delete:
            return DeletePreview(
                sessions=[], event_count=0, waveform_count=0, stats_count=0
            )

        def _count_subquery(model: type[Any]) -> Any:
            return (
                select(func.count())
                .select_from(model)
                .where(model.session_id.in_(session_ids_to_delete))
                .scalar_subquery()
            )

        event_count, waveform_count, stats_count = (
            await self.db_session.execute(
                select(
                    _count_subquery(models.Event),
                    _count_subquery(models.Waveform),
                    _count_subquery(models.Statistics),
                )
            )
        ).one()

        return DeletePreview(
            sessions=sessions,
            event_count=event_count,
            waveform_count=waveform_count,
            stats_count=stats_count,
        )

    async def delete_sessions(self, session_ids: list[int]) -> int:
        """Delete sessions by ID list."""
        if not session_ids:
            return 0

        cursor: CursorResult[Any] = await self.db_session.execute(  # type: ignore[assignment]
            delete(models.Session).where(models.Session.id.in_(session_ids))
        )
        return cursor.rowcount or 0

    async def set_session_enabled(self, session_id: int, enabled: bool) -> None:
        """Toggle session enabled/disabled status.

        Raises NotFoundError if the session doesn't exist or belongs to a
        different profile (foreign ID → 404 to avoid oracle attacks).
        """
        session = (
            (
                await self.db_session.execute(
                    select(models.Session)
                    .join(models.Device, models.Session.device_id == models.Device.id)
                    .where(
                        models.Session.id == session_id,
                        self._profile_filter(),
                    )
                )
            )
            .scalars()
            .first()
        )
        if not session:
            raise NotFoundError(f"Session {session_id} not found")

        if session.enabled == enabled:
            return

        session.enabled = enabled
        await self.db_session.flush()

        if session.day_id:
            day = (
                (
                    await self.db_session.execute(
                        select(models.Day).where(models.Day.id == session.day_id)
                    )
                )
                .scalars()
                .first()
            )
            if day:
                await DayManager.recalculate_day(day, self.db_session)

    async def resolve_session_id(
        self, session_id: int | None, date: datetime | None
    ) -> int:
        """Resolve session ID from either explicit ID or date."""
        if session_id is not None:
            return session_id

        if date is None:
            raise ValueError("Either session_id or date must be provided")

        session = (
            (
                await self.db_session.execute(
                    select(models.Session)
                    .join(models.Device, models.Session.device_id == models.Device.id)
                    .join(models.Day, models.Session.day_id == models.Day.id)
                    .where(
                        models.Day.date == date.date(),
                        self._profile_filter(),
                    )
                )
            )
            .scalars()
            .first()
        )

        if not session:
            raise ValueError(f"No session found for date {date.date()}")

        return int(session.id)
