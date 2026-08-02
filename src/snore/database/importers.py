"""
Session import functionality for converting UnifiedSession to database records.

Handles the complete import process including waveforms, events, and statistics.

Transaction ownership (§6)
---------------------------
- ``import_session``: delegates to ``import_sessions_batch`` with a single-element
  list and an explicit ``session_scope()`` — same UoW pattern as the batch path.
- ``import_sessions_batch``: requires a caller-provided ``db`` session; uses only
  ``begin_nested()`` savepoints so one failed session cannot poison the batch.
  The caller (``ImportService``) opens one ``session_scope()`` per bounded chunk
  and injects it here so the UoW boundary is owned at the service layer.

Bulk strategy (frozen in §90/PR-2)
------------------------------------
Waveforms, events, and settings use ``await db.execute(insert(Model), mappings)``
inside the per-session ``begin_nested()`` savepoint.  Core INSERT rows (Device,
Session, Day, Statistics) use ``add()`` / ``add_all()`` because they are small in
number, require identity-map access after flush, and benefit from ORM-level
change tracking.
"""

import itertools
import json
import logging

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

import numpy as np

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database import models
from snore.database.day_manager import DayManager
from snore.database.session import session_scope
from snore.parsers.unified import UnifiedSession, WaveformData

logger = logging.getLogger(__name__)


def serialize_waveform(waveform: WaveformData) -> bytes:
    """
    Serialize waveform data to bytes for database storage.

    Stores timestamps and values as float32 numpy arrays.
    No compression - SQLite and filesystem handle that efficiently.

    Args:
        waveform: WaveformData object

    Returns:
        Serialized bytes
    """
    if isinstance(waveform.timestamps, list):
        timestamps = np.array(waveform.timestamps, dtype=np.float32)
    else:
        timestamps = waveform.timestamps.astype(np.float32)

    if isinstance(waveform.values, list):
        values = np.array(waveform.values, dtype=np.float32)
    else:
        values = waveform.values.astype(np.float32)

    data = np.column_stack([timestamps, values])
    return data.tobytes()


class SessionImporter:
    """Handles importing UnifiedSession objects to database using SQLAlchemy."""

    def __init__(self) -> None:
        """Initialize importer."""
        pass

    @staticmethod
    async def cleanup_orphaned_records(db: AsyncSession) -> int:
        """
        Remove orphaned records from child tables that reference non-existent sessions.

        This can happen if CASCADE delete is not enabled or if a database was corrupted.

        Uses typed ``delete()`` statements keyed on ``session_id`` FK — no f-string
        SQL, no internal commit (the caller's transaction owns commit/rollback).

        Args:
            db: SQLAlchemy async session (caller owns the transaction)

        Returns:
            Number of orphaned records removed
        """
        orphan_tables = [
            models.Setting,
            models.Event,
            models.Waveform,
            models.Statistics,
        ]
        total_cleaned = 0

        for model_cls in orphan_tables:
            stmt = delete(model_cls).where(
                model_cls.session_id.notin_(  # type: ignore[attr-defined]
                    select(models.Session.id)
                )
            )
            result = await db.execute(stmt)
            count = result.rowcount if hasattr(result, "rowcount") else 0
            if count > 0:
                logger.debug(
                    f"Cleaned {count} orphaned records from {model_cls.__tablename__}"
                )
                total_cleaned += count

        # No db.commit() — caller owns the transaction boundary.
        return total_cleaned

    async def _import_single_session(
        self,
        db: AsyncSession,
        session_data: UnifiedSession,
        force: bool = False,
        *,
        profile_id: int | None = None,
    ) -> tuple[bool, int | None]:
        """
        Import a single session. Returns (imported, day_id).

        This method NEVER aggregates - aggregation is handled by the caller.
        It does NOT open its own savepoint; callers that want per-session
        isolation must wrap each call in ``db.begin_nested()``.

        Args:
            db: SQLAlchemy async database session
            session_data: UnifiedSession to import
            force: If True, re-import existing sessions

        Returns:
            Tuple of (was_imported, day_id)
        """
        stmt = select(models.Device).filter_by(
            serial_number=session_data.device_info.serial_number
        )
        device = (await db.execute(stmt)).scalars().first()

        if device:
            device.manufacturer = session_data.device_info.manufacturer
            device.model = session_data.device_info.model
            device.firmware_version = session_data.device_info.firmware_version
            device.hardware_version = session_data.device_info.hardware_version
            device.product_code = session_data.device_info.product_code
            device.last_import = datetime.now(UTC)
        else:
            device = models.Device(
                profile_id=profile_id,
                manufacturer=session_data.device_info.manufacturer,
                model=session_data.device_info.model,
                serial_number=session_data.device_info.serial_number,
                firmware_version=session_data.device_info.firmware_version,
                hardware_version=session_data.device_info.hardware_version,
                product_code=session_data.device_info.product_code,
            )
            db.add(device)
            await db.flush()

        existing_stmt = select(models.Session).filter_by(
            device_id=device.id,
            device_session_id=session_data.device_session_id,
        )
        existing = (await db.execute(existing_stmt)).scalars().first()

        if existing and not force:
            logger.debug(
                f"Session {session_data.device_session_id} already exists, skipping"
            )
            return False, None

        if existing and force:
            logger.debug(f"Force re-importing session {session_data.device_session_id}")
            await db.delete(existing)
            await db.flush()

        notes_json = (
            json.dumps(session_data.data_quality_notes)
            if session_data.data_quality_notes
            else None
        )

        new_session = models.Session(
            device_id=device.id,
            device_session_id=session_data.device_session_id,
            start_time=session_data.start_time,
            end_time=session_data.end_time,
            duration_seconds=session_data.duration_seconds,
            therapy_mode=session_data.settings.mode.value
            if session_data.settings
            else None,
            import_source=session_data.import_source,
            parser_version=session_data.parser_version,
            data_quality_notes=notes_json,
            has_waveform_data=session_data.has_waveform_data,
            has_event_data=session_data.has_event_data,
            has_statistics=session_data.has_statistics,
        )
        db.add(new_session)
        await db.flush()

        day_date = DayManager.get_day_for_session(session_data.start_time)
        day = await DayManager.get_or_create_day(device.id, day_date, db)
        new_session.day_id = day.id
        day_id = day.id

        if session_data.has_waveform_data:
            await self._import_waveforms(db, new_session.id, session_data)

        if session_data.has_event_data:
            await self._import_events(db, new_session.id, session_data)

        if session_data.has_statistics:
            self._import_statistics(db, new_session.id, session_data)

        if session_data.settings:
            await self._import_settings(db, new_session.id, session_data)

        logger.debug(
            f"Imported session {session_data.device_session_id} from {session_data.start_time}"
        )
        return True, day_id

    async def import_session(
        self,
        session_data: UnifiedSession,
        force: bool = False,
        *,
        db: AsyncSession,
        profile_id: int | None = None,
    ) -> bool:
        """Import a complete session using a caller-provided async session.

        The caller owns the UoW — this method uses only ``begin_nested()``
        savepoints via ``import_sessions_batch``.  No ``session_scope()`` is
        opened here; use the module-level ``import_session`` function for
        standalone imports with automatic scope ownership.

        Args:
            session_data: UnifiedSession to import
            force: If True, re-import existing sessions
            db: Required caller-provided async session.
            profile_id: Profile that owns the device; required by the multiuser schema.

        Returns:
            True if imported, False if skipped (already exists)
        """
        imported, _skipped, _failed = await self.import_sessions_batch(
            [session_data],
            force=force,
            batch_size=1,
            db=db,
            profile_id=profile_id,
        )
        return imported > 0

    async def import_sessions_batch(
        self,
        sessions: Iterable[UnifiedSession],
        force: bool = False,
        batch_size: int = 50,
        progress_callback: Callable[[str], None] | None = None,
        cancel_predicate: Callable[[], bool] | None = None,
        *,
        db: AsyncSession,
        profile_id: int | None = None,
    ) -> tuple[int, int, int]:
        """
        Import multiple sessions in batched transactions with per-session savepoints.

        Each session import is wrapped in ``begin_nested()`` so a single failed session
        releases its savepoint and increments ``failed`` without poisoning the rest of
        the batch.  Day statistics are aggregated at the end of each batch.

        Checks ``cancel_predicate()`` between batches.  If cancellation is requested
        the loop exits early; partial results accumulated so far are returned.

        ``sessions`` may be any iterable (list or lazy iterator); the method consumes
        it in bounded chunks of ``batch_size`` so no full-batch prefetch is required.

        Transaction ownership: the CALLER owns the transaction.  This method uses
        ``begin_nested()`` savepoints only.  The production caller is ``ImportService``,
        which opens one ``session_scope()`` per bounded chunk and injects the session
        here so the UoW boundary is explicit and owned at the service layer.

        For standalone one-session imports, use ``import_session()`` which opens its
        own explicit service-owned scope.

        Args:
            sessions: Iterable of UnifiedSession objects to import.  Need not be
                a list; a lazy generator is consumed in bounded ``batch_size`` chunks.
            force: If True, re-import existing sessions
            batch_size: Number of sessions per transaction (default: 50)
            progress_callback: Optional callback for progress messages
            cancel_predicate: Optional callable; returns True when cancellation
                is requested.  Checked between batches (not mid-batch).
            db: Required injected SQLAlchemy session.  The caller owns
                commit/rollback of the outer transaction.

        Returns:
            Tuple of (imported_count, skipped_count, failed_count)
        """
        imported = 0
        skipped = 0
        failed = 0

        session_iter = iter(sessions)

        for batch_num in itertools.count(1):
            # Check cancellation between batches.
            if cancel_predicate is not None and cancel_predicate():
                break

            batch = list(itertools.islice(session_iter, batch_size))
            if not batch:
                break

            batch_day_ids: set[int] = set()

            logger.debug(f"Importing batch {batch_num} ({len(batch)} sessions)")

            # Caller-owned transaction: use savepoints only.
            (
                imported,
                skipped,
                failed,
                batch_day_ids,
            ) = await self._import_batch_with_session(
                db,
                batch,
                force,
                imported,
                skipped,
                failed,
                batch_day_ids,
                progress_callback,
                profile_id=profile_id,
            )
            if batch_day_ids:
                for day_id in batch_day_ids:
                    day_record = await db.get(models.Day, day_id)
                    if day_record:
                        await DayManager._aggregate_day_statistics(day_record, db)

        return imported, skipped, failed

    async def _import_batch_with_session(
        self,
        db: AsyncSession,
        batch: list[UnifiedSession],
        force: bool,
        imported: int,
        skipped: int,
        failed: int,
        batch_day_ids: set[int],
        progress_callback: Callable[[str], None] | None,
        *,
        profile_id: int | None = None,
    ) -> tuple[int, int, int, set[int]]:
        """Import one batch of sessions within a caller-provided session.

        Returns updated (imported, skipped, failed, batch_day_ids).
        """
        for session_data in batch:
            try:
                async with db.begin_nested():
                    was_imported, day_id = await self._import_single_session(
                        db, session_data, force, profile_id=profile_id
                    )
                if was_imported:
                    imported += 1
                    if day_id:
                        batch_day_ids.add(day_id)
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    f"Failed to import session {session_data.device_session_id}: {e}"
                )
                failed += 1

            if progress_callback:
                sessions_done = imported + skipped + failed
                progress_callback(f"Importing session {sessions_done}...")

        return imported, skipped, failed, batch_day_ids

    async def _import_waveforms(
        self, db: AsyncSession, session_id: int, session_data: UnifiedSession
    ) -> None:
        """Import all waveforms for session using typed bulk INSERT."""
        if not session_data.waveforms:
            return

        mappings = []
        for waveform_type, waveform in session_data.waveforms.items():
            data_blob = serialize_waveform(waveform)
            sample_count = (
                len(waveform.values)
                if isinstance(waveform.values, list)
                else len(waveform.values)
            )
            mappings.append(
                {
                    "session_id": session_id,
                    "waveform_type": waveform_type.value,
                    "sample_rate": waveform.sample_rate,
                    "unit": waveform.unit,
                    "min_value": waveform.min_value,
                    "max_value": waveform.max_value,
                    "mean_value": waveform.mean_value,
                    "data_blob": data_blob,
                    "sample_count": sample_count,
                }
            )

        if mappings:
            await db.execute(insert(models.Waveform), mappings)
        logger.debug(f"Bulk imported {len(mappings)} waveforms")

    async def _import_events(
        self, db: AsyncSession, session_id: int, session_data: UnifiedSession
    ) -> None:
        """Import all respiratory events for session using typed bulk INSERT."""
        if not session_data.events:
            return

        mappings = [
            {
                "session_id": session_id,
                "event_type": event.event_type.value,
                "start_time": event.start_time,
                "duration_seconds": event.duration_seconds,
                "spo2_drop": event.spo2_drop,
                "peak_flow_limitation": event.peak_flow_limitation,
            }
            for event in session_data.events
        ]
        if mappings:
            await db.execute(insert(models.Event), mappings)

        logger.debug(f"Bulk imported {len(mappings)} events")

    def _import_statistics(
        self, db: AsyncSession, session_id: int, session_data: UnifiedSession
    ) -> None:
        """Import session statistics."""
        stats = session_data.statistics

        stats_record = models.Statistics(session_id=session_id, **stats.model_dump())
        db.add(stats_record)

        logger.debug("Imported session statistics")

    async def _import_settings(
        self, db: AsyncSession, session_id: int, session_data: UnifiedSession
    ) -> None:
        """Import session settings using typed bulk INSERT."""
        settings = session_data.settings

        if not settings:
            return

        settings_dict: dict[str, object] = settings.model_dump(
            mode="json", exclude={"other_settings"}, exclude_none=True
        )

        if settings.other_settings:
            settings_dict.update(settings.other_settings)

        # exclude_none only covers the main model fields; other_settings is
        # merged in afterward, so guard against None here to avoid persisting
        # the literal string "None". Floats are rounded to strip EDF
        # gain-arithmetic noise (28.900000000000002 → 28.9).
        mappings = [
            {
                "session_id": session_id,
                "key": key,
                "value": str(round(value, 2))
                if isinstance(value, float)
                else str(value),
            }
            for key, value in settings_dict.items()
            if value is not None
        ]

        if mappings:
            await db.execute(insert(models.Setting), mappings)

        logger.debug(f"Imported {len(mappings)} settings")


async def import_session(
    session_data: UnifiedSession, force: bool = False, *, profile_id: int | None = None
) -> bool:
    """
    Convenience function to import a session.

    Opens an explicit ``session_scope()`` (service-layer UoW ownership) and
    delegates to ``SessionImporter.import_session``.  The importer itself
    does not open any scopes.

    Args:
        session_data: UnifiedSession to import
        force: Force re-import if exists
        profile_id: Profile that owns the device; required by the multiuser schema.

    Returns:
        True if imported, False if skipped
    """
    importer = SessionImporter()
    async with session_scope() as db:
        return await importer.import_session(
            session_data, force=force, db=db, profile_id=profile_id
        )
