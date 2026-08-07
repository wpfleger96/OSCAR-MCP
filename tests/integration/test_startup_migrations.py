"""
Tests for startup database migration behavior in init_database.

These tests verify:
- Fresh DB: tables created, alembic_version stamped at current head
- Idempotence: calling init_database twice is a no-op
"""

import sqlite3

from pathlib import Path

import alembic.command
import pytest

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

import snore.database as _snore_db_pkg

from snore.database.session import (
    _build_alembic_config,
    cleanup_database,
    init_database,
)

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


# ---------------------------------------------------------------------------
# Regression: drop_chk_profile_deleting migration (a1b2c3d4e5f6)
# ---------------------------------------------------------------------------

# The migration prior to a1b2c3d4e5f6 — used as the "stop here then patch" point.
_PREV_REVISION = "dab8ad625898"

# DDL that pre-migration production DBs carry: bare constraint name with no
# naming-convention prefix.  Two variants are exercised: with and without the
# tautological check constraint.
_PROFILES_DDL_WITH_BARE_CONSTRAINT = """\
CREATE TABLE profiles (
    id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name VARCHAR(150) NOT NULL,
    username VARCHAR(100),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    date_of_birth DATE,
    height_cm INTEGER,
    settings TEXT NOT NULL,
    deleting_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_profile_user_name UNIQUE (user_id, name),
    CONSTRAINT chk_profile_name CHECK (length(name) > 0),
    CONSTRAINT chk_profile_deleting CHECK (deleting_at IS NULL OR deleting_at IS NOT NULL),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)\
"""

_PROFILES_DDL_WITHOUT_CONSTRAINT = """\
CREATE TABLE profiles (
    id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name VARCHAR(150) NOT NULL,
    username VARCHAR(100),
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    date_of_birth DATE,
    height_cm INTEGER,
    settings TEXT NOT NULL,
    deleting_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_profile_user_name UNIQUE (user_id, name),
    CONSTRAINT chk_profile_name CHECK (length(name) > 0),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)\
"""


def _build_db_at_prev_revision_with_custom_profiles(
    db_path: str, profiles_ddl: str
) -> None:
    """Migrate to _PREV_REVISION, then swap in a custom profiles DDL.

    This reproduces a pre-migration production DB: run the chain up to the
    revision that precedes the constraint-drop, then drop and recreate
    ``profiles`` with the caller-supplied DDL so constraint name variants
    can be tested in isolation.
    """
    cfg = _build_alembic_config(f"sqlite:///{db_path}")
    alembic.command.upgrade(cfg, _PREV_REVISION)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE profiles")
        conn.execute(profiles_ddl)
        conn.commit()
    finally:
        conn.close()


def _profiles_ddl(db_path: str) -> str:
    """Return the raw DDL string for the profiles table from sqlite_master."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='profiles'"
        ).fetchone()
        assert row is not None, "profiles table not found in sqlite_master"
        return row[0]
    finally:
        conn.close()


class TestDropChkProfileDeletingMigration:
    """Regression tests for migration a1b2c3d4e5f6 (drop_chk_profile_deleting).

    Three scenarios exercise the three DB states that can exist in production:
    1. Fresh chain   — constraint was never present under the bare name.
    2. Bare name     — pre-migration DB carries ``chk_profile_deleting``.
    3. Absent        — constraint already gone; migration must be a no-op.
    """

    def test_fresh_chain_constraint_dropped(self, tmp_path):
        """Running the full migration chain removes any chk_profile_deleting DDL."""
        db_path = str(tmp_path / "fresh.db")
        cfg = _build_alembic_config(f"sqlite:///{db_path}")
        alembic.command.upgrade(cfg, "head")

        assert "chk_profile_deleting" not in _profiles_ddl(db_path)
        assert _read_version(db_path) == _current_head()

    def test_bare_constraint_name_dropped(self, tmp_path):
        """Pre-migration DB with bare name ``chk_profile_deleting`` is cleaned up."""
        db_path = str(tmp_path / "bare.db")
        _build_db_at_prev_revision_with_custom_profiles(
            db_path, _PROFILES_DDL_WITH_BARE_CONSTRAINT
        )

        # Sanity-check that we actually planted the bare constraint name.
        assert "chk_profile_deleting" in _profiles_ddl(db_path)

        cfg = _build_alembic_config(f"sqlite:///{db_path}")
        alembic.command.upgrade(cfg, "head")

        assert "chk_profile_deleting" not in _profiles_ddl(db_path)
        assert _read_version(db_path) == _current_head()

    def test_absent_constraint_no_raise(self, tmp_path):
        """Migration is a no-op when chk_profile_deleting is already absent."""
        db_path = str(tmp_path / "absent.db")
        _build_db_at_prev_revision_with_custom_profiles(
            db_path, _PROFILES_DDL_WITHOUT_CONSTRAINT
        )

        # Confirm the constraint is absent before we run the drop migration.
        assert "chk_profile_deleting" not in _profiles_ddl(db_path)

        cfg = _build_alembic_config(f"sqlite:///{db_path}")
        # Must not raise even though the target constraint does not exist.
        alembic.command.upgrade(cfg, "head")

        assert "chk_profile_deleting" not in _profiles_ddl(db_path)
        assert _read_version(db_path) == _current_head()
