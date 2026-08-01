"""Tests for the integrated SQLite connection recipe (§4).

Verifies:
- PRAGMA foreign_keys == 1 on a live pooled connection.
- PRAGMA journal_mode == "wal" on a file-backed connection.
- Forced-error savepoint test: inside a batch, release one nested savepoint,
  then abort the outer transaction; assert zero rows from that batch survive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from snore.database.models import Device
from snore.database.session import init_database, session_scope

if TYPE_CHECKING:
    from snore.parsers.unified import UnifiedSession


class TestSQLitePragmas:
    """Assert that the integrated connection recipe applies PRAGMAs correctly."""

    def test_foreign_keys_enabled_on_pooled_connection(self, temp_db):
        """PRAGMA foreign_keys == 1 after init_database()."""
        init_database(str(temp_db))

        with session_scope() as session:
            from sqlalchemy import text

            result = session.execute(text("PRAGMA foreign_keys")).scalar()
            assert result == 1, f"Expected foreign_keys=1, got {result}"

    def test_journal_mode_wal_on_file_backed_connection(self, temp_db):
        """PRAGMA journal_mode == 'wal' after init_database()."""
        init_database(str(temp_db))

        with session_scope() as session:
            from sqlalchemy import text

            result = session.execute(text("PRAGMA journal_mode")).scalar()
            assert result == "wal", f"Expected journal_mode=wal, got {result}"

    def test_transaction_control_does_not_silence_foreign_key_pragma(self, temp_db):
        """Integrated recipe: autocommit toggle ensures both FK and WAL are applied atomically."""
        init_database(str(temp_db))

        with session_scope() as session:
            from sqlalchemy import text

            fk = session.execute(text("PRAGMA foreign_keys")).scalar()
            jm = session.execute(text("PRAGMA journal_mode")).scalar()
            assert fk == 1, f"Foreign keys not enabled: got {fk}"
            assert jm == "wal", f"WAL not enabled: got {jm}"


class TestSavepointRollback:
    """Forced-error test: a released savepoint must not escape the outer rollback."""

    def test_released_savepoint_rows_do_not_survive_outer_rollback(self, temp_db):
        """Inside a batch, one failed nested savepoint; outer abort removes all batch rows."""
        init_database(str(temp_db))

        # First, create a device to satisfy the FK constraint on sessions.
        with session_scope() as setup_session:
            device = Device(
                manufacturer="TestMfr",
                model="TestMdl",
                serial_number="SAVEPOINT_TEST",
            )
            setup_session.add(device)
            setup_session.flush()

        # The forced-error test:
        # 1. Start an outer session (outer transaction).
        # 2. Insert row A into Device (harmless row).
        # 3. Begin savepoint SP1; insert row B (batch row).
        # 4. Release SP1 (savepoint committed to outer transaction scope).
        # 5. Abort the outer transaction via rollback.
        # 6. Assert row B is gone (outer rollback removed the batch).
        #
        # This proves the savepoint is nested inside the outer transaction,
        # not independent of it.

        outer_session = session_scope()
        try:
            with outer_session as session:
                # Insert row A: a new device to track.
                marker_device = Device(
                    manufacturer="BatchMfr",
                    model="BatchMdl",
                    serial_number="BATCH_DEVICE_OUTER",
                )
                session.add(marker_device)
                session.flush()
                outer_id = marker_device.id

                # Create a nested savepoint and insert a batch row inside it.
                with session.begin_nested():
                    batch_device = Device(
                        manufacturer="BatchMfr",
                        model="BatchMdl",
                        serial_number="BATCH_DEVICE_INNER",
                    )
                    session.add(batch_device)
                    session.flush()
                    inner_id = batch_device.id
                # sp is released here — row is in the outer transaction scope.

                # Simulate batch abort: deliberately raise an error to force outer rollback.
                raise RuntimeError("Simulated batch abort")

        except RuntimeError:
            pass  # Expected; outer session rolled back by session_scope.

        # Verify: neither the outer row nor the inner row survived.
        with session_scope() as verify:
            from sqlalchemy import select

            outer_count = (
                verify.execute(select(Device).filter_by(id=outer_id)).scalars().all()
            )
            inner_count = (
                verify.execute(select(Device).filter_by(id=inner_id)).scalars().all()
            )

        assert len(outer_count) == 0, (
            "Outer batch row survived rollback — savepoint escaped outer transaction"
        )
        assert len(inner_count) == 0, (
            "Inner savepoint row survived rollback — savepoint was independent"
        )


# ---------------------------------------------------------------------------
# Importer-level forced-failure / continuation test (§6)
# ---------------------------------------------------------------------------


class TestImporterForcedFailureContinuation:
    """Force a session import to fail mid-batch; subsequent sessions must succeed.

    This is the spec §6 importer-level test: when ``_import_single_session`` raises
    **after partial rows have been flushed**, the failing session's rows (device +
    session) are rolled back by the savepoint, and the next session in the batch
    imports successfully.

    Failure injection point: ``DayManager.get_or_create_day`` is patched to raise
    for the bad session.  By the time this is called, ``db.flush()`` has already
    been called for both the Device and Session rows — so the savepoint rollback
    is meaningful (proves partial rows do not survive).
    """

    def _make_session_data(self, serial: str, session_id_str: str) -> UnifiedSession:
        """Build a minimal UnifiedSession with no optional data."""
        from datetime import datetime

        from snore.parsers.unified import DeviceInfo, UnifiedSession

        device_info = DeviceInfo(
            manufacturer="TestMfr",
            model="TestMdl",
            serial_number=serial,
        )
        start = datetime(2024, 1, 1, 21, 0, 0)
        end = datetime(2024, 1, 2, 5, 0, 0)  # 8h session
        return UnifiedSession(
            device_info=device_info,
            device_session_id=session_id_str,
            start_time=start,
            end_time=end,
        )

    def test_failed_session_after_partial_flush_leaves_no_rows_next_session_succeeds(
        self, temp_db
    ):
        """Session failing AFTER partial rows are flushed leaves zero rows; next session commits.

        Failure is injected at ``DayManager.get_or_create_day`` — after both Device
        and Session rows have been flushed into the savepoint.  The savepoint is
        rolled back, leaving no device/day/session rows for the bad session.
        The subsequent good session must then import successfully.
        """
        init_database(str(temp_db))
        from unittest.mock import patch

        from snore.database.day_manager import DayManager
        from snore.database.importers import SessionImporter
        from snore.database.models import Device as DBDevice
        from snore.database.models import Session as DBSession
        from snore.database.session import session_scope

        good1 = self._make_session_data("SN_GOOD1_PF", "SESS_GOOD1_PF")
        bad_session = self._make_session_data("SN_BAD_PF", "SESS_BAD_PF")
        good2 = self._make_session_data("SN_GOOD2_PF", "SESS_GOOD2_PF")

        original_get_or_create = DayManager.get_or_create_day

        def patched_day(device_id, day_date, db_session):
            # Identify the bad session by querying the already-flushed Session row.
            # The flush has fired for Device + Session at this point, so these
            # rows exist inside the savepoint and can be seen by this query.
            from sqlalchemy import select as _select  # noqa: PLC0415

            pending_sessions = (
                db_session.execute(
                    _select(DBSession).where(
                        DBSession.device_session_id == "SESS_BAD_PF",
                        DBSession.id.isnot(None),
                    )
                )
                .scalars()
                .all()
            )
            if pending_sessions:
                raise RuntimeError("Forced mid-import failure after partial flush")
            return original_get_or_create(device_id, day_date, db_session)

        importer = SessionImporter()
        with patch.object(DayManager, "get_or_create_day", patched_day):
            imported, skipped, failed = importer.import_sessions_batch(
                [good1, bad_session, good2],
                batch_size=3,
            )

        assert failed == 1, f"Expected 1 failure, got {failed}"
        assert imported == 2, f"Expected 2 imported, got {imported}"
        assert skipped == 0, f"Expected 0 skipped, got {skipped}"

        # Verify at the DB level: good sessions present, bad session and its
        # device absent — the savepoint rolled back ALL rows flushed for bad.
        with session_scope() as verify:
            from sqlalchemy import select as _sv_select  # noqa: PLC0415

            sessions = verify.execute(_sv_select(DBSession)).scalars().all()
            session_ids = {s.device_session_id for s in sessions}
            device_serials = {
                d.serial_number
                for d in verify.execute(_sv_select(DBDevice)).scalars().all()
            }

        assert "SESS_GOOD1_PF" in session_ids, "SESS_GOOD1_PF should be in DB"
        assert "SESS_GOOD2_PF" in session_ids, "SESS_GOOD2_PF should be in DB"
        assert "SESS_BAD_PF" not in session_ids, (
            "SESS_BAD_PF was rolled back via savepoint — session row must not survive"
        )
        assert "SN_BAD_PF" not in device_serials, (
            "SN_BAD_PF device was flushed then rolled back via savepoint — "
            "device row must not survive"
        )
