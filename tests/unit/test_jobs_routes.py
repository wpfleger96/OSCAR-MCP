"""Unit tests for the shared job-list/cancel route helpers."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fastapi import HTTPException

from snore.api.jobs.routes import cancel_or_409, merge_job_lists, owned_or_404


@dataclass
class _Status:
    job_id: str
    created_at: float


@dataclass
class _Row:
    job_id: str
    created_at: float


@dataclass
class _Job:
    owner_user_id: int | None


# ---------------------------------------------------------------------------
# merge_job_lists
# ---------------------------------------------------------------------------


def test_merge_in_memory_wins_over_db_row_with_same_id():
    in_memory = [_Status("a", 10.0)]
    db_rows = [_Row("a", 5.0), _Row("b", 7.0)]

    merged = merge_job_lists(
        in_memory,
        {"a"},
        db_rows,
        to_status=lambda r: _Status(r.job_id, r.created_at),
        sort_key=lambda s: s.created_at,
    )

    ids = [s.job_id for s in merged]
    assert ids == ["a", "b"]  # "a" from memory, "b" from DB
    # The in-memory "a" (created_at 10.0) is kept, not the DB "a" (5.0).
    assert next(s for s in merged if s.job_id == "a").created_at == 10.0


def test_merge_sorts_descending_by_sort_key():
    in_memory = [_Status("a", 1.0)]
    db_rows = [_Row("b", 9.0), _Row("c", 5.0)]

    merged = merge_job_lists(
        in_memory,
        {"a"},
        db_rows,
        to_status=lambda r: _Status(r.job_id, r.created_at),
        sort_key=lambda s: s.created_at,
    )

    assert [s.job_id for s in merged] == ["b", "c", "a"]


def test_merge_empty_db_rows_returns_sorted_in_memory():
    in_memory = [_Status("a", 1.0), _Status("b", 3.0)]

    merged = merge_job_lists(
        in_memory,
        {"a", "b"},
        [],
        to_status=lambda r: _Status(r.job_id, r.created_at),
        sort_key=lambda s: s.created_at,
    )

    assert [s.job_id for s in merged] == ["b", "a"]


# ---------------------------------------------------------------------------
# owned_or_404
# ---------------------------------------------------------------------------


def test_owned_or_404_missing_job_raises_404():
    with pytest.raises(HTTPException) as exc:
        owned_or_404(None, 1, not_found_detail="nope")
    assert exc.value.status_code == 404
    assert exc.value.detail == "nope"


def test_owned_or_404_foreign_job_raises_404_not_403():
    with pytest.raises(HTTPException) as exc:
        owned_or_404(_Job(owner_user_id=2), 1, not_found_detail="nope")
    assert exc.value.status_code == 404  # never 403 — no info leak


def test_owned_or_404_own_job_returned():
    job = _Job(owner_user_id=1)
    assert owned_or_404(job, 1, not_found_detail="nope") is job


def test_owned_or_404_unowned_job_visible_to_any_actor():
    job = _Job(owner_user_id=None)
    assert owned_or_404(job, 99, not_found_detail="nope") is job


# ---------------------------------------------------------------------------
# cancel_or_409
# ---------------------------------------------------------------------------


def test_cancel_or_409_true_is_noop():
    called: list[str] = []

    def cancel(job_id: str) -> bool:
        called.append(job_id)
        return True

    cancel_or_409(cancel, "x", already_detail="done")
    assert called == ["x"]


def test_cancel_or_409_false_raises_409():
    with pytest.raises(HTTPException) as exc:
        cancel_or_409(lambda _jid: False, "x", already_detail="already done")
    assert exc.value.status_code == 409
    assert exc.value.detail == "already done"
