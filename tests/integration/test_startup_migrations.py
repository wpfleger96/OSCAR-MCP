"""
Tests for startup database schema-creation behavior in init_database.

Migration-chain contract (post-migration introduction)
------------------------------------------------------
With a non-empty ``versions/`` directory, ``_apply_migrations_sync`` manages the
schema via Alembic.  ``_sync_additive_schema`` remains the safety net for
pre-chain (unstamped) databases, whose drift is not covered by migration files.
These tests verify:

- Fresh DB: tables created via create_all and stamped at head; alembic_version
  IS present after first init.
- Idempotence: calling init_database twice is a fast-path no-op; tables and
  alembic_version remain intact.
- Existing unstamped DB: a DB created in zero-migration mode (tables exist but
  no alembic_version) is automatically stamped at 001_baseline and upgraded to
  head — no manual operator action required.
- Off-loop: _apply_migrations_sync runs via asyncio.to_thread (never blocks
  the event loop).
- Additive sync: columns and indexes missing from a pre-chain (unstamped)
  database are added when it is stamped and upgraded.
- NOT NULL guard: _sync_additive_schema raises RuntimeError when a new NOT NULL
  column lacks a server_default.
"""

import pytest

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy import inspect as sa_inspect

from snore.database.session import cleanup_database, init_database

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartupMigrations:
    """Verify init_database creates schema correctly."""

    async def test_fresh_database(self, tmp_path):
        """Fresh DB: tables created and alembic_version stamped at head.

        With a migration chain present, the schema is created via create_all
        and then stamped.  The alembic_version table IS present after first init.
        """
        db_path = str(tmp_path / "fresh.db")

        await init_database(db_path)

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            insp = sa_inspect(engine)
            tables = set(insp.get_table_names())
            assert "sessions" in tables
            assert "statistics" in tables
            assert "alembic_version" in tables
        finally:
            engine.dispose()
            await cleanup_database()

    async def test_idempotent(self, tmp_path):
        """Calling init_database twice on the same DB is a no-op.

        Both calls must succeed and leave core tables and alembic_version
        present.  The second call takes the fast-path (stamp matches head).
        """
        db_path = str(tmp_path / "idempotent.db")

        await init_database(db_path)
        await cleanup_database()
        await init_database(db_path)

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            insp = sa_inspect(engine)
            tables = set(insp.get_table_names())
            assert "sessions" in tables
            assert "statistics" in tables
            assert "alembic_version" in tables
        finally:
            engine.dispose()
            await cleanup_database()

    async def test_existing_unstamped_db_gets_stamped_and_upgraded(self, tmp_path):
        """A DB created in zero-migration mode (no alembic_version) is auto-upgraded.

        Simulates the scenario where an operator has a DB that was created by
        Base.metadata.create_all before this migration chain existed.  The
        startup path must stamp at 001_baseline and upgrade to head rather than
        raising an error.
        """
        db_path = str(tmp_path / "unstamped.db")

        # Simulate a DB from zero-migration mode: tables exist, no alembic_version.
        from snore.database.models import Base

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(engine)
            insp = sa_inspect(engine)
            assert "sessions" in insp.get_table_names()
            assert "alembic_version" not in insp.get_table_names()
        finally:
            engine.dispose()

        # Re-init must succeed (no error raised) and stamp the DB.
        await init_database(db_path)

        engine2 = create_engine(f"sqlite:///{db_path}")
        try:
            insp2 = sa_inspect(engine2)
            tables = set(insp2.get_table_names())
            assert "alembic_version" in tables
            assert "sessions" in tables
        finally:
            engine2.dispose()
            await cleanup_database()

    async def test_additive_sync_adds_missing_column_and_index(self, tmp_path):
        """Additive sync restores columns and indexes absent from a pre-chain DB.

        Simulates a pre-#200 production DB — which by definition predates the
        migration chain, so ``alembic_version`` is also dropped: a user row is
        inserted, then ``google_link_disabled`` (NOT NULL, server_default='0')
        and ``display_name`` (nullable, no default) are dropped along with the
        ``ix_auth_identities_user_provider`` index.  The next ``init_database``
        call routes through the unstamped-existing-DB path (stamp + upgrade +
        additive sync) and must restore all three, with the pre-existing row
        reading ``google_link_disabled = 0`` (server_default backfill by SQLite).
        """
        db_path = str(tmp_path / "additive_sync.db")

        # First init: create the full current schema.
        await init_database(db_path)
        await cleanup_database()

        # Insert a user row with all NOT NULL fields before the columns are dropped.
        # This lets us verify the server_default backfill on re-add.
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO users "
                        "(canonical_email, role, session_version, created_at, updated_at, google_link_disabled) "
                        "VALUES ('pre_200@example.com', 'member', 0, "
                        "'2024-01-01T00:00:00', '2024-01-01T00:00:00', 0)"
                    )
                )
                conn.execute(text("ALTER TABLE users DROP COLUMN google_link_disabled"))
                conn.execute(text("ALTER TABLE users DROP COLUMN display_name"))
                conn.execute(text("DROP INDEX ix_auth_identities_user_provider"))
                # A pre-chain DB has no Alembic stamp; dropping it routes the
                # next init through the unstamped-existing-DB path.
                conn.execute(text("DROP TABLE alembic_version"))
        finally:
            engine.dispose()

        # Second init: additive sync must restore both columns and the index.
        await init_database(db_path)

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            insp = sa_inspect(engine)
            col_names = {c["name"] for c in insp.get_columns("users")}
            assert "google_link_disabled" in col_names, (
                "additive sync must have added google_link_disabled"
            )
            assert "display_name" in col_names, (
                "additive sync must have added display_name"
            )
            idx_names = {i["name"] for i in insp.get_indexes("auth_identities")}
            assert "ix_auth_identities_user_provider" in idx_names, (
                "additive sync must have created ix_auth_identities_user_provider"
            )
            # Pre-existing row must have google_link_disabled backfilled to 0
            # by SQLite's ADD COLUMN DEFAULT '0' behaviour.
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT google_link_disabled FROM users "
                        "WHERE canonical_email = 'pre_200@example.com'"
                    )
                ).fetchone()
            assert row is not None, "pre-existing user row must survive re-init"
            assert not row[0], (
                "google_link_disabled must be backfilled to 0 for pre-existing rows"
            )
        finally:
            engine.dispose()
            await cleanup_database()

    async def test_additive_sync_not_null_without_default_raises(self, tmp_path):
        """_sync_additive_schema raises RuntimeError for NOT NULL columns with no server_default.

        SQLite cannot ADD COLUMN NOT NULL without a default value.  The helper
        must detect this and raise with an actionable message naming the column.
        """
        from snore.database.session import _sync_additive_schema

        db_path = str(tmp_path / "not_null_guard.db")

        # Create a v1 table with just a primary key.
        v1_meta = MetaData()
        Table("items", v1_meta, Column("id", Integer, primary_key=True))
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            v1_meta.create_all(engine)

            # Build v2 metadata describing the same table plus a NOT NULL column
            # that has no server_default.  _sync_additive_schema must reject this.
            v2_meta = MetaData()
            Table(
                "items",
                v2_meta,
                Column("id", Integer, primary_key=True),
                Column("required_field", String(50), nullable=False),
            )

            with pytest.raises(RuntimeError, match="required_field"):
                _sync_additive_schema(v2_meta, engine)
        finally:
            engine.dispose()

    async def test_additive_sync_race_tolerance(self, tmp_path):
        """Concurrent column/index additions are silently skipped; unrelated errors re-raise.

        Simulates rolling-restart races: the helper's inspector holds a stale
        pre-race snapshot (column/index appears missing) while another process
        already applied the change.  Three scenarios are exercised:

        - Column added concurrently → "duplicate column name" OperationalError swallowed.
        - Index created concurrently → "already exists" OperationalError swallowed
          (patching ``Index.create`` because the TOCTOU window between checkfirst's
          PRAGMA and CREATE INDEX is too tight to reproduce reliably in a unit test).
        - Unrelated OperationalError (e.g. "database is locked") propagates.
        """
        from unittest.mock import MagicMock, patch

        from sqlalchemy.exc import OperationalError

        from snore.database.session import _sync_additive_schema

        db_path = str(tmp_path / "race_tolerance.db")

        # v1: items table with just id.
        v1_meta = MetaData()
        Table("items", v1_meta, Column("id", Integer, primary_key=True))
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            v1_meta.create_all(engine)

            # v2: adds a nullable column and an index.
            v2_meta = MetaData()
            items_v2 = Table(
                "items",
                v2_meta,
                Column("id", Integer, primary_key=True),
                Column("val", String(50), nullable=True),
            )
            idx_v2 = Index("ix_items_val", items_v2.c.val)

            # Another process already added "val" to the DB before us.
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE items ADD COLUMN val VARCHAR(50)"))

            # 1. Column race: stale inspector says "val" is missing; the DB has it.
            #    ALTER TABLE raises "duplicate column name: val" — must be swallowed.
            stale_col = MagicMock()
            stale_col.get_table_names.return_value = ["items"]
            stale_col.get_columns.return_value = [{"name": "id"}]
            stale_col.get_indexes.return_value = [{"name": "ix_items_val"}]
            with patch("snore.database.session.inspect", return_value=stale_col):
                _sync_additive_schema(v2_meta, engine)  # must not raise

            # 2. Index race: stale inspector says index is missing; patch idx.create
            #    to raise "already exists" as the losing process would see.
            stale_idx = MagicMock()
            stale_idx.get_table_names.return_value = ["items"]
            stale_idx.get_columns.return_value = [{"name": "id"}, {"name": "val"}]
            stale_idx.get_indexes.return_value = []
            concurrent_idx_err = OperationalError(
                "index ix_items_val already exists", None, None
            )
            with patch("snore.database.session.inspect", return_value=stale_idx):
                with patch.object(idx_v2, "create", side_effect=concurrent_idx_err):
                    _sync_additive_schema(v2_meta, engine)  # must not raise

            # 3. Unrelated OperationalError from the index path must propagate.
            locked_err = OperationalError("database is locked", None, None)
            with patch("snore.database.session.inspect", return_value=stale_idx):
                with patch.object(idx_v2, "create", side_effect=locked_err):
                    with pytest.raises(OperationalError, match="database is locked"):
                        _sync_additive_schema(v2_meta, engine)
        finally:
            engine.dispose()

    async def test_migrations_run_off_event_loop(self, tmp_path):
        """``init_database`` runs ``_apply_migrations_sync`` via ``asyncio.to_thread``.

        This test verifies that the migration call is never made directly from
        the event loop — if it were, the ``to_thread`` wrapper would be absent
        and blocking I/O would stall uvicorn.

        Strategy: patch ``asyncio.to_thread`` globally to record calls, then confirm
        it was invoked with ``_apply_migrations_sync`` as the first positional arg.
        """
        import asyncio

        from unittest.mock import patch

        from snore.database.session import _apply_migrations_sync

        calls: list[tuple] = []
        original_to_thread = asyncio.to_thread

        async def _recording_to_thread(func, *args, **kwargs):
            calls.append((func, args, kwargs))
            return await original_to_thread(func, *args, **kwargs)

        db_path = str(tmp_path / "off_loop.db")

        # Patch at the asyncio module level so the inline ``import asyncio``
        # inside ``init_database`` picks up the same patched function.
        with patch.object(asyncio, "to_thread", side_effect=_recording_to_thread):
            await init_database(db_path)

        try:
            migration_calls = [c for c in calls if c[0] is _apply_migrations_sync]
            assert len(migration_calls) >= 1, (
                "``_apply_migrations_sync`` must be called via ``asyncio.to_thread``; "
                "got zero such calls — migrations may be running on the event loop"
            )
        finally:
            await cleanup_database()

    async def test_checksums_recorded_on_fresh_init(self, tmp_path):
        """snore_migration_checksums is populated with 64-char hex digests on first init.

        Every revision in the migration chain must have exactly one checksum row
        and the value must be the lowercase SHA-256 hexdigest (64 characters) of
        the migration file's raw bytes.  The row count must equal the number of
        revisions in the chain — no more, no fewer.
        """
        import sqlite3

        from alembic.script import ScriptDirectory

        from snore.database.session import _build_alembic_config

        db_path = str(tmp_path / "fresh_checksum.db")
        await init_database(db_path)

        script_dir = ScriptDirectory.from_config(
            _build_alembic_config(f"sqlite:///{db_path}")
        )
        expected_count = len(list(script_dir.walk_revisions()))

        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT checksum FROM snore_migration_checksums"
            ).fetchall()
            assert len(rows) == expected_count, (
                f"expected exactly {expected_count} checksum rows (one per chain "
                f"revision), got {len(rows)}"
            )
            for (checksum,) in rows:
                assert len(checksum) == 64, (
                    f"checksum must be 64 hex chars, got {len(checksum)}: {checksum!r}"
                )
                assert all(c in "0123456789abcdef" for c in checksum), (
                    f"checksum must be lowercase hex, got {checksum!r}"
                )
        finally:
            con.close()
            await cleanup_database()

    async def test_checksum_mismatch_triggers_replay(self, tmp_path, caplog):
        """Corrupt stored checksum for head triggers a WARNING and rewrites the correct hash.

        The head revision is resolved dynamically so the test remains valid as
        new migrations are added to the chain.  After corrupting the head revision's
        stored checksum via raw sqlite3, the next init_database call must:
        - emit a WARNING on logger "snore.database.session" mentioning the revision;
        - complete successfully with alembic_version still at head; and
        - overwrite the stored checksum with the correct 64-char hex digest.
        """
        import logging
        import sqlite3

        from alembic.script import ScriptDirectory

        from snore.database.session import _build_alembic_config

        db_path = str(tmp_path / "mismatch.db")
        await init_database(db_path)
        await cleanup_database()

        head = ScriptDirectory.from_config(
            _build_alembic_config(f"sqlite:///{db_path}")
        ).get_current_head()

        # Corrupt the stored checksum for the head revision.
        con = sqlite3.connect(db_path)
        con.execute(
            "UPDATE snore_migration_checksums SET checksum='cafebabe' WHERE revision=?",
            (head,),
        )
        con.commit()
        con.close()

        with caplog.at_level(logging.WARNING, logger="snore.database.session"):
            await init_database(db_path)

        # A WARNING mentioning the edited revision must have been emitted.
        mismatch_records = [
            r for r in caplog.records if "Migration checksum mismatch" in r.message
        ]
        assert any(head in r.message for r in mismatch_records), (
            f"expected a WARNING mentioning {head!r}; "
            f"got records: {[r.message for r in caplog.records]}"
        )

        # DB must still be stamped at head after replay.
        con = sqlite3.connect(db_path)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            assert row is not None
            assert row[0] == head, f"expected version_num=={head!r}, got {row[0]!r}"

            # The stored checksum for head must have been corrected.
            row = con.execute(
                "SELECT checksum FROM snore_migration_checksums WHERE revision=?",
                (head,),
            ).fetchone()
            assert row is not None
            assert row[0] != "cafebabe", "stored checksum must have been overwritten"
            assert len(row[0]) == 64, (
                f"corrected checksum must be 64 hex chars, got {len(row[0])}"
            )
            assert all(c in "0123456789abcdef" for c in row[0]), (
                f"corrected checksum must be lowercase hex, got {row[0]!r}"
            )
        finally:
            con.close()
            await cleanup_database()

    async def test_backfill_no_replay_on_pre_feature_db(self, tmp_path, caplog):
        """Pre-feature DB (checksum table absent) is backfilled without triggering replay.

        A DB that predates the checksum feature has no snore_migration_checksums table.
        The next init_database call must silently create and backfill the table — no
        WARNING about a mismatch, no INFO about replay completion.
        """
        import logging
        import sqlite3

        db_path = str(tmp_path / "pre_feature.db")
        await init_database(db_path)
        await cleanup_database()

        # Simulate a pre-feature DB by dropping the checksum table.
        con = sqlite3.connect(db_path)
        con.execute("DROP TABLE snore_migration_checksums")
        con.commit()
        con.close()

        with caplog.at_level(logging.WARNING, logger="snore.database.session"):
            await init_database(db_path)

        # No mismatch or replay messages must have been logged.
        noisy_records = [
            r
            for r in caplog.records
            if "mismatch" in r.message.lower() or "replay" in r.message.lower()
        ]
        assert not noisy_records, (
            "pre-feature DB must not trigger mismatch/replay logs; "
            f"got: {[r.message for r in noisy_records]}"
        )

        # Table must be recreated and backfilled with at least one row.
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT revision FROM snore_migration_checksums"
            ).fetchall()
            assert len(rows) > 0, (
                "snore_migration_checksums must be backfilled after pre-feature DB init"
            )
        finally:
            con.close()
            await cleanup_database()

    async def test_matching_checksums_fast_path_skips(self, tmp_path, caplog):
        """Second init with all checksums matching takes the fast-path skip.

        When the DB is already stamped at head and every stored checksum matches
        the migration file on disk, init_database must emit a DEBUG log containing
        "matching checksums" on logger "snore.database.session" and skip Alembic.
        """
        import logging

        db_path = str(tmp_path / "fast_path.db")
        await init_database(db_path)
        await cleanup_database()

        with caplog.at_level(logging.DEBUG, logger="snore.database.session"):
            await init_database(db_path)

        assert any("matching checksums" in r.message for r in caplog.records), (
            "fast-path skip must emit a DEBUG log containing 'matching checksums'; "
            f"got records: {[r.message for r in caplog.records]}"
        )

        await cleanup_database()

    async def test_data_outside_replay_range_survives(self, tmp_path):
        """Rows in tables untouched by revision 009 survive downgrade+upgrade replay.

        import_job_records is unaffected by revision 009 (which only alters mask_log
        column nullability and CHECK constraints).  A sentinel row inserted before
        the checksum corruption must still be present after the downgrade(008)+
        upgrade(head) cycle.
        """
        import sqlite3

        db_path = str(tmp_path / "data_survives.db")
        await init_database(db_path)
        await cleanup_database()

        # Insert a sentinel row into import_job_records — a table with no FK
        # dependencies and untouched by revision 009.
        sentinel_job_id = "sentinel-checksum-test-01"
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO import_job_records "
            "(job_id, job_type, state, file_count, created_at, updated_at) "
            "VALUES (?, 'edf', 'pending', 0, "
            "'2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00')",
            (sentinel_job_id,),
        )
        # Corrupt revision 009's checksum to force a replay.
        con.execute(
            "UPDATE snore_migration_checksums SET checksum='cafebabe' "
            "WHERE revision='009_mask_log_optional_fields'"
        )
        con.commit()
        con.close()

        # Re-init: replay downgrades to 008 and re-upgrades to head.
        await init_database(db_path)

        # Sentinel row must have survived the replay.
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT job_id FROM import_job_records WHERE job_id=?",
                (sentinel_job_id,),
            ).fetchone()
            assert row is not None, (
                "sentinel row in import_job_records must survive the 009 downgrade+upgrade replay"
            )
        finally:
            con.close()
            await cleanup_database()

    async def test_mid_chain_edit_replays_from_edited_revision(self, tmp_path, caplog):
        """Corrupt stored checksum for a mid-chain revision triggers replay from that point.

        Corrupting 007_str_extras — not the head — forces the runner to walk the
        chain back past the clean tip revisions (009, 008) to detect the earliest
        mismatch, then downgrade to before 007 and upgrade back to head.  The
        WARNING must name the corrupted revision and the DB must end at head with
        all checksums valid.
        """
        import logging
        import sqlite3

        from alembic.script import ScriptDirectory

        from snore.database.session import _build_alembic_config

        db_path = str(tmp_path / "mid_chain_mismatch.db")
        alembic_cfg = _build_alembic_config(f"sqlite:///{db_path}")
        head = ScriptDirectory.from_config(alembic_cfg).get_current_head()

        await init_database(db_path)
        await cleanup_database()

        # Corrupt a mid-chain revision's stored checksum.
        con = sqlite3.connect(db_path)
        con.execute(
            "UPDATE snore_migration_checksums SET checksum='deadbeef' "
            "WHERE revision='007_str_extras'"
        )
        con.commit()
        con.close()

        with caplog.at_level(logging.WARNING, logger="snore.database.session"):
            await init_database(db_path)

        # WARNING must name the corrupted revision.
        mismatch_records = [
            r for r in caplog.records if "Migration checksum mismatch" in r.message
        ]
        assert any("007_str_extras" in r.message for r in mismatch_records), (
            "expected a WARNING mentioning '007_str_extras'; "
            f"got records: {[r.message for r in caplog.records]}"
        )

        # DB must be at head with all checksums corrected.
        con = sqlite3.connect(db_path)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            assert row is not None
            assert row[0] == head, f"expected version_num=={head!r}, got {row[0]!r}"

            rows = con.execute(
                "SELECT checksum FROM snore_migration_checksums"
            ).fetchall()
            assert len(rows) > 0
            for (checksum,) in rows:
                assert len(checksum) == 64, (
                    f"all checksums must be 64-char hex after replay, got {checksum!r}"
                )
                assert all(c in "0123456789abcdef" for c in checksum), (
                    f"checksum must be lowercase hex, got {checksum!r}"
                )
        finally:
            con.close()
            await cleanup_database()

    async def test_behind_head_with_edited_applied_revision(self, tmp_path, caplog):
        """DB behind head with a corrupt applied-revision checksum replays and upgrades.

        Simulates a DB that was last written when only revisions 001-007 existed:
        alembic_version is downgraded to 007_str_extras, checksum rows for 008 and
        009 are deleted (they didn't exist yet), and 005_analysis_job_records is
        corrupted.  Re-init must: warn about 005, downgrade to before 005, upgrade
        all the way to head, and leave exactly one valid checksum row per chain
        revision.
        """
        import logging
        import sqlite3

        from alembic import command as alembic_command
        from alembic.script import ScriptDirectory

        from snore.database.session import _build_alembic_config

        db_path = str(tmp_path / "behind_head.db")
        alembic_cfg = _build_alembic_config(f"sqlite:///{db_path}")
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head = script_dir.get_current_head()
        expected_count = len(list(script_dir.walk_revisions()))

        await init_database(db_path)
        await cleanup_database()

        # Downgrade the DB to 007_str_extras, leaving it behind head.
        alembic_command.downgrade(alembic_cfg, "007_str_extras")

        con = sqlite3.connect(db_path)
        # Remove checksum rows for revisions that post-date this DB's vintage,
        # simulating a DB written before 008 and 009 were introduced.
        con.execute(
            "DELETE FROM snore_migration_checksums "
            "WHERE revision IN ('008_mask_log', '009_mask_log_optional_fields')"
        )
        # Corrupt an applied revision's stored checksum to trigger replay.
        con.execute(
            "UPDATE snore_migration_checksums SET checksum='cafebabe' "
            "WHERE revision='005_analysis_job_records'"
        )
        con.commit()
        con.close()

        with caplog.at_level(logging.WARNING, logger="snore.database.session"):
            await init_database(db_path)

        # WARNING must name the corrupted revision.
        mismatch_records = [
            r for r in caplog.records if "Migration checksum mismatch" in r.message
        ]
        assert any("005_analysis_job_records" in r.message for r in mismatch_records), (
            "expected a WARNING mentioning '005_analysis_job_records'; "
            f"got records: {[r.message for r in caplog.records]}"
        )

        # DB must be at head with exactly one valid checksum row per chain revision.
        con = sqlite3.connect(db_path)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            assert row is not None
            assert row[0] == head, f"expected version_num=={head!r}, got {row[0]!r}"

            rows = con.execute(
                "SELECT checksum FROM snore_migration_checksums"
            ).fetchall()
            assert len(rows) == expected_count, (
                f"expected {expected_count} checksum rows after replay, got {len(rows)}"
            )
            for (checksum,) in rows:
                assert len(checksum) == 64, (
                    f"all checksums must be 64-char hex after replay, got {checksum!r}"
                )
                assert all(c in "0123456789abcdef" for c in checksum), (
                    f"checksum must be lowercase hex, got {checksum!r}"
                )
        finally:
            con.close()
            await cleanup_database()

    async def test_empty_checksum_table_triggers_backfill(self, tmp_path, caplog):
        """Empty snore_migration_checksums table (all rows deleted) is backfilled silently.

        An empty table (table present, zero rows) is treated as a missing baseline:
        init_database must repopulate it without emitting any mismatch or replay
        warnings, and the result must have exactly one row per chain revision.
        Regression test for the empty-baseline fast-path bug.
        """
        import logging
        import sqlite3

        from alembic.script import ScriptDirectory

        from snore.database.session import _build_alembic_config

        db_path = str(tmp_path / "empty_checksum.db")
        alembic_cfg = _build_alembic_config(f"sqlite:///{db_path}")
        expected_count = len(
            list(ScriptDirectory.from_config(alembic_cfg).walk_revisions())
        )

        await init_database(db_path)
        await cleanup_database()

        # Delete all rows, leaving the table structure intact.
        con = sqlite3.connect(db_path)
        con.execute("DELETE FROM snore_migration_checksums")
        con.commit()
        con.close()

        with caplog.at_level(logging.WARNING, logger="snore.database.session"):
            await init_database(db_path)

        # No mismatch or replay warnings must have been logged.
        noisy_records = [
            r
            for r in caplog.records
            if "mismatch" in r.message.lower() or "replay" in r.message.lower()
        ]
        assert not noisy_records, (
            "empty checksum baseline must not trigger mismatch/replay logs; "
            f"got: {[r.message for r in noisy_records]}"
        )

        # Table must be backfilled with exactly one row per chain revision.
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT checksum FROM snore_migration_checksums"
            ).fetchall()
            assert len(rows) == expected_count, (
                f"expected {expected_count} checksum rows after backfill, got {len(rows)}"
            )
            for (checksum,) in rows:
                assert len(checksum) == 64, (
                    f"backfilled checksum must be 64 hex chars, got {checksum!r}"
                )
        finally:
            con.close()
            await cleanup_database()

    async def test_baseline_edit_requires_explicit_opt_in(self, tmp_path, monkeypatch):
        """Corrupted 001_baseline checksum raises RuntimeError without the env-var opt-in.

        Editing the baseline migration file is almost certainly a mistake, so the
        runner refuses to replay it unless ``SNORE_ALLOW_BASELINE_REPLAY=1`` is set.
        Without the opt-in, init_database must raise RuntimeError with a message
        naming the env var.  With the opt-in set via monkeypatch, init_database
        must succeed, stamp the DB at head, and write valid checksums.
        """
        import sqlite3

        from alembic.script import ScriptDirectory

        from snore.database.session import _build_alembic_config

        db_path = str(tmp_path / "baseline_gate.db")
        alembic_cfg = _build_alembic_config(f"sqlite:///{db_path}")
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head = script_dir.get_current_head()
        expected_count = len(list(script_dir.walk_revisions()))

        await init_database(db_path)
        await cleanup_database()

        # Corrupt the stored checksum for the baseline revision.
        con = sqlite3.connect(db_path)
        con.execute(
            "UPDATE snore_migration_checksums SET checksum='cafebabe' "
            "WHERE revision='001_baseline'"
        )
        con.commit()
        con.close()

        # Without the opt-in env var, init_database must raise.
        with pytest.raises(RuntimeError, match="SNORE_ALLOW_BASELINE_REPLAY"):
            await init_database(db_path)
        # Clean up any partial session state left by the failed init.
        await cleanup_database()

        # With the opt-in set, init_database must succeed.
        monkeypatch.setenv("SNORE_ALLOW_BASELINE_REPLAY", "1")
        await init_database(db_path)

        con = sqlite3.connect(db_path)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            assert row is not None
            assert row[0] == head, f"expected version_num=={head!r}, got {row[0]!r}"

            rows = con.execute(
                "SELECT checksum FROM snore_migration_checksums"
            ).fetchall()
            assert len(rows) == expected_count, (
                f"expected {expected_count} checksum rows after opt-in replay, got {len(rows)}"
            )
            for (checksum,) in rows:
                assert len(checksum) == 64, (
                    f"checksum must be 64-char hex after opt-in replay, got {checksum!r}"
                )
                assert all(c in "0123456789abcdef" for c in checksum), (
                    f"checksum must be lowercase hex, got {checksum!r}"
                )
        finally:
            con.close()
            await cleanup_database()
