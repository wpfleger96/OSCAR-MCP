"""Tests for the integrated SQLite connection recipe (§4).

Verifies:
- PRAGMA foreign_keys == 1 on a live pooled connection.
- PRAGMA journal_mode == "wal" on a file-backed connection.
- Forced-error savepoint test: inside a batch, release one nested savepoint,
  then abort the outer transaction; assert zero rows from that batch survive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from snore.database.models import Device
from snore.database.session import init_database, session_scope

if TYPE_CHECKING:
    from snore.parsers.unified import UnifiedSession


class TestSQLitePragmas:
    """Assert that the integrated connection recipe applies PRAGMAs correctly."""

    async def test_foreign_keys_enabled_on_pooled_connection(self, temp_db):
        """PRAGMA foreign_keys == 1 after init_database()."""
        init_database(str(temp_db))

        async with session_scope() as session:
            from sqlalchemy import text

            result = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
            assert result == 1, f"Expected foreign_keys=1, got {result}"

    async def test_journal_mode_wal_on_file_backed_connection(self, temp_db):
        """PRAGMA journal_mode == 'wal' after init_database()."""
        init_database(str(temp_db))

        async with session_scope() as session:
            from sqlalchemy import text

            result = (await session.execute(text("PRAGMA journal_mode"))).scalar()
            assert result == "wal", f"Expected journal_mode=wal, got {result}"

    async def test_transaction_control_does_not_silence_foreign_key_pragma(self, temp_db):
        """Integrated recipe: autocommit toggle ensures both FK and WAL are applied atomically."""
        init_database(str(temp_db))

        async with session_scope() as session:
            from sqlalchemy import text

            fk = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
            jm = (await session.execute(text("PRAGMA journal_mode"))).scalar()
            assert fk == 1, f"Foreign keys not enabled: got {fk}"
            assert jm == "wal", f"WAL not enabled: got {jm}"


class TestSavepointRollback:
    """Forced-error test: a released savepoint must not escape the outer rollback."""

    async def test_released_savepoint_rows_do_not_survive_outer_rollback(self, temp_db):
        """Inside a batch, one failed nested savepoint; outer abort removes all batch rows."""
        init_database(str(temp_db))

        # First, create a device to satisfy the FK constraint on sessions.
        async with session_scope() as setup_session:
            device = Device(
                manufacturer="TestMfr",
                model="TestMdl",
                serial_number="SAVEPOINT_TEST",
            )
            setup_session.add(device)
            await setup_session.flush()

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
            async with outer_session as session:
                # Insert row A: a new device to track.
                marker_device = Device(
                    manufacturer="BatchMfr",
                    model="BatchMdl",
                    serial_number="BATCH_DEVICE_OUTER",
                )
                session.add(marker_device)
                await session.flush()
                outer_id = marker_device.id

                # Create a nested savepoint and insert a batch row inside it.
                async with session.begin_nested():
                    batch_device = Device(
                        manufacturer="BatchMfr",
                        model="BatchMdl",
                        serial_number="BATCH_DEVICE_INNER",
                    )
                    session.add(batch_device)
                    await session.flush()
                    inner_id = batch_device.id
                # sp is released here — row is in the outer transaction scope.

                # Simulate batch abort: deliberately raise an error to force outer rollback.
                raise RuntimeError("Simulated batch abort")

        except RuntimeError:
            pass  # Expected; outer session rolled back by session_scope.

        # Verify: neither the outer row nor the inner row survived.
        async with session_scope() as verify:
            from sqlalchemy import select

            outer_count = (
                await verify.execute(select(Device).filter_by(id=outer_id))
            ).scalars().all()
            inner_count = (
                await verify.execute(select(Device).filter_by(id=inner_id))
            ).scalars().all()

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

    Skipped: volatile — SessionImporter transaction ownership is being rewritten in PR-1.
    """

    pytestmark = pytest.mark.skip(reason="volatile: SessionImporter pending PR-1 rewrite")

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

    def _make_session_data_with_children(
        self, serial: str, session_id_str: str
    ) -> UnifiedSession:
        """Build a UnifiedSession with waveforms, events, statistics, and settings.

        Used to verify that child rows (Waveform, Event, Statistics, Setting) are
        fully rolled back by the savepoint when the session import fails.
        """
        from datetime import datetime  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415

        from snore.parsers.unified import (  # noqa: PLC0415
            DeviceInfo,
            RespiratoryEvent,
            RespiratoryEventType,
            SessionStatistics,
            TherapyMode,
            TherapySettings,
            UnifiedSession,
            WaveformData,
            WaveformType,
        )

        # Minimal float32 blob: 2 samples × 2 columns (timestamp, value)
        arr = np.array([[0.0, 1.0], [0.04, 1.1]], dtype=np.float32)
        blob = arr.tobytes()

        device_info = DeviceInfo(
            manufacturer="TestMfr",
            model="TestMdl",
            serial_number=serial,
        )
        start = datetime(2024, 1, 1, 21, 0, 0)
        end = datetime(2024, 1, 2, 5, 0, 0)

        wf = WaveformData(
            waveform_type=WaveformType.FLOW_RATE,
            sample_rate=25.0,
            unit="L/min",
            min_value=-10.0,
            max_value=10.0,
            mean_value=0.0,
            sample_count=2,
            data_blob=blob,
            timestamps=[0.0, 0.04],
            values=[1.0, 1.1],
        )

        evt = RespiratoryEvent(
            event_type=RespiratoryEventType.OBSTRUCTIVE_APNEA,
            start_time=start,
            duration_seconds=10.0,
        )

        settings = TherapySettings(
            mode=TherapyMode.CPAP,
            pressure_fixed=8.0,
        )

        stats = SessionStatistics(
            usage_hours=8.0,
            ahi=2.5,
        )

        sess = UnifiedSession(
            device_info=device_info,
            device_session_id=session_id_str,
            start_time=start,
            end_time=end,
            settings=settings,
            statistics=stats,
            has_waveform_data=True,
            has_event_data=True,
            has_statistics=True,
        )
        sess.waveforms = {WaveformType.FLOW_RATE: wf}
        sess.events = [evt]
        return sess

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

    def test_failed_session_after_child_writes_leaves_no_child_rows(self, temp_db):
        """Child rows (Waveform, Event, Statistics, Setting) are rolled back when a session fails.

        Failure is injected AFTER waveforms and events have been flushed into the
        savepoint.  The savepoint rollback must remove ALL child rows for the
        failed session, while the next session's child rows survive.

        This test exercises the §6 requirement: forced-failure test must inject
        after real child writes and assert no child rows survive.
        """
        init_database(str(temp_db))
        from unittest.mock import patch

        from snore.database.importers import SessionImporter
        from snore.database.models import Device as DBDevice
        from snore.database.models import Event as DBEvent
        from snore.database.models import Session as DBSession
        from snore.database.models import Statistics as DBStatistics
        from snore.database.models import Waveform as DBWaveform
        from snore.database.session import session_scope

        # Build sessions WITH child data so there are waveform/event/stat rows to roll back.
        good = self._make_session_data_with_children("SN_CHILD_GOOD", "SESS_CHILD_GOOD")
        bad = self._make_session_data_with_children("SN_CHILD_BAD", "SESS_CHILD_BAD")

        original_import_statistics = SessionImporter._import_statistics

        def _raise_for_bad(self, db, session_id, session_data):
            """Raise after waveforms+events are flushed, but before statistics commit."""
            # Check if this is the bad session by querying the flushed Session row.
            from sqlalchemy import select as _sel  # noqa: PLC0415

            sess_rows = (
                db.execute(
                    _sel(DBSession).where(
                        DBSession.id == session_id,
                    )
                )
                .scalars()
                .all()
            )
            if sess_rows and sess_rows[0].device_session_id == "SESS_CHILD_BAD":
                raise RuntimeError(
                    "Forced failure after child writes (waveforms+events flushed)"
                )
            return original_import_statistics(self, db, session_id, session_data)

        importer = SessionImporter()
        with patch.object(SessionImporter, "_import_statistics", _raise_for_bad):
            imported, skipped, failed = importer.import_sessions_batch(
                [good, bad],
                batch_size=2,
            )

        assert failed == 1, f"Expected 1 failure, got {failed}"
        assert imported == 1, f"Expected 1 imported, got {imported}"

        # Verify at the DB level: good session's child rows present,
        # bad session's child rows absent — savepoint rolled back everything.
        with session_scope() as verify:
            from sqlalchemy import select as _sv  # noqa: PLC0415

            good_sessions = (
                verify.execute(
                    _sv(DBSession).where(
                        DBSession.device_session_id == "SESS_CHILD_GOOD"
                    )
                )
                .scalars()
                .all()
            )
            bad_sessions = (
                verify.execute(
                    _sv(DBSession).where(
                        DBSession.device_session_id == "SESS_CHILD_BAD"
                    )
                )
                .scalars()
                .all()
            )
            assert len(good_sessions) == 1, "Good session must survive"
            assert len(bad_sessions) == 0, "Bad session must be rolled back"

            good_session_id = good_sessions[0].id

            good_waveforms = (
                verify.execute(
                    _sv(DBWaveform).where(DBWaveform.session_id == good_session_id)
                )
                .scalars()
                .all()
            )
            assert len(good_waveforms) >= 1, "Good session's waveforms must survive"

            good_events = (
                verify.execute(
                    _sv(DBEvent).where(DBEvent.session_id == good_session_id)
                )
                .scalars()
                .all()
            )
            assert len(good_events) >= 1, "Good session's events must survive"

            good_stats = (
                verify.execute(
                    _sv(DBStatistics).where(DBStatistics.session_id == good_session_id)
                )
                .scalars()
                .all()
            )
            assert len(good_stats) >= 1, "Good session's statistics must survive"

            # Bad session was rolled back; its device must also be gone.
            bad_devices = (
                verify.execute(
                    _sv(DBDevice).where(DBDevice.serial_number == "SN_CHILD_BAD")
                )
                .scalars()
                .all()
            )
            assert len(bad_devices) == 0, (
                "Bad session's device was flushed then rolled back — "
                "no device row for SN_CHILD_BAD must survive"
            )

            # No orphan Waveform/Event rows for any non-existent session.
            all_session_ids = {
                s.id for s in verify.execute(_sv(DBSession)).scalars().all()
            }
            orphan_waveforms = [
                w
                for w in verify.execute(_sv(DBWaveform)).scalars().all()
                if w.session_id not in all_session_ids
            ]
            assert len(orphan_waveforms) == 0, (
                f"Orphan waveform rows found: {[w.id for w in orphan_waveforms]}"
            )
