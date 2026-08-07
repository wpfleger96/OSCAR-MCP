"""Database service for database statistics and metadata operations."""

import logging
import os

from datetime import datetime
from pathlib import Path

from sqlalchemy import ColumnElement, create_engine, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.models import Base
from snore.services.schemas import DatabaseStats, VacuumResult

__all__ = ["DatabaseService", "_vacuum_background"]

logger = logging.getLogger(__name__)


def _vacuum_background(db_path: str) -> None:
    """Run VACUUM on a SQLite file path; intended for BackgroundTasks scheduling.

    FastAPI runs sync background-task functions in a thread pool, so this never
    blocks the event loop. No-op when db_path is not a valid SQLite file target.

    Uses the Python ``sqlite3`` module directly (no SQLAlchemy pool) with
    ``isolation_level=None`` (autocommit) so each statement runs outside any
    transaction.  VACUUM requires exclusive access; if any reader holds a lock,
    the VACUUM fails immediately and the failure is logged as a warning.  This
    is non-fatal — the DB is simply not compacted until the next scheduled run
    (e.g. after the next reset, or on the next server start with a VACUUM cron).
    """
    import sqlite3  # noqa: PLC0415

    if not DatabaseService.is_sqlite_target(db_path):
        logger.debug("_vacuum_background: skipping non-SQLite target %r", db_path)
        return
    conn: sqlite3.Connection | None = None
    try:
        # isolation_level=None → SQLite autocommit; VACUUM must run outside
        # an explicit transaction.  No SQLAlchemy pool, so the connection is
        # opened, used, and closed in a single call path.
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("VACUUM")
        logger.info("Background VACUUM completed for %s", db_path)
    except Exception as exc:
        logger.warning("Background VACUUM failed for %s (non-fatal): %s", db_path, exc)
    finally:
        if conn is not None:
            conn.close()


class DatabaseService:
    """Service for database statistics and metadata operations."""

    def __init__(self, db_session: AsyncSession, profile_id: int):
        """
        Initialize database service.

        Args:
            db_session: SQLAlchemy database session
            profile_id: Active profile — CPAP data counts are scoped to this profile.
        """
        self.db_session = db_session
        self.profile_id = profile_id

    def _profile_filter(self) -> ColumnElement[bool]:
        """WHERE predicate: limit CPAP data to this profile via device ownership."""
        return models.Device.profile_id == self.profile_id

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
                select(func.count())
                .select_from(models.Device)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        session_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        day_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Day)
                .join(models.Device, models.Day.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        event_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Event)
                .join(models.Session, models.Event.session_id == models.Session.id)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        waveform_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Waveform)
                .join(models.Session, models.Waveform.session_id == models.Session.id)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        analysis_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.AnalysisResult)
                .join(
                    models.Session,
                    models.AnalysisResult.session_id == models.Session.id,
                )
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        pattern_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.DetectedPattern)
                .join(
                    models.AnalysisResult,
                    models.DetectedPattern.analysis_result_id
                    == models.AnalysisResult.id,
                )
                .join(
                    models.Session,
                    models.AnalysisResult.session_id == models.Session.id,
                )
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0

        sessions_with_waveforms = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    self._profile_filter(),
                    models.Session.has_waveform_data.is_(True),
                )
            )
        ).scalar() or 0
        sessions_with_events = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    self._profile_filter(),
                    models.Session.has_event_data.is_(True),
                )
            )
        ).scalar() or 0
        # Distinct sessions that have at least one analysis result (re-analysis of the
        # same session produces multiple rows; COUNT(DISTINCT) prevents over-counting).
        sessions_with_analysis = (
            await self.db_session.execute(
                select(func.count(func.distinct(models.AnalysisResult.session_id)))
                .select_from(models.AnalysisResult)
                .join(
                    models.Session,
                    models.AnalysisResult.session_id == models.Session.id,
                )
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar() or 0
        # Sessions that have a flow waveform — the only sessions analysis can run on.
        # Uses Session.has_waveform_data=True only flags *any* waveform type, so we
        # join Waveform directly and filter on waveform_type == "flow".
        analyzable_session_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .join(
                    models.Waveform,
                    (models.Waveform.session_id == models.Session.id)
                    & (models.Waveform.waveform_type == "flow"),
                )
                .where(self._profile_filter())
            )
        ).scalar() or 0

        first_session_raw = (
            await self.db_session.execute(
                select(func.min(models.Session.start_time))
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
        ).scalar()

        last_session_raw = (
            await self.db_session.execute(
                select(func.max(models.Session.start_time))
                .select_from(models.Session)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(self._profile_filter())
            )
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
        # Coverage = analyzed / analyzable (sessions with a flow waveform), not
        # total analysis rows / total sessions.  This stays in [0, 100] after
        # re-analysis and correctly excludes summary-only imports from the denominator.
        analysis_coverage_pct = (
            (sessions_with_analysis / analyzable_session_count * 100)
            if analyzable_session_count > 0
            else 0
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
            sessions_with_analysis=sessions_with_analysis,
            analyzable_session_count=analyzable_session_count,
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
    async def delete_user_data(
        db: AsyncSession,
        user_id: int,
        raw_root: Path,
    ) -> tuple[int, int, int]:
        """Delete all sleep data owned by *user_id*; commit; purge raw dirs.

        Deletes Device rows for every live profile owned by the user — the DB
        cascades (Device → Session/Day/Waveform/Event/Statistics/Setting/
        AnalysisResult/Breath/DetectedPattern) handle the rest.  Session has two
        FK paths to Day (via sessions.device_id and sessions.day_id); both
        cascades fire but are benign — by the time SQLite processes the Day-FK
        cascade, the Session row is already gone via the Device-FK cascade.

        Import job records (no FK, explicit delete) are also removed.

        NOTE: Concurrent in-flight imports for this user's devices will fail if
        their device rows are deleted mid-import; this is accepted behavior.

        Commits the transaction.  Caller is responsible for scheduling vacuum.

        Returns:
            (devices_deleted, import_jobs_deleted, profiles_processed) counts.
        """
        from snore.services.profile_service import (
            purge_profile_raw_dir,  # noqa: PLC0415
        )

        profile_ids = list(
            (
                await db.execute(
                    select(models.Profile.id).where(
                        models.Profile.user_id == user_id,
                        models.Profile.deleting_at.is_(None),
                    )
                )
            ).scalars()
        )

        # Device rows per profile — RETURNING is bounded by a small device count.
        devices_deleted = 0
        for profile_id in profile_ids:
            result = await db.execute(
                delete(models.Device)
                .where(models.Device.profile_id == profile_id)
                .returning(models.Device.id)
            )
            devices_deleted += len(result.scalars().all())

        # Import records are potentially unbounded — use rowcount.
        import_cursor = await db.execute(
            delete(models.ImportJobRecord).where(
                models.ImportJobRecord.owner_user_id == user_id
            )
        )
        import_jobs_deleted = import_cursor.rowcount or 0  # type: ignore[attr-defined]

        await db.commit()

        for profile_id in profile_ids:
            purge_profile_raw_dir(profile_id, raw_root, label="delete-data")

        return devices_deleted, import_jobs_deleted, len(profile_ids)

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
