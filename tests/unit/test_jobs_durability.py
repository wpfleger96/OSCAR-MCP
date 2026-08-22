"""Unit tests for the shared crash-recovery upsert (jobs.durability)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sqlalchemy import select

from snore.api.jobs.durability import upsert_job_record


@pytest.mark.asyncio
async def test_insert_then_conflict_updates_only_update_fields(temp_db):
    from snore.database.models import AnalysisJobRecord
    from snore.database.session import (
        cleanup_database,
        init_database,
        session_scope,
    )

    await init_database(str(temp_db))
    try:
        created = datetime(2026, 1, 1, tzinfo=UTC)
        base = {
            "job_id": "job-1",
            "source": "batch",
            "profile_id": 7,
            "owner_user_id": 3,
            "session_ids_json": [1, 2, 3],
            "modes": None,
            "primary_mode": None,
            "store_results": True,
            "state": "running",  # analysis records persist only running + terminal
            "progress_completed": 0,
            "progress_total": 3,
            "error_message": None,
            "created_at": created,
            "started_at": None,
            "finished_at": None,
            "updated_at": created,
        }
        update_fields = [
            "state",
            "progress_completed",
            "progress_total",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        ]

        await upsert_job_record(
            AnalysisJobRecord, values=base, update_fields=update_fields
        )

        # Conflict upsert: mutate an update_field (state) AND an identity field
        # (created_at, profile_id) that must NOT be overwritten on conflict.
        later = datetime(2026, 2, 2, tzinfo=UTC)
        conflict = {
            **base,
            "state": "succeeded",
            "progress_completed": 3,
            "created_at": later,  # not in update_fields → must be ignored
            "profile_id": 999,  # not in update_fields → must be ignored
            "started_at": later,
            "updated_at": later,
        }
        await upsert_job_record(
            AnalysisJobRecord, values=conflict, update_fields=update_fields
        )

        async with session_scope() as db:
            rows = (
                (
                    await db.execute(
                        select(AnalysisJobRecord).where(
                            AnalysisJobRecord.job_id == "job-1"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert len(rows) == 1  # ON CONFLICT updated in place, no duplicate row
        row = rows[0]
        # update_fields were applied.
        assert row.state == "succeeded"
        assert row.progress_completed == 3
        assert row.started_at is not None
        # Identity columns preserved from the original insert.
        assert row.created_at == created
        assert row.profile_id == 7
    finally:
        await cleanup_database()
