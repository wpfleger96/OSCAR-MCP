"""No-op baseline revision for the SNORE migration chain.

Databases that existed before Alembic migrations were introduced are stamped
at this revision by ``_apply_migrations_sync`` and then upgraded to head so
subsequent migrations apply cleanly without ``create_all`` clobbering existing
tables.

When running ``alembic upgrade head`` from an empty database (e.g. the schema
drift test), this migration creates all application tables EXCEPT
``import_job_records`` (handled by 002) and ``breaths`` (model-only, per
ruling #4) using the current ``Base.metadata`` definitions.  Existing installs
are stamped here without running this code, so their schema is unchanged.

Revision ID: 001_baseline
Revises: None
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables managed by other migrations or excluded from the chain.
_EXCLUDE = frozenset({"import_job_records", "breaths", "analysis_job_records"})


def upgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    from snore.database.models import Base  # noqa: PLC0415

    existing = set(sa_inspect(bind).get_table_names())
    tables_to_create = [
        t
        for t in Base.metadata.sorted_tables
        if t.name not in _EXCLUDE and t.name not in existing
    ]
    if tables_to_create:
        Base.metadata.create_all(bind, tables=tables_to_create)


def downgrade() -> None:
    bind = op.get_bind()
    from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415

    from snore.database.models import Base  # noqa: PLC0415

    existing = set(sa_inspect(bind).get_table_names())
    tables_to_drop = [
        t
        for t in reversed(Base.metadata.sorted_tables)
        if t.name not in _EXCLUDE and t.name in existing
    ]
    if tables_to_drop:
        Base.metadata.drop_all(bind, tables=tables_to_drop)
