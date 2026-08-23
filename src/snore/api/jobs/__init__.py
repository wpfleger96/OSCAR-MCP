"""Shared background-job machinery for the import and analysis pipelines."""

from __future__ import annotations

from snore.api.jobs.core import (
    JOB_TTL_SECONDS,
    JobRecordBase,
    JobStore,
    run_worker_loop,
)
from snore.api.jobs.durability import upsert_job_record
from snore.api.jobs.pool import ThrottledReaper, WorkerPool
from snore.api.jobs.routes import (
    cancel_or_409,
    merge_job_lists,
    owned_or_404,
    terminal_records_query,
)

__all__ = [
    "JOB_TTL_SECONDS",
    "JobRecordBase",
    "JobStore",
    "ThrottledReaper",
    "WorkerPool",
    "cancel_or_409",
    "merge_job_lists",
    "owned_or_404",
    "run_worker_loop",
    "terminal_records_query",
    "upsert_job_record",
]
