"""
Tests for startup database schema-creation behavior in init_database.

Zero-migration contract (pre-1.0)
----------------------------------
With an empty ``versions/`` directory, ``_apply_migrations_sync`` manages the
schema exclusively via ``Base.metadata.create_all``.  No ``alembic_version``
table is created on this path, and stale stamps from pre-flatten databases are
silently ignored.  These tests verify:

- Fresh DB: tables created via create_all; alembic_version NOT stamped.
- Idempotence: calling init_database twice leaves tables intact with no
  alembic_version stamp.
- Stale stamp ignored: a pre-existing alembic_version row does not cause
  init_database to raise.
- Off-loop: _apply_migrations_sync runs via asyncio.to_thread (never blocks
  the event loop).
"""

from sqlalchemy import create_engine, text
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

        migration_calls = [c for c in calls if c[0] is _apply_migrations_sync]
        assert len(migration_calls) >= 1, (
            "``_apply_migrations_sync`` must be called via ``asyncio.to_thread``; "
            "got zero such calls — migrations may be running on the event loop"
        )

        await cleanup_database()
