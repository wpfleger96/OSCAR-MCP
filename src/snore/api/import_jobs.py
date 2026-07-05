"""In-memory job store for streaming import progress via SSE."""

from __future__ import annotations

import threading
import time
import uuid

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue
from typing import Any


class JobType(Enum):
    UPLOAD = "upload"
    PATH = "path"


@dataclass
class ImportJob:
    job_id: str
    job_type: JobType
    progress_queue: Queue[dict[str, Any]] = field(default_factory=Queue)
    created_at: float = field(default_factory=time.monotonic)
    # UPLOAD jobs: temp dir with written files
    temp_dir: Path | None = None
    # PATH jobs: sources list
    sources: list[Any] | None = None


_jobs: dict[str, ImportJob] = {}
_lock = threading.Lock()

_JOB_TIMEOUT_SECONDS = 600


def create_job(job_type: JobType, **kwargs: Any) -> ImportJob:
    _purge_stale()
    job = ImportJob(job_id=uuid.uuid4().hex, job_type=job_type, **kwargs)
    with _lock:
        _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> ImportJob | None:
    with _lock:
        return _jobs.get(job_id)


def remove_job(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


def _purge_stale() -> None:
    import shutil

    now = time.monotonic()
    with _lock:
        stale = [
            jid
            for jid, job in _jobs.items()
            if now - job.created_at > _JOB_TIMEOUT_SECONDS
        ]
        for jid in stale:
            job = _jobs.pop(jid, None)
            if job is not None and job.temp_dir is not None:
                shutil.rmtree(job.temp_dir, ignore_errors=True)
