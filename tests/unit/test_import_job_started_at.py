"""Unit tests for import-job ``started_at`` persistence.

An import job records ``started_at`` the moment it transitions to RUNNING (via
the base ``_start_running`` hook), and ``import_worker._upsert_job_record`` must
persist it to ``import_job_records`` — null while the job is still pending, set
once it has started.
"""

from __future__ import annotations

import pytest

from sqlalchemy import select

from snore.api.import_jobs import ImportJob, JobState, JobType
from snore.api.import_worker import _upsert_job_record


def _pending_job() -> ImportJob:
    job = ImportJob(job_id="job-started-at", job_type=JobType.UPLOAD, owner_user_id=1)
    job._state = JobState.PENDING
    job.target_profile_id = 7
    return job


@pytest.mark.asyncio
async def test_started_at_null_while_pending(temp_db):
    from snore.database.models import ImportJobRecord
    from snore.database.session import cleanup_database, init_database, session_scope

    await init_database(str(temp_db))
    try:
        job = _pending_job()  # never started
        await _upsert_job_record(job)

        async with session_scope() as db:
            rec = (
                (
                    await db.execute(
                        select(ImportJobRecord).where(
                            ImportJobRecord.job_id == job.job_id
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert rec.started_at is None
    finally:
        await cleanup_database()


@pytest.mark.asyncio
async def test_started_at_persisted_after_running(temp_db):
    from snore.database.models import ImportJobRecord
    from snore.database.session import cleanup_database, init_database, session_scope

    await init_database(str(temp_db))
    try:
        job = _pending_job()
        # Persist while pending, then start and re-persist: the conflict upsert
        # must fill in started_at (it is in update_fields).
        await _upsert_job_record(job)
        assert job.try_start() is True
        await _upsert_job_record(job)

        async with session_scope() as db:
            rec = (
                (
                    await db.execute(
                        select(ImportJobRecord).where(
                            ImportJobRecord.job_id == job.job_id
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert rec.started_at is not None
        assert rec.started_at == job.started_at_wall
    finally:
        await cleanup_database()
