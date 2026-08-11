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
        # Also capture the profile_id for reuse in the batch Devices below.
        test_profile_id: int
        async with session_scope() as setup_session:
            from snore.database.models import Profile, User

            test_user = User(canonical_email="savepoint@example.com", role="admin")
            setup_session.add(test_user)
            await setup_session.flush()
            test_profile = Profile(user_id=test_user.id, name="Test Profile")
            setup_session.add(test_profile)
            await setup_session.flush()
            test_profile_id = test_profile.id

            device = Device(
                profile_id=test_profile_id,
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
                    profile_id=test_profile_id,
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
                        profile_id=test_profile_id,
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

        with patch.object(DayManager, "get_or_create_day", patched_day):
            async with session_scope() as batch_db:
                # Create a profile for devices.
                import uuid  # noqa: PLC0415

                from snore.database.models import Profile as _P  # noqa: PLC0415
                from snore.database.models import User as _U

                _u = _U(
                    canonical_email=f"pf_{uuid.uuid4().hex[:8]}@example.com",
                    role="admin",
                )
                batch_db.add(_u)
                await batch_db.flush()
                _p = _P(user_id=_u.id, name="PF Test")
                batch_db.add(_p)
                await batch_db.flush()
                _pid = _p.id

                importer = SessionImporter(_pid)
                imported, skipped, failed, _ids = await importer.import_sessions_batch(
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

        with patch.object(
            SessionImporter, "_import_single_session", _raise_after_all_children_flushed
        ):
            async with session_scope() as chunk_db:
                # Create a profile for devices.
                import uuid  # noqa: PLC0415

                from snore.database.models import Profile as _P2  # noqa: PLC0415
                from snore.database.models import User as _U2

                _u2 = _U2(
                    canonical_email=f"child_{uuid.uuid4().hex[:8]}@example.com",
                    role="admin",
                )
                chunk_db.add(_u2)
                await chunk_db.flush()
                _p2 = _P2(user_id=_u2.id, name="Child Test")
                chunk_db.add(_p2)
                await chunk_db.flush()
                _pid2 = _p2.id

                importer = SessionImporter(_pid2)
                imported, skipped, failed, _ids = await importer.import_sessions_batch(
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
        import uuid  # noqa: PLC0415

        from sqlalchemy import select

        from snore.database.importers import SessionImporter
        from snore.database.models import Event, Profile, Setting, User, Waveform
        from snore.database.session import session_scope

        await init_database(str(temp_db))

        session_data = self._make_session_with_waveforms(
            "SN_BULK_READ", "SESS_BULK_READ"
        )

        # Create a profile for the device to satisfy the NOT NULL FK constraint.
        async with session_scope() as setup_db:
            user = User(
                canonical_email=f"bulk_{uuid.uuid4().hex[:8]}@example.com", role="admin"
            )
            setup_db.add(user)
            await setup_db.flush()
            profile = Profile(user_id=user.id, name="Bulk Test")
            setup_db.add(profile)
            await setup_db.flush()
            profile_id = profile.id

        importer = SessionImporter(profile_id)
        async with session_scope() as db:
            async with db.begin_nested():
                imported, day_id, _sid, _extra = await importer._import_single_session(
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
        should not accumulate child rows during a bulk insert.

        This test imports N sessions each with 50 events and measures the ORM
        new-object set (``db.new``) plus identity-map size INSIDE the savepoint,
        immediately after ``_import_single_session`` returns and before the
        nested transaction flushes.  That is the point where ``add_all()``
        accumulates all child instances; typed INSERT leaves ``db.new`` empty.
        """
        from datetime import datetime  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415

        from snore.database.importers import SessionImporter  # noqa: PLC0415
        from snore.database.session import session_scope  # noqa: PLC0415
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

        await init_database(str(temp_db))

        n_sessions = 20
        n_events_per_session = 50  # large enough to make add_all() peak clearly visible

        def _make_heavy_session(idx: int) -> UnifiedSession:
            arr = np.array([[0.0, 1.0]], dtype=np.float32)
            device_info = DeviceInfo(
                manufacturer="BulkMfr", model="BulkMdl", serial_number=f"SN_BND_{idx}"
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
                sample_count=1,
                data_blob=arr.tobytes(),
                timestamps=[0.0],
                values=[1.0],
            )
            events = [
                RespiratoryEvent(
                    event_type=RespiratoryEventType.OBSTRUCTIVE_APNEA,
                    start_time=start,
                    duration_seconds=float(j),
                )
                for j in range(n_events_per_session)
            ]
            settings = TherapySettings(mode=TherapyMode.CPAP, pressure_fixed=10.0)
            stats = SessionStatistics(usage_hours=8.0, ahi=1.5)
            sess = UnifiedSession(
                device_info=device_info,
                device_session_id=f"SESS_BND_{idx}",
                start_time=start,
                end_time=end,
                settings=settings,
                statistics=stats,
                has_waveform_data=True,
                has_event_data=True,
                has_statistics=True,
            )
            sess.waveforms = {WaveformType.FLOW_RATE: wf}
            sess.events = events
            return sess

        peak_orm_sizes: list[int] = []

        # Create a profile for devices to satisfy the NOT NULL FK constraint.
        import uuid  # noqa: PLC0415

        from snore.database.models import Profile as _Profile  # noqa: PLC0415
        from snore.database.models import User as _User

        async with session_scope() as setup_db:
            _user = _User(
                canonical_email=f"bnd_{uuid.uuid4().hex[:8]}@example.com", role="admin"
            )
            setup_db.add(_user)
            await setup_db.flush()
            _profile = _Profile(user_id=_user.id, name="BND Test")
            setup_db.add(_profile)
            await setup_db.flush()
            _profile_id = _profile.id

        importer = SessionImporter(_profile_id)
        async with session_scope() as db:
            for i in range(n_sessions):
                session_data = _make_heavy_session(i)
                async with db.begin_nested():
                    # Disable autoflush so add_all() child objects accumulate in
                    # db.new throughout the import rather than being silently
                    # flushed when _import_settings calls db.execute().
                    # Explicit db.flush() calls inside the importer still run —
                    # only automatic pre-execute flushes are suppressed.
                    with db.no_autoflush:
                        await importer._import_single_session(db, session_data)
                    # Measure INSIDE the savepoint, before flush on exit.
                    # add_all() accumulates all child instances in db.new here;
                    # typed INSERT leaves db.new empty.
                    peak_orm_sizes.append(len(db.new) + len(db.identity_map))

        # With no_autoflush, typed INSERT (execute(insert(Model), mappings))
        # never adds instances to db.new or the identity map — child rows are
        # inserted via raw SQL and bypass the ORM unit-of-work entirely.
        # add_all() regression: instances accumulate in db.new (50 events +
        # 1 Statistics object = 51 pending), clearly exceeding the cap of 5.
        # Cap is generous for the parent objects that ARE tracked via add():
        # Device (flushed to identity_map) + Session (flushed) = identity_map
        # size of ~2; Statistics is in db.new but is a single object.
        max_allowed = 5
        max_observed = max(peak_orm_sizes)
        assert max_observed <= max_allowed, (
            f"ORM new+identity_map peaked at {max_observed} inside savepoint "
            f"(max allowed {max_allowed}, n_events={n_events_per_session}). "
            f"Typed INSERT may be falling back to add_all() or accumulating "
            f"child rows in the ORM unit-of-work."
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
            # _init_task.done() is True (failed); retry will create a fresh task.
            assert sess_mod._init_task is None or sess_mod._init_task.done(), (
                "Once-task must be done (failed) after migration failure so retry can proceed."
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

    async def test_cancelled_caller_does_not_interrupt_init_task(self, tmp_path):
        """Cancelling a caller's awaiting coroutine does not cancel the shared Task.

        With asyncio.shield(), cancelling the caller (the coroutine that created
        the init task) raises CancelledError in the caller only; the inner
        _do_init Task keeps running and eventually publishes engine/factory.

        This is the "init-survives-caller-cancellation" semantic: once _do_init
        is underway, it always runs to completion.

        A second caller launched after the first is cancelled can await the same
        task (or find globals already set) and succeed.
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "cancel_caller.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()
        original_apply = sess_mod._apply_migrations_sync

        def blocking_migration(sync_url: str) -> None:
            migration_started.set()
            migration_gate.wait()
            original_apply(sync_url)

        second_succeeded = [False]
        first_got_cancelled = [False]

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", blocking_migration
        ):

            async def first_caller() -> None:
                try:
                    await sess_mod.init_database(str(db_path))
                except asyncio.CancelledError:
                    first_got_cancelled[0] = True

            async def second_caller() -> None:
                # Wait until migration is in progress, then call init.
                await asyncio.get_event_loop().run_in_executor(
                    None, migration_started.wait
                )
                await sess_mod.init_database(str(db_path))
                second_succeeded[0] = True

            first_task = asyncio.create_task(first_caller())
            second_task = asyncio.create_task(second_caller())

            # Let migration start, cancel first caller, then release gate.
            await asyncio.sleep(0.05)
            first_task.cancel()
            migration_gate.set()

            await asyncio.gather(first_task, second_task, return_exceptions=True)

        # Inner _do_init task completed (survived first_task cancellation).
        assert sess_mod._engine is not None, (
            "Engine must be published — _do_init survives caller cancellation."
        )
        assert sess_mod._AsyncSessionFactory is not None
        # First caller got CancelledError (shield propagated it).
        assert first_got_cancelled[0], (
            "First caller should have received CancelledError."
        )
        # Second caller succeeded.
        assert second_succeeded[0], (
            "Second caller must succeed after _do_init completes."
        )

        await sess_mod.cleanup_database()

    async def test_cancelled_waiter_does_not_affect_successful_driver(self, tmp_path):
        """Cancelling a waiter via shield() leaves the driver's success intact.

        The other concurrent caller (the driver's shielded await) must get
        success — no InvalidStateError, no unretrieved-future warning.
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "cancel_waiter.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()
        original_apply = sess_mod._apply_migrations_sync

        def blocking_migration(sync_url: str) -> None:
            migration_started.set()
            migration_gate.wait()
            original_apply(sync_url)

        waiter_got_cancelled = [False]
        driver_got_error = [False]

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", blocking_migration
        ):

            async def driver_caller() -> None:
                try:
                    await sess_mod.init_database(str(db_path))
                except Exception:
                    driver_got_error[0] = True

            async def waiter_caller() -> None:
                # Wait until migration is running, then try to init (becomes a waiter).
                await asyncio.get_event_loop().run_in_executor(
                    None, migration_started.wait
                )
                try:
                    await sess_mod.init_database(str(db_path))
                except asyncio.CancelledError:
                    waiter_got_cancelled[0] = True

            driver_task = asyncio.create_task(driver_caller())
            waiter_task = asyncio.create_task(waiter_caller())

            # Let both callers get into migration, then cancel the waiter.
            await asyncio.sleep(0.05)
            waiter_task.cancel()
            # Release the migration gate so the driver can finish.
            migration_gate.set()

            await asyncio.gather(driver_task, waiter_task, return_exceptions=True)

        # Waiter got CancelledError (shield propagates it to the waiter only).
        assert waiter_got_cancelled[0], "Waiter should have received CancelledError"
        # Driver must have succeeded — no InvalidStateError.
        assert not driver_got_error[0], (
            "Driver must not raise after waiter cancellation"
        )
        assert sess_mod._engine is not None, (
            "Engine must be published after driver success"
        )
        assert sess_mod._AsyncSessionFactory is not None

        await sess_mod.cleanup_database()

    async def test_failure_leaves_no_unretrieved_future_warning(self, tmp_path):
        """A failed init must not emit 'Future exception was never retrieved'.

        With a shared Task (not a bare Future), the exception is attached to the
        Task object; the Task is cleared on failure so there is no orphan
        future/task whose exception is never consumed.
        """
        import asyncio  # noqa: PLC0415
        import logging  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "no_warning_failure.db"

        # Capture asyncio's internal logger to detect the warning.
        warning_messages: list[str] = []

        def failing_once(sync_url: str) -> None:
            raise RuntimeError("Injected failure for warning test")

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", failing_once
        ):
            with unittest.mock.patch.object(
                logging.getLogger("asyncio"),
                "error",
                side_effect=lambda msg, *a, **kw: warning_messages.append(str(msg)),
            ):
                try:
                    await sess_mod.init_database(str(db_path))
                except RuntimeError:
                    pass
                # Allow event loop to process any pending task callbacks.
                await asyncio.sleep(0)

        unretrieved = [
            m for m in warning_messages if "exception was never retrieved" in m
        ]
        assert not unretrieved, (
            f"Unretrieved exception warning(s) detected: {unretrieved}"
        )
        # Globals must be clean; _init_task is done (failed).
        assert sess_mod._engine is None
        assert sess_mod._AsyncSessionFactory is None
        assert sess_mod._init_task is None or sess_mod._init_task.done(), (
            "_init_task must be None or done after a failed init"
        )

    async def test_cleanup_during_init_prevents_engine_publish(self, tmp_path):
        """cleanup_database() awaits the in-flight task; no engine appears after it returns.

        Scenario: block _do_init, call cleanup_database(), assert cleanup does
        not return while the task is live, and no engine/factory is published
        after cleanup returns.

        Note: asyncio.to_thread wraps a real OS thread; cancelling the Task
        injects CancelledError into the coroutine but the thread keeps running.
        The gate is released after cleanup so the orphan thread exits cleanly.
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "cleanup_race.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()
        original_apply = sess_mod._apply_migrations_sync

        def blocking_migration(sync_url: str) -> None:
            migration_started.set()
            migration_gate.wait()
            # Gate is released by the test after cleanup; thread completes harmlessly.
            original_apply(sync_url)

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", blocking_migration
        ):
            init_task = asyncio.create_task(sess_mod.init_database(str(db_path)))
            # Wait until migration is in progress.
            await asyncio.get_event_loop().run_in_executor(None, migration_started.wait)

            # Call cleanup while init is blocked.  cleanup cancels the task and
            # awaits it — the blocked migration coroutine is interrupted via
            # CancelledError; the underlying thread is still running but
            # cleanup does not need to wait for it.
            await sess_mod.cleanup_database()

        # After cleanup returns, no engine or factory may be live.
        assert sess_mod._engine is None, (
            "Engine must be None after cleanup_database() returns"
        )
        assert sess_mod._AsyncSessionFactory is None, (
            "SessionFactory must be None after cleanup_database() returns"
        )
        # cleanup_database() explicitly sets _init_task = None
        assert sess_mod._init_task is None, (
            "init_task must be None after cleanup_database() returns"
        )

        # Release the orphan thread so it can exit cleanly, then give the
        # event loop a turn so init_task's shield propagates cancellation.
        migration_gate.set()
        await asyncio.sleep(0.05)

        # After the event loop turn, init_task should be done (cancelled).
        assert init_task.done(), (
            "Init task must be done after cleanup + event loop turn"
        )

    async def test_cleanup_does_not_allow_concurrent_reinit_to_publish(self, tmp_path):
        """cleanup_database() blocks concurrent re-inits until disposal is complete.

        T1 scenario: block _do_init for the first init, start cleanup, fire a
        concurrent init_database() while cleanup is waiting on the old task.
        Assert:
        - At cleanup return, no engine/factory is live.
        - The new init, once the barrier clears, publishes normally.

        Acceptance shape (Paul/Thufir probes): the re-init must not publish an
        engine before cleanup returns.

        Migration function: the fake migration blocks on the first call (to keep
        the first init in flight for the race) and is a no-op on subsequent calls
        (to let the re-init publish without spawning a concurrent Alembic session,
        which would corrupt Alembic's process-global EnvironmentContext).
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path1 = tmp_path / "cleanup_old.db"
        db_path2 = tmp_path / "cleanup_new.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()
        call_count: list[int] = [0]

        def fake_migration(sync_url: str) -> None:
            """Block the first call; return immediately on subsequent calls.

            Alembic's EnvironmentContext uses process-global state, so two
            concurrent stamp/upgrade calls corrupt each other.  This fake avoids
            that by letting only one Alembic session run at a time: the first
            call blocks until the gate is set (giving cleanup something to cancel)
            and subsequent calls are no-ops (so the re-init can publish without a
            real database).
            """
            call_count[0] += 1
            if call_count[0] == 1:
                migration_started.set()
                migration_gate.wait()
            # No-op — engine creation + migration stub is enough for the
            # barrier test; the engine object is published without a real DB file.

        # Track state at the moment cleanup returns.
        engine_at_cleanup_return: list[object] = []
        factory_at_cleanup_return: list[object] = []

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", fake_migration
        ):
            # Start an init that blocks in migration (db_path1).
            init_task = asyncio.create_task(sess_mod.init_database(str(db_path1)))
            await asyncio.get_event_loop().run_in_executor(None, migration_started.wait)

            # Start cleanup while init is blocked.  cleanup_database() will
            # create a _cleanup_task that owns the in-flight init task,
            # cancel+await it, then dispose state.
            cleanup_task = asyncio.create_task(sess_mod.cleanup_database())

            # Give cleanup one event loop turn so it can acquire the lock and
            # create _cleanup_task before the re-init can see it.
            await asyncio.sleep(0)

            # Fire the re-init while cleanup is in progress.  The state machine
            # should make it wait on _cleanup_task until cleanup fully disposes.
            reinit_task = asyncio.create_task(sess_mod.init_database(str(db_path2)))

            # Release the blocked migration thread so cleanup can quiesce.
            migration_gate.set()

            # Wait for cleanup to finish.
            await cleanup_task

            # init_task was cancelled by cleanup (shield propagates CancelledError);
            # collect it so the task is not left pending.
            await asyncio.gather(init_task, return_exceptions=True)

        # Record state immediately after cleanup returns (inside the patch or not
        # doesn't matter here — we captured the reference just after await).
        engine_at_cleanup_return.append(sess_mod._engine)
        factory_at_cleanup_return.append(sess_mod._AsyncSessionFactory)

        # Let the re-init complete now that the barrier is clear.
        await asyncio.gather(reinit_task, return_exceptions=True)

        # Core invariant: at cleanup return, no engine was live.
        assert engine_at_cleanup_return[0] is None, (
            "Engine must be None at cleanup_database() return — "
            f"got {engine_at_cleanup_return[0]!r}"
        )
        assert factory_at_cleanup_return[0] is None, (
            "SessionFactory must be None at cleanup_database() return — "
            f"got {factory_at_cleanup_return[0]!r}"
        )

        # After the re-init, the new engine should be live.
        assert sess_mod._engine is not None, (
            "New init must publish an engine after cleanup barrier clears"
        )
        assert sess_mod._AsyncSessionFactory is not None

        await sess_mod.cleanup_database()

    async def test_cancelled_sole_caller_then_failure_no_unretrieved_warning(
        self, tmp_path
    ):
        """No 'Task exception was never retrieved' when sole caller is cancelled.

        T2 scenario: cancel the only caller while init is running, then fail
        the shared task.  Assert:
        - No asyncio "Task exception was never retrieved" warning is emitted.
        - The failure IS logged (via the done-callback's logger.error call).
        """
        import asyncio  # noqa: PLC0415
        import logging  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "cancelled_sole_then_fail.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()

        failure_message = "late init failure after sole caller cancelled"

        def failing_migration(sync_url: str) -> None:
            migration_started.set()
            migration_gate.wait()
            raise RuntimeError(failure_message)

        warning_messages: list[str] = []
        logged_errors: list[str] = []

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", failing_migration
        ):
            with (
                unittest.mock.patch.object(
                    logging.getLogger("asyncio"),
                    "error",
                    side_effect=lambda msg, *a, **kw: warning_messages.append(str(msg)),
                ),
                unittest.mock.patch.object(
                    sess_mod.logger,
                    "error",
                    side_effect=lambda msg, *a, **kw: logged_errors.append(str(msg)),
                ),
            ):
                caller_task = asyncio.create_task(sess_mod.init_database(str(db_path)))
                # Capture the shared _init_task before cancelling the caller —
                # after cancel+gather, _init_task may still be set but running.
                await asyncio.sleep(0)  # let caller create _init_task
                task_ref = sess_mod._init_task
                # Wait until migration has started.
                await asyncio.get_event_loop().run_in_executor(
                    None, migration_started.wait
                )

                # Cancel the sole caller.
                caller_task.cancel()
                await asyncio.gather(caller_task, return_exceptions=True)

                # Release the gate — migration will fail; the shared task will
                # fail with RuntimeError.  The done-callback should catch it.
                migration_gate.set()
                # Wait (bounded) for the shared task to finish WITHOUT awaiting
                # it — nobody may retrieve the exception except the done-callback.
                import time  # noqa: PLC0415

                deadline = time.monotonic() + 30.0
                while task_ref is not None and not task_ref.done():
                    assert time.monotonic() < deadline, "init task did not finish"
                    await asyncio.sleep(0.01)
                # One extra turn: done-callbacks run via call_soon after the
                # task completes.
                await asyncio.sleep(0)

        # T2 assertion 1: no asyncio "never retrieved" warning.
        unretrieved = [
            m for m in warning_messages if "exception was never retrieved" in m
        ]
        assert not unretrieved, (
            f"'Task exception was never retrieved' warning detected: {unretrieved}"
        )

        # T2 assertion 2: the done-callback logged the failure (not silent suppression).
        assert any("initialization failed" in m for m in logged_errors), (
            f"Expected an error log from the done-callback; got: {logged_errors}"
        )

        # Globals are clean (teardown ran in BaseException handler).
        assert sess_mod._engine is None
        assert sess_mod._AsyncSessionFactory is None

    # -----------------------------------------------------------------------
    # New prescriptive tests for the shared-cleanup-task state machine
    # (Thufir's "Alternatives" design, Paul's A-round dispatch)
    # -----------------------------------------------------------------------

    async def test_lock_queue_init_then_cleanup_no_unowned_task(self, tmp_path):
        """Lock-queue interleaving: init queued then cleanup wins — no unowned task.

        Deterministically forces the worst-case FIFO order:
        1. Test holds _init_lock.
        2. init_database() queues behind the lock (it will see idle state when
           it eventually acquires).
        3. cleanup_database() queues behind init_database() (FIFO).
        4. Lock is released.  init_database() runs first (creates init task),
           cleanup_database() runs second (sees init task and owns it).

        Asserts: at cleanup return, no unowned init task capable of publication
        exists; state is fully clean.  This is the exact shape that killed the
        old check/use-gap barrier.
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "lock_queue.db"

        migration_gate = threading.Event()
        migration_started = threading.Event()
        original_apply = sess_mod._apply_migrations_sync

        def slow_migration(sync_url: str) -> None:
            migration_started.set()
            migration_gate.wait()
            original_apply(sync_url)

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", slow_migration
        ):
            # Reset _init_lock so it's created fresh in this test's event loop.
            # Required because _init_lock is module-level and pytest-asyncio uses
            # function-scoped event loops — the lock from a prior test is bound to
            # a different (now closed) loop.
            sess_mod._init_lock = None
            lock = sess_mod._get_init_lock()

            # Hold the lock from the test so we can queue init and cleanup
            # behind it in FIFO order before releasing.
            async with lock:
                # Queue: init_database() will be first to acquire after release.
                init_task = asyncio.create_task(sess_mod.init_database(str(db_path)))
                # Give init_database() a turn so it blocks on the lock.
                await asyncio.sleep(0)

                # Queue: cleanup_database() will be second (FIFO behind init).
                cleanup_task = asyncio.create_task(sess_mod.cleanup_database())
                # Give cleanup_database() a turn so it also blocks on the lock.
                await asyncio.sleep(0)
            # Lock released.  init_database() acquires first, creates _init_task,
            # releases.  cleanup_database() acquires second, sees the init task,
            # creates _cleanup_task owning it, releases, then awaits _do_cleanup.

            # Let the event loop run both tasks to their first suspension point.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # Release migration so cleanup can quiesce the init task.
            migration_gate.set()

            # Await both tasks; neither should raise.
            results = await asyncio.gather(
                init_task, cleanup_task, return_exceptions=True
            )
            for r in results:
                if isinstance(r, BaseException) and not isinstance(
                    r, asyncio.CancelledError
                ):
                    raise r

        # Core invariants after cleanup.
        assert sess_mod._engine is None, (
            f"Engine must be None after cleanup — got {sess_mod._engine!r}"
        )
        assert sess_mod._AsyncSessionFactory is None
        assert sess_mod._init_task is None
        assert sess_mod._cleanup_task is None

    async def test_concurrent_cleanups_both_return_no_attribute_error(self, tmp_path):
        """Two concurrent cleanup_database() calls complete without AttributeError.

        Makes quiescence slow (migration blocks) so the second cleanup arrives
        while the first is still running.  Both callers must return cleanly;
        teardown must execute exactly once; state must be fully clean.
        """
        import asyncio  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "concurrent_cleanup.db"

        migration_started = threading.Event()
        migration_gate = threading.Event()
        original_apply = sess_mod._apply_migrations_sync

        def slow_migration(sync_url: str) -> None:
            migration_started.set()
            migration_gate.wait()
            original_apply(sync_url)

        with unittest.mock.patch.object(
            sess_mod, "_apply_migrations_sync", slow_migration
        ):
            # Reset _init_lock for this test's event loop (function-scoped).
            sess_mod._init_lock = None
            # Start an init that blocks in migration.
            await asyncio.create_task(asyncio.sleep(0))  # flush loop
            init_coro = asyncio.create_task(sess_mod.init_database(str(db_path)))
            await asyncio.get_event_loop().run_in_executor(None, migration_started.wait)

            # Instrument _do_cleanup to count actual teardown executions.
            # A single shared _cleanup_task must execute teardown exactly once;
            # a two-owner implementation would execute it twice.
            do_cleanup_call_count: list[int] = [0]
            original_do_cleanup = sess_mod._do_cleanup

            async def counting_do_cleanup(
                owned_init_task: asyncio.Task[None] | None,
            ) -> None:
                do_cleanup_call_count[0] += 1
                return await original_do_cleanup(owned_init_task)

            sess_mod._do_cleanup = counting_do_cleanup

            # Start two concurrent cleanups while init is in flight.
            cleanup1 = asyncio.create_task(sess_mod.cleanup_database())
            # Give cleanup1 one turn so it creates _cleanup_task first.
            await asyncio.sleep(0)
            cleanup2 = asyncio.create_task(sess_mod.cleanup_database())

            # Let both cleanups queue up.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # Release the migration gate so teardown can quiesce the init task.
            migration_gate.set()

            # All three tasks must complete without raising.
            results = await asyncio.gather(
                init_coro, cleanup1, cleanup2, return_exceptions=True
            )
            errors = [
                r
                for r in results
                if isinstance(r, BaseException)
                and not isinstance(r, asyncio.CancelledError)
            ]
            assert not errors, (
                f"Concurrent cleanup raised unexpected exceptions: {errors}"
            )

            # Restore original _do_cleanup.
            sess_mod._do_cleanup = original_do_cleanup

        # State is fully clean.
        assert sess_mod._engine is None
        assert sess_mod._AsyncSessionFactory is None
        assert sess_mod._init_task is None
        assert sess_mod._cleanup_task is None
        # Single teardown: the shared _cleanup_task ran _do_cleanup exactly once.
        # A two-owner implementation would produce count == 2 here.
        assert do_cleanup_call_count[0] == 1, (
            f"_do_cleanup must execute exactly once across both concurrent cleanup "
            f"callers (executed {do_cleanup_call_count[0]} times)"
        )

    async def test_cancelled_cleanup_caller_teardown_still_completes(self, tmp_path):
        """Cancelling a cleanup_database() CALLER does not abandon teardown.

        The shield in cleanup_database() means caller cancellation only raises
        CancelledError in the caller; the _cleanup_task continues and finishes.
        A subsequent init_database() must succeed (no stuck-barrier hang).
        """
        import asyncio  # noqa: PLC0415
        import time  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path1 = tmp_path / "cancel_cleanup.db"
        db_path2 = tmp_path / "after_cancel.db"

        dispose_started = asyncio.Event()
        dispose_gate = asyncio.Event()
        original_dispose = sess_mod.AsyncEngine.dispose

        async def slow_dispose(self: object) -> None:
            dispose_started.set()
            await dispose_gate.wait()
            await original_dispose(self)

        # Reset _init_lock for this test's event loop (function-scoped).
        sess_mod._init_lock = None

        # First: bring up a live engine so cleanup has something to dispose.
        await sess_mod.init_database(str(db_path1))
        assert sess_mod._engine is not None

        with unittest.mock.patch.object(sess_mod.AsyncEngine, "dispose", slow_dispose):
            # Start cleanup — it will block inside slow_dispose.
            cleanup_caller = asyncio.create_task(sess_mod.cleanup_database())

            # Wait until _do_cleanup is inside dispose.
            await dispose_started.wait()

            # Cancel the CALLER.  _cleanup_task is shielded and keeps running.
            cleanup_caller.cancel()
            await asyncio.gather(cleanup_caller, return_exceptions=True)

            # Verify: cleanup task is still running (not done) right after cancel.
            assert sess_mod._cleanup_task is not None, (
                "_cleanup_task must still exist after caller cancel"
            )
            assert not sess_mod._cleanup_task.done(), (
                "_cleanup_task must still be running after caller cancel"
            )
            cleanup_task_ref = sess_mod._cleanup_task

            # Release dispose gate — teardown finishes.
            dispose_gate.set()

            # Wait for cleanup task to finish (bounded).
            # Poll the TASK OBJECT (not sess_mod._cleanup_task which becomes None
            # before the task fully returns — the lock is held until task exit).
            deadline = time.monotonic() + 30.0
            while not cleanup_task_ref.done():
                assert time.monotonic() < deadline, "_cleanup_task did not finish"
                await asyncio.sleep(0.01)
            # One extra turn for done-callbacks.
            await asyncio.sleep(0)

        # State is clean after teardown completed.
        assert sess_mod._engine is None, "Engine must be None after cleanup"
        assert sess_mod._AsyncSessionFactory is None
        assert sess_mod._cleanup_task is None

        # Subsequent init must succeed — no stuck-barrier hang.
        init_task = asyncio.create_task(sess_mod.init_database(str(db_path2)))
        try:
            await asyncio.wait_for(init_task, timeout=30.0)
        except TimeoutError:
            init_task.cancel()
            raise AssertionError(
                "init_database() timed out after cancelled cleanup — stuck barrier"
            ) from None
        assert sess_mod._engine is not None, (
            "init_database() must publish an engine after cleanup"
        )
        await sess_mod.cleanup_database()

    async def test_init_during_cleanup_waits_not_fast_path_success(self, tmp_path):
        """init_database() during cleanup-in-flight waits, not false success.

        While _do_cleanup is mid-dispose, a concurrent init_database() must NOT
        return via a stale lock-free engine check while cleanup is clearing the
        engine from under it.  It must wait for cleanup and then re-initialize.
        """
        import asyncio  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path1 = tmp_path / "init_during_cleanup.db"
        db_path2 = tmp_path / "after_cleanup.db"

        dispose_started = asyncio.Event()
        dispose_gate = asyncio.Event()
        original_dispose = sess_mod.AsyncEngine.dispose

        async def slow_dispose(self: object) -> None:
            dispose_started.set()
            await dispose_gate.wait()
            await original_dispose(self)

        # Reset _init_lock for this test's event loop (function-scoped).
        sess_mod._init_lock = None

        # Bring up a live engine.
        await sess_mod.init_database(str(db_path1))
        assert sess_mod._engine is not None
        engine_at_start = sess_mod._engine

        with unittest.mock.patch.object(sess_mod.AsyncEngine, "dispose", slow_dispose):
            # Start cleanup — will block in slow_dispose.
            cleanup_task = asyncio.create_task(sess_mod.cleanup_database())

            # Wait until cleanup is inside dispose (engine is still published).
            await dispose_started.wait()
            assert sess_mod._engine is not None, (
                "Engine should still be live inside dispose"
            )

            # Fire init while cleanup is mid-dispose.  It must NOT return
            # "success" via the stale engine — it must wait for cleanup.
            reinit_task = asyncio.create_task(sess_mod.init_database(str(db_path2)))

            # Give reinit a turn to start.
            await asyncio.sleep(0)

            # Assert: reinit is NOT done — it should be waiting for cleanup.
            assert not reinit_task.done(), (
                "init_database() must not return immediately while cleanup is active"
            )

            # Release dispose so cleanup can finish.
            dispose_gate.set()
            await cleanup_task

        # After cleanup: engine is None, reinit should proceed and publish.
        assert sess_mod._engine is None or sess_mod._engine is not engine_at_start, (
            "Cleanup must have disposed the original engine"
        )

        # Wait for reinit to complete.
        try:
            await asyncio.wait_for(reinit_task, timeout=30.0)
        except TimeoutError:
            reinit_task.cancel()
            raise AssertionError("reinit_task timed out after cleanup") from None

        assert sess_mod._engine is not None, (
            "Reinit must publish a fresh engine after cleanup completes"
        )
        assert sess_mod._engine is not engine_at_start, (
            "Reinit must publish a NEW engine, not the disposed one"
        )
        await sess_mod.cleanup_database()

    async def test_disposal_failure_clears_globals_and_fresh_init_succeeds(
        self, tmp_path
    ):
        """A failing engine.dispose() must not leave stale published state.

        V1 scenario: patch AsyncEngine.dispose to raise; call cleanup_database().
        The cleanup task fails with the disposal error.  Assert:
        - All three globals (_engine, _AsyncSessionFactory, _db_path) are None.
        - _cleanup_task is None (outer finally ran).
        - A subsequent init_database() actually calls _do_init (not the stale
          initialized branch) and publishes a fresh engine.
        """
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "disposal_failure.db"

        # Reset _init_lock for this test's event loop (function-scoped).
        sess_mod._init_lock = None

        # Bring up a live engine.
        await sess_mod.init_database(str(db_path))
        assert sess_mod._engine is not None

        dispose_error = RuntimeError("dispose-probe-failure")
        do_init_call_count: list[int] = [0]
        original_do_init = sess_mod._do_init

        async def counting_do_init(*args: object, **kwargs: object) -> None:
            do_init_call_count[0] += 1
            return await original_do_init(*args, **kwargs)

        async def failing_dispose(self: object) -> None:
            raise dispose_error

        # Phase 1: patch dispose to fail; patch _do_init to count calls.
        # Run cleanup (must raise) and the subsequent fresh init inside the
        # counting patch so we can assert _do_init ran exactly once.
        db_path2 = tmp_path / "after_disposal_failure.db"
        with (
            unittest.mock.patch.object(
                sess_mod.AsyncEngine, "dispose", failing_dispose
            ),
            unittest.mock.patch.object(sess_mod, "_do_init", counting_do_init),
        ):
            # Cleanup must raise (disposal exception propagates).
            try:
                await sess_mod.cleanup_database()
                raise AssertionError("cleanup_database() should have raised")
            except RuntimeError as exc:
                assert exc is dispose_error, f"Expected disposal error, got {exc!r}"

            # V1 assertion: all three globals are cleared despite the disposal failure.
            assert sess_mod._engine is None, "Engine must be None after failed disposal"
            assert sess_mod._AsyncSessionFactory is None
            assert sess_mod._db_path is None
            assert sess_mod._cleanup_task is None, (
                "_cleanup_task outer finally must run"
            )

            # V1 assertion: subsequent init actually runs _do_init (not stale initialized).
            # The failing_dispose patch is still active but irrelevant here — no engine
            # to dispose during init.  The counting_do_init patch is what we need.
            await sess_mod.init_database(str(db_path2))
            assert do_init_call_count[0] == 1, (
                f"_do_init must have been called once after failed disposal "
                f"(was called {do_init_call_count[0]} times)"
            )
            assert sess_mod._engine is not None

        await sess_mod.cleanup_database()

    async def test_cancelled_sole_cleanup_caller_then_disposal_failure_no_unretrieved(
        self, tmp_path
    ):
        """No 'Task exception was never retrieved' when sole cleanup caller is cancelled.

        V2 scenario: cancel the only cleanup_database() caller while teardown
        is in progress, then fail disposal.  Assert:
        - No asyncio "Task exception was never retrieved" warning is emitted.
        - The failure IS logged (via the done-callback's logger.error call).
        """
        import asyncio  # noqa: PLC0415
        import logging  # noqa: PLC0415
        import time  # noqa: PLC0415
        import unittest.mock  # noqa: PLC0415

        from snore.database import session as sess_mod  # noqa: PLC0415

        db_path = tmp_path / "cleanup_sole_caller_fail.db"

        # Reset _init_lock for this test's event loop (function-scoped).
        sess_mod._init_lock = None

        dispose_started = asyncio.Event()
        dispose_gate = asyncio.Event()

        async def slow_failing_dispose(self: object) -> None:
            dispose_started.set()
            await dispose_gate.wait()
            raise RuntimeError("cleanup-probe-failure")

        # Bring up a live engine.
        await sess_mod.init_database(str(db_path))
        assert sess_mod._engine is not None

        warning_messages: list[str] = []
        logged_errors: list[str] = []

        with (
            unittest.mock.patch.object(
                sess_mod.AsyncEngine, "dispose", slow_failing_dispose
            ),
            unittest.mock.patch.object(
                logging.getLogger("asyncio"),
                "error",
                side_effect=lambda msg, *a, **kw: warning_messages.append(str(msg)),
            ),
            unittest.mock.patch.object(
                sess_mod.logger,
                "error",
                side_effect=lambda msg, *a, **kw: logged_errors.append(str(msg)),
            ),
        ):
            cleanup_caller = asyncio.create_task(sess_mod.cleanup_database())

            # Wait until _do_cleanup is inside slow_failing_dispose.
            await dispose_started.wait()

            # Capture the shared task ref before cancelling the caller.
            cleanup_task_ref = sess_mod._cleanup_task
            assert cleanup_task_ref is not None

            # Cancel the sole caller.  _cleanup_task is shielded and keeps running.
            cleanup_caller.cancel()
            await asyncio.gather(cleanup_caller, return_exceptions=True)

            # Release the gate — disposal will fail.
            dispose_gate.set()

            # Wait (bounded) for the shared task to finish WITHOUT awaiting it —
            # nobody may retrieve the exception except the done-callback.
            deadline = time.monotonic() + 30.0
            while not cleanup_task_ref.done():
                assert time.monotonic() < deadline, "cleanup task did not finish"
                await asyncio.sleep(0.01)
            # One extra turn for done-callbacks.
            await asyncio.sleep(0)

        # V2 assertion 1: no asyncio "never retrieved" warning.
        unretrieved = [
            m for m in warning_messages if "exception was never retrieved" in m
        ]
        assert not unretrieved, (
            f"'Task exception was never retrieved' warning detected: {unretrieved}"
        )

        # V2 assertion 2: the done-callback logged the cleanup failure.
        assert any("cleanup failed" in m for m in logged_errors), (
            f"Expected a cleanup-failure error log from the done-callback; "
            f"got: {logged_errors}"
        )

        # Globals are clean (V1 inner finally ran despite the failure).
        assert sess_mod._engine is None
        assert sess_mod._AsyncSessionFactory is None
        assert sess_mod._cleanup_task is None
