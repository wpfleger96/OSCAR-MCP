"""Database service for database statistics and metadata operations."""

import os

from datetime import datetime

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from snore.database import models
from snore.database.models import Base
from snore.services.schemas import DatabaseStats, ResetResult, VacuumResult

__all__ = ["DatabaseService"]


class DatabaseService:
    """Service for database statistics and metadata operations."""

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
        profile_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.Profile)
            ).scalar()
            or 0
        )
        device_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.Device)
            ).scalar()
            or 0
        )
        session_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.Session)
            ).scalar()
            or 0
        )
        day_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.Day)
            ).scalar()
            or 0
        )
        event_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.Event)
            ).scalar()
            or 0
        )
        waveform_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.Waveform)
            ).scalar()
            or 0
        )
        analysis_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.AnalysisResult)
            ).scalar()
            or 0
        )
        pattern_count = (
            self.db_session.execute(
                select(func.count()).select_from(models.DetectedPattern)
            ).scalar()
            or 0
        )

        sessions_with_waveforms = (
            self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .where(models.Session.has_waveform_data.is_(True))
            ).scalar()
            or 0
        )
        sessions_with_events = (
            self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .where(models.Session.has_event_data.is_(True))
            ).scalar()
            or 0
        )

        first_session_raw = self.db_session.execute(
            select(func.min(models.Session.start_time))
        ).scalar()

        last_session_raw = self.db_session.execute(
            select(func.max(models.Session.start_time))
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

    def vacuum(self, db_path: str) -> VacuumResult:
        """Vacuum the database to reclaim space after deletions.

        SQLite-only operation.  Raises ``RuntimeError`` for non-SQLite targets.
        VACUUM requires AUTOCOMMIT on SQLite; it runs on a dedicated connection
        separate from the normal session pool so it cannot affect in-flight
        transactions.
        """
        if not db_path:
            raise RuntimeError(
                "VACUUM is a SQLite-only operation and requires a file-backed database. "
                "This operation is not available for in-memory or non-SQLite databases."
            )

        size_before = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )

        # VACUUM cannot run inside a transaction.  Use a dedicated AUTOCOMMIT
        # engine so the PRAGMA recipe on the pool does not interfere.
        vacuum_engine = create_engine(
            f"sqlite:///{db_path}",
            isolation_level="AUTOCOMMIT",
        )
        try:
            with vacuum_engine.connect() as conn:
                conn.execute(text("VACUUM"))
        finally:
            vacuum_engine.dispose()

        size_after = (
            os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0.0
        )

        return VacuumResult(
            status="success",
            size_before_mb=size_before,
            size_after_mb=size_after,
        )

    def reset(self, db_path: str) -> ResetResult:
        """Delete all rows from all data tables.

        Split into two capabilities:

        1. **Generic row reset** (any dialect): clears all user data rows in FK-safe
           order via typed ``table.delete()`` statements.  The caller's session
           commits the deletes.
        2. **SQLite file VACUUM** (SQLite targets only): runs on a separate AUTOCOMMIT
           connection after the delete commit.  Dispatched only when the target
           explicitly reports ``is_sqlite_target()`` — not via an empty-string
           implicit switch.  VACUUM cannot execute inside a transaction.

        Args:
            db_path: Path to the SQLite database file.  Required for VACUUM and
                     size measurement.  Ignored for non-SQLite targets (pass ``""``
                     to skip file operations).

        Note:
            To check whether VACUUM will run, callers should test
            ``DatabaseService.is_sqlite_target(db_path)`` before calling.
        """
        size_before = (
            os.path.getsize(db_path) / (1024 * 1024)
            if db_path and os.path.exists(db_path)
            else 0.0
        )

        # Generic phase: typed row deletion (any dialect).
        tables_cleared: dict[str, int] = {}
        total = 0
        for table in reversed(Base.metadata.sorted_tables):
            cursor = self.db_session.execute(table.delete())
            count = cursor.rowcount or 0  # type: ignore[attr-defined]
            tables_cleared[table.name] = count
            total += count

        # Commit deletes before VACUUM — SQLite forbids VACUUM in a transaction.
        # This is intentional: reset is a destructive, single-request operation
        # and the route dependency creates a fresh session per request.
        self.db_session.commit()

        # SQLite file maintenance phase: dispatched only for SQLite file targets.
        if self.is_sqlite_target(db_path):
            vacuum_engine = create_engine(
                f"sqlite:///{db_path}",
                isolation_level="AUTOCOMMIT",
            )
            try:
                with vacuum_engine.connect() as conn:
                    conn.execute(text("VACUUM"))
            finally:
                vacuum_engine.dispose()

        size_after = (
            os.path.getsize(db_path) / (1024 * 1024)
            if db_path and os.path.exists(db_path)
            else 0.0
        )

        return ResetResult(
            status="success",
            tables_cleared=tables_cleared,
            total_rows_deleted=total,
            size_before_mb=size_before,
            size_after_mb=size_after,
        )

    @staticmethod
    def is_sqlite_target(db_path: str) -> bool:
        """Return True if *db_path* identifies a SQLite file target.

        Non-empty, non-memory paths are SQLite file targets.  Empty strings
        and ``:memory:`` are treated as non-file targets.  This is the explicit
        capability gate that controls whether VACUUM and file-size measurements
        run — replaces the previous implicit ``if db_path:`` switch.
        """
        return bool(db_path) and db_path != ":memory:"
