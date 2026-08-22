"""Shared HTTP helpers for the import and analysis job-list/cancel endpoints.

Both routers merge in-memory jobs with terminal DB rows, apply the same
no-info-leak ownership rule (404 for foreign jobs, never 403), and translate a
False cancel result into 409.  Those pieces live here; the per-row translation
to each router's Pydantic status model stays local to the router (the models and
their derived fields differ).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, or_, select


def terminal_records_query(
    model: Any,
    owner_user_id: int | None,
    terminal_states: Iterable[str],
) -> Select[Any]:
    """Build the historical-records query: terminal rows visible to the actor.

    Visibility mirrors the in-memory filter (the actor's own rows plus unowned
    ones).  The terminal-only ``state`` filter is deliberate: non-terminal rows
    are in-flight jobs whose live state lives in memory, so a persisted
    non-terminal row without an in-memory twin is an orphan that must not surface
    as a phantom "forever running" entry.  Newest-first, capped at 50.
    """
    return (
        select(model)
        .where(
            or_(
                model.owner_user_id == owner_user_id,
                model.owner_user_id.is_(None),
            ),
            model.state.in_(list(terminal_states)),
        )
        .order_by(model.created_at.desc())
        .limit(50)
    )


def merge_job_lists[S, R](
    in_memory: Sequence[S],
    in_memory_ids: set[str],
    db_terminal_rows: Iterable[R],
    *,
    to_status: Callable[[R], S],
    sort_key: Callable[[S], Any],
) -> list[S]:
    """Merge in-memory statuses with DB terminal rows, in-memory winning on id.

    ``in_memory`` is the already-built list of status models for live jobs;
    ``db_terminal_rows`` are ORM rows whose ids are checked against
    ``in_memory_ids`` so an in-memory job is never duplicated by its persisted
    row.  Surviving rows are mapped through ``to_status`` and the result is
    sorted by ``sort_key`` descending.
    """
    merged: list[S] = list(in_memory)
    for rec in db_terminal_rows:
        if rec.job_id in in_memory_ids:  # type: ignore[attr-defined]
            continue
        merged.append(to_status(rec))
    merged.sort(key=sort_key, reverse=True)
    return merged


def owned_or_404[J](
    job: J | None, actor_user_id: int | None, *, not_found_detail: str
) -> J:
    """Return *job* if the actor may see it, else raise 404.

    404 (never 403) for a missing or foreign job so no information leaks about
    other users' job ids.  A job with ``owner_user_id=None`` is visible to any
    actor (local-mode parity).
    """
    if job is None or (
        job.owner_user_id is not None  # type: ignore[attr-defined]
        and job.owner_user_id != actor_user_id  # type: ignore[attr-defined]
    ):
        raise HTTPException(status_code=404, detail=not_found_detail)
    return job


def cancel_or_409(
    cancel_fn: Callable[[str], bool],
    job_id: str,
    *,
    already_detail: str,
) -> None:
    """Invoke *cancel_fn*; raise 409 when it reports the job already terminal."""
    if not cancel_fn(job_id):
        raise HTTPException(status_code=409, detail=already_detail)
