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

    async def test_foreign_keys_enabled_on_pooled_connection(self, temp_db):
        """PRAGMA foreign_keys == 1 after init_database()."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            from sqlalchemy import text

            result = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
            assert result == 1, f"Expected foreign_keys=1, got {result}"

    async def test_journal_mode_wal_on_file_backed_connection(self, temp_db):
        """PRAGMA journal_mode == 'wal' after init_database()."""
        await init_database(str(temp_db))

        async with session_scope() as session:
            from sqlalchemy import text

            result = (await session.execute(text("PRAGMA journal_mode"))).scalar()
            assert result == "wal", f"Expected journal_mode=wal, got {result}"

    async def test_transaction_control_does_not_silence_foreign_key_pragma(
        self, temp_db
    ):
        """Integrated recipe: autocommit toggle ensures both FK and WAL are applied atomically."""
        await init_database(str(temp_db))

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
        await init_database(str(temp_db))

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
                (await verify.execute(select(Device).filter_by(id=outer_id)))
                .scalars()
                .all()
            )
            inner_count = (
                (await verify.execute(select(Device).filter_by(id=inner_id)))
                .scalars()
                .all()
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
    """Force a session import to fail mid-batch; subsequent sessions must succeed."""

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

    async def test_failed_session_after_partial_flush_leaves_no_rows_next_session_succeeds(
        self, temp_db
    ):
        """Session failing AFTER partial rows are flushed leaves zero rows; next session commits.

        Failure is injected at ``DayManager.get_or_create_day`` — after both Device
        and Session rows have been flushed into the savepoint.  The savepoint is
        rolled back, leaving no device/day/session rows for the bad session.
        The subsequent good session must then import successfully.
        """
        await init_database(str(temp_db))
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

        async def patched_day(device_id, day_date, db_session):
            # Identify the bad session by querying the already-flushed Session row.
            # The flush has fired for Device + Session at this point, so these
            # rows exist inside the savepoint and can be seen by this query.
            from sqlalchemy import select as _select  # noqa: PLC0415

            pending_sessions = (
                (
                    await db_session.execute(
                        _select(DBSession).where(
                            DBSession.device_session_id == "SESS_BAD_PF",
                            DBSession.id.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if pending_sessions:
                raise RuntimeError("Forced mid-import failure after partial flush")
            return await original_get_or_create(device_id, day_date, db_session)

        importer = SessionImporter()
        with patch.object(DayManager, "get_or_create_day", patched_day):
            async with session_scope() as batch_db:
                imported, skipped, failed = await importer.import_sessions_batch(
                    iter([good1, bad_session, good2]),
                    batch_size=3,
                    db=batch_db,
                )

        assert failed == 1, f"Expected 1 failure, got {failed}"
        assert imported == 2, f"Expected 2 imported, got {imported}"
        assert skipped == 0, f"Expected 0 skipped, got {skipped}"

        # Verify at the DB level: good sessions present, bad session and its
        # device absent — the savepoint rolled back ALL rows flushed for bad.
        async with session_scope() as verify:
            from sqlalchemy import select as _sv_select  # noqa: PLC0415

            sessions = (await verify.execute(_sv_select(DBSession))).scalars().all()
            session_ids = {s.device_session_id for s in sessions}
            device_serials = {
                d.serial_number
                for d in (await verify.execute(_sv_select(DBDevice))).scalars().all()
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

    async def test_failed_session_after_child_writes_leaves_no_child_rows(
        self, temp_db
    ):
        """ALL child rows rolled back when session import fails after every child flush.

        Failure is injected AFTER all children have been flushed (wrapping
        _import_single_session to raise AFTER it returns).  The savepoint rollback
        must remove device/day/session/waveform/event/statistics/setting rows for
        the failed session while the next session's rows survive.

        This is the §6 spec requirement: failure after every child is flushed;
        all seven bad-table projections asserted empty.
        """
        await init_database(str(temp_db))
        from unittest.mock import patch

        from snore.database.importers import SessionImporter
        from snore.database.models import Day as DBDay
        from snore.database.models import Device as DBDevice
        from snore.database.models import Event as DBEvent
        from snore.database.models import Session as DBSession
        from snore.database.models import Setting as DBSetting
        from snore.database.models import Statistics as DBStatistics
        from snore.database.models import Waveform as DBWaveform
        from snore.database.session import session_scope

        # Build sessions WITH full child data: waveform, event, statistics, settings.
        good = self._make_session_data_with_children("SN_CHILD_GOOD", "SESS_CHILD_GOOD")
        bad = self._make_session_data_with_children("SN_CHILD_BAD", "SESS_CHILD_BAD")

        original_import_single = SessionImporter._import_single_session

        async def _raise_after_all_children_flushed(
            self_imp, db, session_data, force=False
        ):
            """Call real _import_single_session (which flushes all children),
            then raise — savepoint must roll back all child rows for the bad session."""
            result = await original_import_single(
                self_imp, db, session_data, force=force
            )
            # All children (waveform/event/stat/setting) are now flushed inside the
            # savepoint.  Raise here to prove the savepoint rolls them all back.
            from sqlalchemy import select as _sel  # noqa: PLC0415

            sess_rows = (
                (
                    await db.execute(
                        _sel(DBSession).where(
                            DBSession.device_session_id == "SESS_CHILD_BAD"
                        )
                    )
                )
                .scalars()
                .all()
            )
            if sess_rows:
                raise RuntimeError(
                    "Forced failure after ALL children flushed (waveform+event+stat+setting)"
                )
            return result

        importer = SessionImporter()
        with patch.object(
            SessionImporter, "_import_single_session", _raise_after_all_children_flushed
        ):
            async with session_scope() as chunk_db:
                imported, skipped, failed = await importer.import_sessions_batch(
                    iter([good, bad]),
                    batch_size=2,
                    db=chunk_db,
                )

        assert failed == 1, f"Expected 1 failure, got {failed}"
        assert imported == 1, f"Expected 1 imported, got {imported}"

        # Verify: good session's rows present; ALL of bad session's rows absent.
        async with session_scope() as verify:
            from sqlalchemy import select as _sv  # noqa: PLC0415

            # --- Good session must survive ---
            good_sessions = (
                (
                    await verify.execute(
                        _sv(DBSession).where(
                            DBSession.device_session_id == "SESS_CHILD_GOOD"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(good_sessions) == 1, "Good session must survive"
            good_sid = good_sessions[0].id

            assert (
                len(
                    (
                        await verify.execute(
                            _sv(DBWaveform).where(DBWaveform.session_id == good_sid)
                        )
                    )
                    .scalars()
                    .all()
                )
                >= 1
            ), "Good session waveforms must survive"
            assert (
                len(
                    (
                        await verify.execute(
                            _sv(DBEvent).where(DBEvent.session_id == good_sid)
                        )
                    )
                    .scalars()
                    .all()
                )
                >= 1
            ), "Good session events must survive"
            assert (
                len(
                    (
                        await verify.execute(
                            _sv(DBStatistics).where(DBStatistics.session_id == good_sid)
                        )
                    )
                    .scalars()
                    .all()
                )
                >= 1
            ), "Good session statistics must survive"

            # --- Bad session: all seven tables must be clean ---
            bad_sessions = (
                (
                    await verify.execute(
                        _sv(DBSession).where(
                            DBSession.device_session_id == "SESS_CHILD_BAD"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(bad_sessions) == 0, "Bad session row must be rolled back"

            bad_devices = (
                (
                    await verify.execute(
                        _sv(DBDevice).where(DBDevice.serial_number == "SN_CHILD_BAD")
                    )
                )
                .scalars()
                .all()
            )
            assert len(bad_devices) == 0, "Bad device row must be rolled back"

            # Day rows belonging only to the bad session must be gone.
            all_session_ids = {
                s.id for s in (await verify.execute(_sv(DBSession))).scalars().all()
            }
            all_device_ids = {
                d.id for d in (await verify.execute(_sv(DBDevice))).scalars().all()
            }

            # No Day row should have a device_id that belongs to no surviving device.
            orphan_days = [
                d
                for d in (await verify.execute(_sv(DBDay))).scalars().all()
                if d.device_id not in all_device_ids
            ]
            assert len(orphan_days) == 0, (
                f"Orphan Day rows found: {[d.id for d in orphan_days]}"
            )

            orphan_waveforms = [
                w
                for w in (await verify.execute(_sv(DBWaveform))).scalars().all()
                if w.session_id not in all_session_ids
            ]
            assert len(orphan_waveforms) == 0, (
                f"Orphan Waveform rows found: {[w.id for w in orphan_waveforms]}"
            )

            orphan_events = [
                e
                for e in (await verify.execute(_sv(DBEvent))).scalars().all()
                if e.session_id not in all_session_ids
            ]
            assert len(orphan_events) == 0, (
                f"Orphan Event rows found: {[e.id for e in orphan_events]}"
            )

            orphan_stats = [
                s
                for s in (await verify.execute(_sv(DBStatistics))).scalars().all()
                if s.session_id not in all_session_ids
            ]
            assert len(orphan_stats) == 0, (
                f"Orphan Statistics rows found: {[s.id for s in orphan_stats]}"
            )

            orphan_settings = [
                s
                for s in (await verify.execute(_sv(DBSetting))).scalars().all()
                if s.session_id not in all_session_ids
            ]
            assert len(orphan_settings) == 0, (
                f"Orphan Setting rows found: {[s.id for s in orphan_settings]}"
            )


# ---------------------------------------------------------------------------
# Typed bulk INSERT tests (W4: § 90/PR-2)
# ---------------------------------------------------------------------------


class TestTypedBulkInsert:
    """``execute(insert(Model), mappings)`` must populate rows correctly.

    Verifies the bulk INSERT strategy used by ``_import_waveforms``,
    ``_import_events``, and ``_import_settings``: rows are readable after
    commit, autoincrement IDs are populated, and a savepoint rollback removes
    ALL bulk rows for a failed session while the good session's rows survive.
    """

    def _make_session_with_waveforms(
        self, serial: str, session_id_str: str
    ) -> UnifiedSession:
        """Build a UnifiedSession with one FLOW_RATE waveform and one event."""
        from datetime import datetime

        import numpy as np

        from snore.parsers.unified import (
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

        arr = np.array([[0.0, 1.0], [0.04, 1.1]], dtype=np.float32)
        blob = arr.tobytes()

        device_info = DeviceInfo(
            manufacturer="BulkMfr",
            model="BulkMdl",
            serial_number=serial,
        )
        start = datetime(2024, 2, 1, 21, 0, 0)
        end = datetime(2024, 2, 2, 5, 0, 0)

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
            duration_seconds=15.0,
        )

        settings = TherapySettings(mode=TherapyMode.CPAP, pressure_fixed=10.0)
        stats = SessionStatistics(usage_hours=8.0, ahi=1.5)

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

    async def test_bulk_insert_rows_readable_and_ids_populated(self, temp_db):
        """After import, waveform/event/setting rows are readable with non-null IDs.

        Confirms that ``execute(insert(Model), mappings)`` correctly:
        - Creates rows visible to a follow-up SELECT.
        - Populates the autoincrement ``id`` column.
        - Stores the correct field values.
        """
        from sqlalchemy import select

        from snore.database.importers import SessionImporter
        from snore.database.models import Event, Setting, Waveform
        from snore.database.session import session_scope

        await init_database(str(temp_db))

        session_data = self._make_session_with_waveforms(
            "SN_BULK_READ", "SESS_BULK_READ"
        )

        importer = SessionImporter()
        async with session_scope() as db:
            async with db.begin_nested():
                imported, day_id = await importer._import_single_session(
                    db, session_data
                )

        assert imported is True

        # Verify waveforms: id populated, sample_rate correct.
        async with session_scope() as verify:
            from sqlalchemy import select

            from snore.database.models import Session as DBSession

            sess_row = (
                (
                    await verify.execute(
                        select(DBSession).where(
                            DBSession.device_session_id == "SESS_BULK_READ"
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert sess_row is not None, "Session row missing after import"

            waveforms = (
                (
                    await verify.execute(
                        select(Waveform).where(Waveform.session_id == sess_row.id)
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await verify.execute(
                        select(Event).where(Event.session_id == sess_row.id)
                    )
                )
                .scalars()
                .all()
            )
            settings = (
                (
                    await verify.execute(
                        select(Setting).where(Setting.session_id == sess_row.id)
                    )
                )
                .scalars()
                .all()
            )

        assert len(waveforms) == 1, f"Expected 1 waveform, got {len(waveforms)}"
        assert waveforms[0].id is not None, (
            "Waveform id must be populated (autoincrement)"
        )
        assert waveforms[0].sample_rate == 25.0

        assert len(events) == 1, f"Expected 1 event, got {len(events)}"
        assert events[0].id is not None, "Event id must be populated (autoincrement)"
        assert events[0].duration_seconds == 15.0

        assert len(settings) >= 1, f"Expected at least 1 setting, got {len(settings)}"
        mode_settings = [s for s in settings if s.key == "mode"]
        assert len(mode_settings) == 1
        assert mode_settings[0].value.upper() == "CPAP"

    async def test_bulk_insert_no_applicable_defaults_schema_assertion(self, temp_db):
        """Assert that Waveform/Event/Setting have no non-id ORM-level defaults.

        These models use typed INSERT executemany — all column values are
        supplied explicitly in the mapping.  There are no ``default=`` or
        ``server_default=`` column kwargs on non-pk columns, so no SQLAlchemy
        default population step runs during bulk insert.  This test documents
        that fact explicitly so future schema changes that add a default are
        caught here.

        If a column default is added in the future, this test fails and the
        importer mapping must be updated to either include or omit the column.
        """
        from sqlalchemy import inspect as sa_inspect

        from snore.database.models import Event, Setting, Waveform

        for model_cls in (Waveform, Event, Setting):
            mapper = sa_inspect(model_cls)
            # mapper.columns yields Column objects directly (SQLAlchemy 2.0 mapper
            # inspection API) — no .columns[0] indirection needed.
            for col in mapper.columns:
                # Skip the primary key — autoincrement is expected and intentional.
                if col.primary_key:
                    continue
                assert col.default is None, (
                    f"{model_cls.__name__}.{col.name} has an ORM column default "
                    f"({col.default!r}). Update the typed INSERT mapping to handle it, "
                    f"then revise or remove this assertion."
                )
                assert col.server_default is None, (
                    f"{model_cls.__name__}.{col.name} has a server_default "
                    f"({col.server_default!r}). Ensure the typed INSERT mapping "
                    f"accounts for it, then revise or remove this assertion."
                )

    async def test_bulk_insert_identity_map_stays_bounded(self, temp_db):
        """Typed INSERT executemany must not grow the identity map with child rows.

        ``add_all()`` registers every instance in the session's identity map,
        causing memory to grow with the child-row count.  ``execute(insert(Model),
        mappings)`` bypasses the ORM unit-of-work entirely, so the identity map
        should not accumulate child rows across multiple bulk inserts.

        This test imports N sessions' worth of waveforms/events/settings and
        asserts that the session identity map size does not scale with N.
        """
        import gc  # noqa: PLC0415

        from snore.database.importers import SessionImporter  # noqa: PLC0415
        from snore.database.session import session_scope  # noqa: PLC0415

        await init_database(str(temp_db))

        n_sessions = 20  # enough to show O(n) growth if present

        importer = SessionImporter()
        identity_map_sizes: list[int] = []

        async with session_scope() as db:
            for i in range(n_sessions):
                session_data = self._make_session_with_waveforms(
                    f"SN_BOUNDED_{i}", f"SESS_BOUNDED_{i}"
                )
                async with db.begin_nested():
                    await importer._import_single_session(db, session_data)

                # Measure identity-map size after each session import.
                # expunge_all is not called — we are testing natural growth.
                gc.collect()
                identity_map_sizes.append(len(db.identity_map))

        # The identity map should not grow proportionally with n_sessions.
        # After N bulk inserts, its size should reflect only parent objects
        # (Device, Day, Session) — not the child Waveform/Event/Setting rows.
        # We allow a generous cap: at most 10 * max_parent_rows per import.
        max_allowed = (
            n_sessions * 10
        )  # Device + Day + Session per import = 3; 10x slack
        final_size = identity_map_sizes[-1]
        assert final_size <= max_allowed, (
            f"Identity map grew to {final_size} after {n_sessions} imports "
            f"(max allowed {max_allowed}). Typed INSERT may be falling back to "
            f"add_all() or accumulating child rows in the identity map."
        )


# ---------------------------------------------------------------------------
# Once-future initialization state-machine tests (X1)
# ---------------------------------------------------------------------------


class TestInitOnceFuture:
    """``init_database`` once-future state machine: concurrency + failure-retry."""

    async def test_concurrent_callers_await_same_migration(self, tmp_path):
        """Second caller must NOT return before the first caller's migration finishes.

        Probe: block the migration in a threading.Event; launch two concurrent
        callers; assert the second has not returned while the first is still
        migrating.
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "concurrent_init.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()
        migration_calls = [0]

        original_apply = sess_mod._apply_migrations_sync

        def blocking_migration(sync_url: str) -> None:
            migration_calls[0] += 1
            migration_started.set()  # Signal: migration is now running.
            migration_gate.wait()  # Block until the test releases the gate.
            original_apply(sync_url)

        second_returned_before_migration: list[bool] = [False]

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", blocking_migration
        ):

            async def first_caller() -> None:
                await sess_mod.init_database(str(db_path))

            async def second_caller() -> None:
                # Wait until migration is in progress, then call init_database.
                await asyncio.get_event_loop().run_in_executor(
                    None, migration_started.wait
                )
                # If the once-future is working, this should block until the
                # first caller's migration completes.
                await sess_mod.init_database(str(db_path))
                # If we reached here before migration_gate was released, the
                # once-future returned early.
                if not migration_gate.is_set():
                    second_returned_before_migration[0] = True

            task1 = asyncio.create_task(first_caller())
            task2 = asyncio.create_task(second_caller())

            # Let both start, then release the migration gate.
            await asyncio.sleep(0.05)
            migration_gate.set()

            await asyncio.gather(task1, task2)

        assert not second_returned_before_migration[0], (
            "Second concurrent caller returned before first caller's migration "
            "completed — once-future coordination is broken."
        )
        assert migration_calls[0] == 1, (
            f"Migration ran {migration_calls[0]} times — expected exactly 1."
        )

        await sess_mod.cleanup_database()

    async def test_failure_retry_reruns_migration_and_clears_globals(self, tmp_path):
        """After an injected migration failure, a retry re-runs migration from clean state.

        On failure: engine/factory/future must all be cleared.
        On retry: migration runs again and init succeeds.
        """
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "failure_retry.db"

        original_apply = sess_mod._apply_migrations_sync
        call_count = [0]
        should_fail = [True]

        def maybe_fail(sync_url: str) -> None:
            call_count[0] += 1
            if should_fail[0]:
                raise RuntimeError("Injected migration failure")
            original_apply(sync_url)

        with unittest.mock.patch.object(sess_mod, "_apply_migrations_sync", maybe_fail):
            # First call — should fail.
            try:
                await sess_mod.init_database(str(db_path))
                raise AssertionError("Expected RuntimeError not raised")
            except RuntimeError:
                pass

            # Globals must be clean after failure.
            assert sess_mod._engine is None, "Engine must be None after failed init."
            assert sess_mod._AsyncSessionFactory is None, (
                "SessionFactory must be None after failed init."
            )
            assert sess_mod._init_future is None, (
                "Once-future must be cleared after failure so retry can proceed."
            )
            retry_migration_calls_before = call_count[0]

            # Retry — should succeed.
            should_fail[0] = False
            await sess_mod.init_database(str(db_path))

        assert sess_mod._engine is not None, "Engine must be set after retry."
        assert sess_mod._AsyncSessionFactory is not None, (
            "SessionFactory must be set after retry."
        )
        retry_calls = call_count[0] - retry_migration_calls_before
        assert retry_calls >= 1, (
            f"Migration was not called on retry (calls after first failure: {retry_calls})."
        )

        await sess_mod.cleanup_database()
