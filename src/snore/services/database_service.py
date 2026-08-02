"""Database service for database statistics and metadata operations."""

import os

from datetime import datetime

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.models import Base
from snore.services.schemas import DatabaseStats, VacuumResult

__all__ = ["DatabaseService"]


class DatabaseService:
    """Service for database statistics and metadata operations."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize database service.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    async def get_stats(self, db_path: str) -> DatabaseStats:
        """
        Query database statistics including table counts and coverage metrics.

        Args:
            db_path: Path to the database file (for file size calculation)

        Returns:
            DatabaseStats with all counts, percentages, and date range
        """
        profile_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.Profile)
            )
        ).scalar() or 0
        device_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.Device)
            )
        ).scalar() or 0
        session_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.Session)
            )
        ).scalar() or 0
        day_count = (
            await self.db_session.execute(select(func.count()).select_from(models.Day))
        ).scalar() or 0
        event_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.Event)
            )
        ).scalar() or 0
        waveform_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.Waveform)
            )
        ).scalar() or 0
        analysis_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.AnalysisResult)
            )
        ).scalar() or 0
        pattern_count = (
            await self.db_session.execute(
                select(func.count()).select_from(models.DetectedPattern)
            )
        ).scalar() or 0

        sessions_with_waveforms = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .where(models.Session.has_waveform_data.is_(True))
            )
        ).scalar() or 0
        sessions_with_events = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .where(models.Session.has_event_data.is_(True))
            )
        ).scalar() or 0

        first_session_raw = (
            await self.db_session.execute(select(func.min(models.Session.start_time)))
        ).scalar()

        last_session_raw = (
            await self.db_session.execute(select(func.max(models.Session.start_time)))
        ).scalar()

        first_session = None
        last_session = None
        if first_session_raw is not None:
            first_session = (
                datetime.fromisoformat(first_session_raw)
                if isinstance(first_session_raw, str)
                else first_session_raw
            )
        if last_session_raw is not None:
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

    async def reset_rows(self) -> dict[str, int]:
        """Delete all rows from all data tables.  Caller-transaction-owned.

        Performs generic typed ``table.delete()`` statements in FK-safe order.
        Does NOT commit — the caller owns the transaction.
        Does NOT VACUUM — call ``vacuum_sqlite()`` separately if needed.

        Returns:
            Mapping of table name → rows deleted.
        """
        tables_cleared: dict[str, int] = {}
        for table in reversed(Base.metadata.sorted_tables):
            cursor = await self.db_session.execute(table.delete())
            count = cursor.rowcount or 0  # type: ignore[attr-defined]
            tables_cleared[table.name] = count
        return tables_cleared

    def vacuum_sqlite(self, db_path: str) -> VacuumResult:
        """Run VACUUM on a SQLite file-backed database.

        Dispatched separately from row-reset so callers can vacuum without
        deleting, or skip vacuum on non-SQLite targets.

        Args:
            db_path: Path to the SQLite file.

        Raises:
            RuntimeError: If db_path is not a valid SQLite file target.
        """
        if not self.is_sqlite_target(db_path):
            raise RuntimeError(
                f"vacuum_sqlite() requires a SQLite file target; got {db_path!r}"
            )
        vacuum_engine = create_engine(
            f"sqlite:///{db_path}",
            isolation_level="AUTOCOMMIT",
        )
        try:
            with vacuum_engine.connect() as conn:
                conn.execute(text("VACUUM"))
        finally:
            vacuum_engine.dispose()

        size = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )
        return VacuumResult(status="success", size_before_mb=size, size_after_mb=size)

    @staticmethod
    def is_sqlite_target(db_path: str) -> bool:
        """Return True if *db_path* identifies a SQLite file target.

        Routes through ``DatabaseTarget`` to determine the dialect rather than
        relying on path-string heuristics.  A bare file path (no ``://``) is
        treated as a SQLite path by ``DatabaseTarget.from_url()``.  An explicit
        URL is parsed to extract the dialect; only ``"sqlite"`` with a non-memory
        location returns True.

        Examples:
            >>> DatabaseService.is_sqlite_target("/home/user/snore.db")
            True
            >>> DatabaseService.is_sqlite_target(":memory:")
            False
            >>> DatabaseService.is_sqlite_target("postgresql://user@host/db")
            False
            >>> DatabaseService.is_sqlite_target("sqlite:////abs/path.db")
            True
        """
        if not db_path or db_path == ":memory:":
            return False
        from snore.database.target import DatabaseTarget  # noqa: PLC0415

        try:
            target = DatabaseTarget.from_url(db_path)
        except (ValueError, Exception):
            return False  # Unrecognised — treat as non-SQLite to be safe
        return target.dialect == "sqlite" and target.location not in ("", ":memory:")
