"""
Tests for startup database migration behavior in init_database.

These tests verify:
- Fresh DB: tables created, alembic_version stamped at current head
- Legacy unstamped DB without ipap columns: detected, stamped at 102cf96663ea, upgraded
- Legacy unstamped DB with ipap columns: detected, stamped at a3f8e9c12b45, upgraded
- Idempotence: calling init_database twice is a no-op
"""

from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

import snore.database as _snore_db_pkg

from snore.database.models import Base
from snore.database.session import cleanup_database, init_database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrations_dir() -> str:
    return str(Path(_snore_db_pkg.__file__).parent / "migrations")


def _alembic_cfg(db_path: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _migrations_dir())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


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

    def test_legacy_pre_ipap_database(self, tmp_path):
        """Unstamped DB at 102cf96663ea (no ipap cols): stamped then upgraded to head."""
        db_path = str(tmp_path / "legacy_pre_ipap.db")

        # Build a DB at the first revision only, then simulate never-stamped state
        alembic_command.upgrade(_alembic_cfg(db_path), "102cf96663ea")
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE alembic_version"))
            conn.commit()
        engine.dispose()

        init_database(db_path)

        head = _current_head()
        assert _read_version(db_path) == head

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            insp = sa_inspect(engine)
            stat_cols = {col["name"] for col in insp.get_columns("statistics")}
            assert "ipap_median" in stat_cols
        finally:
            engine.dispose()

    def test_legacy_head_equivalent_database(self, tmp_path):
        """Unstamped DB created via create_all (ipap cols present): stamped at a3f8e9c12b45 then upgraded to head."""
        db_path = str(tmp_path / "legacy_head_equiv.db")

        # Build DB from current models without running alembic at all
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()

        init_database(db_path)

        head = _current_head()
        assert _read_version(db_path) == head

    def test_idempotent(self, tmp_path):
        """Calling init_database twice on the same DB is a no-op."""
        db_path = str(tmp_path / "idempotent.db")

        init_database(db_path)
        version_first = _read_version(db_path)

        # Reset global state so init_database runs migration logic on second call
        cleanup_database()

        init_database(db_path)
        version_second = _read_version(db_path)

        head = _current_head()
        assert version_first == head
        assert version_second == head
