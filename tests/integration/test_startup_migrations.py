"""
Tests for startup database migration behavior in init_database.

These tests verify:
- Fresh DB: tables created, alembic_version stamped at current head
- Idempotence: calling init_database twice is a no-op
"""

from pathlib import Path

import pytest

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

import snore.database as _snore_db_pkg

from snore.database.session import cleanup_database, init_database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrations_dir() -> str:
    return str(Path(_snore_db_pkg.__file__).parent / "migrations")


def _current_head() -> str:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _migrations_dir())
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert head is not None, "No head revision found in migrations directory"
    return head


def _read_version(db_path: str) -> str | None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        insp = sa_inspect(engine)
        if "alembic_version" not in insp.get_table_names():
            return None
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).fetchone()
            return row[0] if row else None
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartupMigrations:
    """Verify init_database applies alembic migrations correctly on first call."""

    async def test_fresh_database(self, tmp_path):
        """Fresh DB: tables created via create_all and version stamped at head."""
        db_path = str(tmp_path / "fresh.db")

        await init_database(db_path)

        head = _current_head()
        assert _read_version(db_path) == head

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            insp = sa_inspect(engine)
            tables = set(insp.get_table_names())
            assert "sessions" in tables
            assert "statistics" in tables
            assert "alembic_version" in tables
        finally:
            engine.dispose()

    async def test_idempotent(self, tmp_path):
        """Calling init_database twice on the same DB is a no-op."""
        db_path = str(tmp_path / "idempotent.db")

        await init_database(db_path)
        version_first = _read_version(db_path)

        # Reset global engine so the second init_database call actually re-runs
        # _apply_migrations; without this the early-return guard silently skips it.
        await cleanup_database()

        await init_database(db_path)
        version_second = _read_version(db_path)

        head = _current_head()
        assert version_first == head
        assert version_second == head

    async def test_unknown_revision_fails_loudly(self, tmp_path):
        """Pre-squash or unstamped DBs with an unknown revision fail loudly.

        Pre-alpha contract: delete the DB file and re-import rather than
        attempting an in-place migration from an unrecognized baseline.
        """
        db_path = str(tmp_path / "stale.db")

        await init_database(db_path)
        await cleanup_database()

        # Overwrite alembic_version with a revision absent from the migration chain.
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE alembic_version SET version_num='deadbeef0000'")
                )
        finally:
            engine.dispose()

        with pytest.raises(Exception, match="deadbeef0000|Can't locate"):
            await init_database(db_path)

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
