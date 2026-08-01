"""
Tests for startup database migration behavior in init_database.

These tests verify:
- Fresh DB: tables created, alembic_version stamped at current head
- Idempotence: calling init_database twice is a no-op
"""

from pathlib import Path

import asyncio
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

    def test_fresh_database(self, tmp_path):
        """Fresh DB: tables created via create_all and version stamped at head."""
        db_path = str(tmp_path / "fresh.db")

        init_database(db_path)

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

    def test_idempotent(self, tmp_path):
        """Calling init_database twice on the same DB is a no-op."""
        db_path = str(tmp_path / "idempotent.db")

        init_database(db_path)
        version_first = _read_version(db_path)

        # Reset global engine so the second init_database call actually re-runs
        # _apply_migrations; without this the early-return guard silently skips it.
        asyncio.run(cleanup_database())

        init_database(db_path)
        version_second = _read_version(db_path)

        head = _current_head()
        assert version_first == head
        assert version_second == head

    def test_unknown_revision_fails_loudly(self, tmp_path):
        """Pre-squash or unstamped DBs with an unknown revision fail loudly.

        Pre-alpha contract: delete the DB file and re-import rather than
        attempting an in-place migration from an unrecognized baseline.
        """
        db_path = str(tmp_path / "stale.db")

        init_database(db_path)
        asyncio.run(cleanup_database())

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
            init_database(db_path)
