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

import pytest

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

# Tables intentionally excluded from Alembic migrations (model-only, per locked
# ruling #4 in the implementation plan).  Fresh DBs get these tables via
# Base.metadata.create_all; existing DBs must drop and reimport.
_NO_MIGRATION_TABLES: frozenset[str] = frozenset({"breaths"})


def _migrations_dir() -> str:
    return str(Path(_snore_db_pkg.__file__).parent / "migrations")


def _alembic_cfg(db_path: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _migrations_dir())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


# Guard: skip the test class when versions/ is empty (zero-migration mode).
# The test auto-reactivates when migration files are added to versions/.
_cfg = AlembicConfig()
_cfg.set_main_option("script_location", _migrations_dir())
_HAS_MIGRATIONS = bool(ScriptDirectory.from_config(_cfg).get_heads())


def _structural_diffs(engine: Engine) -> list[tuple[Any, ...]]:
    """Return only structural (add/remove table/column) diffs against Base.metadata."""
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
                # Skip diffs for tables that deliberately have no migration.
                table_obj = op[1] if len(op) > 1 else None
                table_name = getattr(table_obj, "name", None) or getattr(
                    table_obj, "parent", {}
                )
                if isinstance(table_name, str) and table_name in _NO_MIGRATION_TABLES:
                    continue
                structural.append(op)
    return structural


@pytest.mark.skipif(
    not _HAS_MIGRATIONS,
    reason="versions/ is empty (zero-migration mode); auto-reactivates when migration files are added",
)
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
