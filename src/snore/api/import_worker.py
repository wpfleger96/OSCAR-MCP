"""Import worker: the background thread function that executes import jobs.

Kept in its own module to avoid a circular dependency — the router that accepts
uploads (routers/import_data.py) needs to enqueue jobs without importing the
worker body, and the worker body needs the ImportService which is unrelated to
HTTP routing.

The lifespan (app.py) passes _run_import to start_import_worker() so the
persistent queue thread holds no direct reference to the router module.
"""

from __future__ import annotations

import asyncio
import logging

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from snore.api.import_jobs import ImportJob, JobPhase, JobType
from snore.services.import_service import ImportService

logger = logging.getLogger(__name__)


async def _upsert_job_record(job: ImportJob) -> None:
    """Upsert the current job state to the database for crash-recovery durability.

    Called at each state transition (PENDING → RUNNING → terminal) so a server
    restart can detect orphaned in-progress rows and mark them failed.  Uses
    SQLite's ``ON CONFLICT DO UPDATE`` so the same job_id is never double-inserted
    regardless of how many times this function is called.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: PLC0415

    from snore.database.models import ImportJobRecord  # noqa: PLC0415
    from snore.database.session import session_scope  # noqa: PLC0415

    now = datetime.now(UTC)
    finished = job.finished_at_wall if job.is_terminal else None

    stmt = (
        sqlite_insert(ImportJobRecord)
        .values(
            job_id=job.job_id,
            job_type=job.job_type.value,
            owner_user_id=job.owner_user_id,
            target_profile_id=job.target_profile_id,
            state=job.state.value,
            file_count=job.file_count,
            sessions_imported=job.sessions_imported,
            import_result_json=job.import_result_snapshot,
            error_message=job.error_message,
            analysis_queued=job.analysis_queued,
            spool_dir_path=str(job.temp_dir) if job.temp_dir is not None else None,
            created_at=job.created_at_wall,
            finished_at=finished,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["job_id"],
            set_={
                "state": job.state.value,
                "file_count": job.file_count,
                "sessions_imported": job.sessions_imported,
                "import_result_json": job.import_result_snapshot,
                "error_message": job.error_message,
                "analysis_queued": job.analysis_queued,
                "spool_dir_path": str(job.temp_dir)
                if job.temp_dir is not None
                else None,
                "finished_at": finished,
                "updated_at": now,
            },
        )
    )
    async with session_scope(immediate=True) as db:
        await db.execute(stmt)


def _run_import(job: ImportJob, profile_raw_root: Path | None = None) -> None:
    """Worker function — runs in a background thread. Must be started exactly once.

    Ordering contract:
        1. Do the import work.
        2. Call phase_complete(IMPORT) — non-terminal milestone for observers.
        3. Run analysis phase (session IDs from import result).
        4. Publish terminal state (always carries import_committed + import_result
           when data was committed, even on analysis failure or cancellation).
        5. Clean parser spool + job temp.
        6. Release capacity (slot owns the disk it admitted).
    """

    def _make_terminal(
        event: str,
        *,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a terminal SSE payload, injecting import_committed + import_result
        when the import phase already committed."""
        data: dict[str, Any] = {}
        if message is not None:
            data["message"] = message
        if extra:
            data.update(extra)
        snapshot = job.import_result_snapshot
        if snapshot is not None:
            data["import_committed"] = True
            data["import_result"] = snapshot
        return {"event": event, "data": data}

    try:
        # Persist RUNNING state — the worker loop already called try_start()
        # so job.state is RUNNING by the time this function executes.
        try:
            asyncio.run(_upsert_job_record(job))
        except Exception:
            logger.exception("Failed to upsert RUNNING state for job %s", job.job_id)

        service = ImportService()
        # Consume the snapshotted target_profile_id so DB writes land in the
        # correct profile even if the default profile changes between job creation
        # and worker execution.
        target_profile_id = job.target_profile_id
        if target_profile_id is None:
            raise ValueError("Import job has no target profile — cannot proceed")
        if job.job_type == JobType.UPLOAD and job.temp_dir is not None:
            job.report_progress("Detecting data sources...")
            if job.cancel_requested:
                job._finish(succeeded=False)
                return
            sources = service.detect_sources(job.temp_dir)
            job.report_progress(f"Detected {len(sources)} source(s)")
            if job.cancel_requested:
                job._finish(succeeded=False)
                return
            result = asyncio.run(
                service.import_sources(
                    sources,
                    backup=True,
                    backup_root=profile_raw_root,
                    profile_id=target_profile_id,
                    progress_callback=lambda msg: job.report_progress(msg),
                    cancel_predicate=lambda: job.cancel_requested,
                )
            )
        else:
            raise ValueError("Invalid job configuration")

        # --- Phase 1 complete: import committed ---
        import_result_dict = result.model_dump()
        job.phase_complete(JobPhase.IMPORT, import_result_dict)

        if job.cancel_requested:
            job._finish(
                succeeded=False,
                terminal_msg=_make_terminal("error", message="Cancelled"),
            )
            return

        # Enqueue background analysis for imported sessions, then immediately
        # emit terminal "complete" so the user can upload more files.
        analysis_job_id = None
        imported_ids = result.imported_session_ids
        if imported_ids:
            from snore.api import analysis_jobs  # noqa: PLC0415

            aj = analysis_jobs.enqueue(
                profile_id=target_profile_id,
                session_ids=imported_ids,
                source=analysis_jobs.AnalysisJobSource.IMPORT,
                owner_user_id=job.owner_user_id,
            )
            if aj is not None:
                analysis_job_id = aj.job_id
            else:
                logger.warning(
                    "Analysis queue full; skipping auto-analysis for import job %s",
                    job.job_id,
                )

        job.set_analysis_link(
            analysis_job_id=analysis_job_id,
            queue_full=bool(imported_ids) and analysis_job_id is None,
        )

        terminal_extra: dict[str, Any] = {"result": import_result_dict}
        if analysis_job_id is not None:
            terminal_extra["analysis_job_id"] = analysis_job_id
        elif imported_ids:
            # Queue was full — tell the client so it can distinguish from
            # "nothing was imported" (where analysis_queued is absent).
            terminal_extra["analysis_queued"] = False
        terminal_msg = _make_terminal("complete", extra=terminal_extra)
        job._finish(succeeded=True, terminal_msg=terminal_msg)
    except Exception as e:
        logger.exception("Import job %s failed", job.job_id)
        job._finish(
            succeeded=False,
            terminal_msg=_make_terminal("error", message=str(e)),
        )
    finally:
        # Ordering: publish terminal (done above), persist, clean, release capacity.
        if job.is_terminal:
            try:
                asyncio.run(_upsert_job_record(job))
            except Exception:
                logger.exception("Failed to persist job record for %s", job.job_id)
        from snore.api.import_jobs import is_shutdown_in_progress  # noqa: PLC0415

        if not is_shutdown_in_progress():
            job.cleanup_files()
        job.release_capacity()
