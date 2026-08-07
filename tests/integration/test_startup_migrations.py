"""
Tests for startup database schema-creation behavior in init_database.

Zero-migration contract (pre-1.0)
----------------------------------
With an empty ``versions/`` directory, ``_apply_migrations_sync`` manages the
schema exclusively via ``Base.metadata.create_all`` followed by
``_sync_additive_schema``.  No ``alembic_version`` table is created on this
path, and stale stamps from pre-flatten databases are silently ignored.
These tests verify:

- Fresh DB: tables created via create_all; alembic_version NOT stamped.
- Idempotence: calling init_database twice leaves tables intact with no
  alembic_version stamp.
- Stale stamp ignored: a pre-existing alembic_version row does not cause
  init_database to raise.
- Off-loop: _apply_migrations_sync runs via asyncio.to_thread (never blocks
  the event loop).
- Additive sync: columns and indexes missing from existing tables are added on
  the next startup.
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
    """Verify init_database creates schema correctly in zero-migration mode."""

    async def test_fresh_database(self, tmp_path):
        """Fresh DB: tables created via create_all; no alembic_version stamp.

        In zero-migration mode (empty versions/), schema is managed solely by
        Base.metadata.create_all.  The alembic_version table must NOT be
        present on a fresh database.
        """
        db_path = str(tmp_path / "fresh.db")

        await init_database(db_path)

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            insp = sa_inspect(engine)
            tables = set(insp.get_table_names())
            assert "sessions" in tables
            assert "statistics" in tables
            assert "alembic_version" not in tables
        finally:
            engine.dispose()
            await cleanup_database()

    async def test_idempotent(self, tmp_path):
        """Calling init_database twice on the same DB is a no-op.

        Both calls must succeed and leave core tables present.  No
        alembic_version stamp is written in zero-migration mode.
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
            assert "alembic_version" not in tables
        finally:
            engine.dispose()
            await cleanup_database()

    async def test_stale_stamp_ignored(self, tmp_path):
        """A stale alembic_version row is silently ignored in zero-migration mode.

        Zero-migration mode never reads alembic_version, so a leftover row
        from a pre-flatten database does not cause init_database to raise.
        Owners drop incompatible DB files manually; no automatic rollback.
        """
        db_path = str(tmp_path / "stale.db")

        # First init creates the schema.
        await init_database(db_path)
        await cleanup_database()

        # Manually create alembic_version with a stale stamp (first init no
        # longer creates this table in zero-migration mode, so we insert it
        # ourselves to simulate a DB that carried a pre-flatten stamp).
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS alembic_version "
                        "(version_num VARCHAR(32) NOT NULL)"
                    )
                )
                conn.execute(
                    text("INSERT INTO alembic_version VALUES ('deadbeef0000')")
                )
        finally:
            engine.dispose()

        # Re-init must succeed — zero-migration mode ignores the stale stamp.
        await init_database(db_path)
        await cleanup_database()

    async def test_additive_sync_adds_missing_column_and_index(self, tmp_path):
        """Additive sync restores columns and indexes absent from existing tables.

        Simulates a pre-#200 production DB: a user row is inserted, then
        ``google_link_disabled`` (NOT NULL, server_default='0') and
        ``display_name`` (nullable, no default) are dropped along with the
        ``ix_auth_identities_user_provider`` index.  A second ``init_database``
        call must restore all three, and the pre-existing row must read
        ``google_link_disabled = 0`` (server_default backfill by SQLite).
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
