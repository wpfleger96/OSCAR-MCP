"""Shared crash-recovery durability upsert for background-job records.

Both pipelines persist a job's state to a ``*_job_records`` table at each
transition so a server restart can detect orphaned in-progress rows.  The two
upserts were ~59% identical: a ``sqlite_insert(...).on_conflict_do_update`` on
``job_id`` inside ``session_scope(immediate=True)``.  This single-sources that
shape; the per-model column set stays in the thin wrappers that call it
(``import_worker._upsert_job_record`` / ``analysis_jobs._upsert_analysis_record``),
which remain the patched seams the tests target.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


async def upsert_job_record(
    model: Any,
    *,
    values: Mapping[str, Any],
    update_fields: Iterable[str],
    index_elements: Iterable[str] = ("job_id",),
) -> None:
    """Insert *values* into *model*, updating *update_fields* on conflict.

    The conflict target is *index_elements* (the ``job_id`` unique index by
    default).  ``update_fields`` names the subset of *values* to overwrite when
    the row already exists — identity columns (job_id, created_at, immutable job
    metadata) are written only on insert.  Runs in an immediate transaction so
    the SQLite write lock is taken up front, matching the original callers.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: PLC0415

    from snore.database.session import session_scope  # noqa: PLC0415

    stmt = (
        sqlite_insert(model)
        .values(**values)
        .on_conflict_do_update(
            index_elements=list(index_elements),
            set_={field: values[field] for field in update_fields},
        )
    )
    async with session_scope(immediate=True) as db:
        await db.execute(stmt)
