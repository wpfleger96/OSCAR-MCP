"""
Drift guard: verify the alembic migration chain produces a schema that matches
snore.database.models.Base.metadata with no structural gaps.

Why a structural-only check: SQLite does not preserve column nullability
information in a form alembic's reflection layer can recover (SQLite treats
all columns as nullable unless a NOT NULL constraint is stored verbatim in the
CREATE TABLE SQL, and alembic's SQLiteDialect reflection returns nullable=True
for those columns regardless of what the model declares). This means
compare_metadata always reports ``modify_nullable`` diffs against a SQLite DB.
Those are reflection noise, not schema drift. The assertion here therefore
covers only structural operations — missing/extra tables or columns — which are
the category of drift that actually breaks the application.
"""

from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine

import snore.database as _snore_db_pkg

from snore.database.models import Base

# Structural diff types that represent genuine schema gaps.
_STRUCTURAL_DIFF_TYPES = frozenset(
    {"add_table", "remove_table", "add_column", "remove_column"}
)

# Naming convention from migrations/env.py — must match so constraint name
# comparisons use the same pattern alembic used when creating the tables.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


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


def _structural_diffs(engine: Engine) -> list[tuple[Any, ...]]:
    """Return only structural (add/remove table/column) diffs against Base.metadata."""
    Base.metadata.naming_convention = _NAMING_CONVENTION
    with engine.connect() as conn:
        mc = MigrationContext.configure(
            conn,
            opts={"compare_type": False, "compare_server_default": False},
        )
        all_diffs = compare_metadata(mc, Base.metadata)

    structural: list[tuple[Any, ...]] = []
    for diff in all_diffs:
        # Each diff is either a tuple or a list of tuples (batch of related ops).
        ops = diff if isinstance(diff, list) else [diff]
        for op in ops:
            if isinstance(op, tuple) and op[0] in _STRUCTURAL_DIFF_TYPES:
                structural.append(op)
    return structural


class TestMigrationSchemaDrift:
    """Verify the migration chain leaves no structural gap against models.py."""

    def test_empty_db_upgraded_to_head_has_no_structural_drift(self, tmp_path):
        """An empty DB upgraded to head should match Base.metadata structurally."""
        db_path = str(tmp_path / "drift_empty.db")
        alembic_command.upgrade(_alembic_cfg(db_path), "head")

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            diffs = _structural_diffs(engine)
            assert diffs == [], (
                f"Structural schema drift after upgrade head on empty DB: {diffs}"
            )
        finally:
            engine.dispose()

    def test_create_all_db_stamped_and_upgraded_has_no_structural_drift(self, tmp_path):
        """create_all DB (epap cols present) stamped at a3f8e9c12b45 then upgraded to
        head must not fail with a duplicate-column error and must reach head cleanly."""
        db_path = str(tmp_path / "drift_create_all.db")

        # Simulate legacy DB created via snore db init (Base.metadata.create_all)
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()

        # Stamp at a3f8e9c12b45 (the revision the startup auto-migration will use
        # for a DB that already has the ipap columns but no alembic_version table).
        cfg = _alembic_cfg(db_path)
        alembic_command.stamp(cfg, "a3f8e9c12b45")

        # upgrade head — must not raise even though epap cols already exist.
        alembic_command.upgrade(cfg, "head")

        head = _current_head()
        from sqlalchemy import text

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
            assert version == head, f"Expected head {head}, got {version}"

            diffs = _structural_diffs(engine)
            assert diffs == [], (
                f"Structural schema drift after create_all+stamp+upgrade: {diffs}"
            )
        finally:
            engine.dispose()
