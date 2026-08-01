"""Analysis facade for listing and managing analysis results."""

import logging

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from snore.analysis.types import AnalysisResult
from snore.database import models
from snore.services.schemas import (
    AnalysisDeletePreview,
    AnalysisListItem,
    AnalysisSessionDetail,
    BatchAnalysisResult,
    BatchSessionResult,
)

__all__ = ["AnalysisFacade", "BatchAnalysisCoordinator"]

logger = logging.getLogger(__name__)


class AnalysisFacade:
    """Facade for analysis listing and deletion operations."""

    def __init__(self, db_session: Session):
        """
        Initialize analysis facade.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session
        self._batch_coordinator: BatchAnalysisCoordinator | None = None

    @property
    def batch_coordinator(self) -> "BatchAnalysisCoordinator | None":
        """The active ``BatchAnalysisCoordinator``, or ``None`` when no batch is running.

        Set immediately before ``submit()`` is called; remains set after completion so
        callers can inspect ``progress`` after the batch finishes.  PR-2 replaces the
        sync coordinator with an async one; callers use the same ``cancel()`` handle.
        """
        return self._batch_coordinator

    def _status_select(
        self,
        start: datetime | None,
        end: datetime | None,
        analyzed_only: bool,
    ) -> Any:
        """Build the shared 2.0-style select for list/count of analysis status."""
        stmt = select(models.Session).join(
            models.Day, models.Session.day_id == models.Day.id
        )

        if start:
            stmt = stmt.where(models.Day.date >= start.date())
        if end:
            stmt = stmt.where(models.Day.date <= end.date())

        if analyzed_only:
            stmt = stmt.where(
                select(models.AnalysisResult.id)
                .where(models.AnalysisResult.session_id == models.Session.id)
                .exists()
            )

        return stmt

    def _latest_analysis_ids(self, session_ids: list[int]) -> dict[int, int]:
        """Map each session ID to its latest AnalysisResult ID (by created_at)."""
        if not session_ids:
            return {}

        ranked = (
            select(
                models.AnalysisResult.session_id,
                models.AnalysisResult.id,
                func.row_number()
                .over(
                    partition_by=models.AnalysisResult.session_id,
                    order_by=models.AnalysisResult.created_at.desc(),
                )
                .label("recency_rank"),
            )
            .where(models.AnalysisResult.session_id.in_(session_ids))
            .subquery()
        )
        rows = self.db_session.execute(
            select(ranked.c.session_id, ranked.c.id).where(ranked.c.recency_rank == 1)
        ).all()
        return {session_id: analysis_id for session_id, analysis_id in rows}

    def list_sessions_with_status(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
        analyzed_only: bool = False,
        sort_by: str = "date-desc",
    ) -> list[AnalysisListItem]:
        """List sessions with their analysis status.

        Args:
            start: Filter sessions from this datetime (inclusive)
            end: Filter sessions to this datetime (inclusive)
            limit: Maximum sessions to return (0 for unlimited)
            analyzed_only: Only return sessions that have analysis results
            sort_by: Sort order (date-asc, date-desc, session-id)

        Returns:
            List of AnalysisListItem with has_analysis and analysis_id fields
        """
        stmt = self._status_select(start, end, analyzed_only)

        sort_clauses: dict[str, Any] = {
            "date-asc": models.Day.date.asc(),
            "date-desc": models.Day.date.desc(),
            "session-id": models.Session.id.asc(),
        }

        sort_clause = sort_clauses.get(sort_by, models.Day.date.desc())
        stmt = stmt.order_by(sort_clause)

        if offset > 0:
            stmt = stmt.offset(offset)

        if limit > 0:
            stmt = stmt.limit(limit)
        else:
            stmt = stmt.limit(10000)

        # Load Day inline to avoid lazy-load access below.
        from sqlalchemy.orm import joinedload as _joinedload

        stmt = stmt.options(_joinedload(models.Session.day))
        sessions = self.db_session.execute(stmt).unique().scalars().all()

        latest_analysis = self._latest_analysis_ids([s.id for s in sessions])

        results = []
        for session in sessions:
            analysis_id = latest_analysis.get(session.id)

            # day is loaded via joinedload; use start_time.date() as fallback.
            session_date = (
                session.day.date if session.day else session.start_time.date()
            )

            results.append(
                AnalysisListItem(
                    session_id=session.id,
                    session_date=session_date,
                    duration_hours=(
                        session.duration_seconds / 3600
                        if session.duration_seconds
                        else None
                    ),
                    has_analysis=analysis_id is not None,
                    analysis_id=analysis_id,
                )
            )

        return results

    def count_sessions_with_status(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        analyzed_only: bool = False,
    ) -> int:
        """Return total count of sessions matching the same filters as list_sessions_with_status.

        Used by the API to populate the `total` field in paginated responses.
        """
        count_stmt = select(func.count()).select_from(
            self._status_select(start, end, analyzed_only).subquery()
        )
        return self.db_session.execute(count_stmt).scalar() or 0

    def get_delete_preview(
        self,
        session_ids: list[int] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        delete_all: bool = False,
        all_versions: bool = False,
    ) -> AnalysisDeletePreview:
        """Preview analysis data that would be deleted.

        Args:
            session_ids: Specific session IDs to filter
            from_date: Filter sessions from this datetime
            to_date: Filter sessions to this datetime
            delete_all: Consider all sessions
            all_versions: Count all analysis versions (affects records_to_delete)

        Returns:
            AnalysisDeletePreview with counts and session details

        Raises:
            ValueError: If no filters specified
        """
        if not any([session_ids, from_date, to_date, delete_all]):
            raise ValueError(
                "At least one filter must be specified: "
                "session_ids, from_date, to_date, or delete_all"
            )

        query = (
            select(
                models.Session.id,
                models.Session.device_session_id,
                models.Session.start_time,
                models.Device.manufacturer,
                models.Device.model,
            )
            .join(models.Device, models.Session.device_id == models.Device.id)
            .join(
                models.AnalysisResult,
                models.Session.id == models.AnalysisResult.session_id,
            )
            .distinct()
        )

        if session_ids:
            query = query.where(models.Session.id.in_(session_ids))

        if from_date:
            query = query.where(models.Session.start_time >= from_date)

        if to_date:
            query = query.where(models.Session.start_time <= to_date)

        query = query.order_by(models.Session.start_time.desc())

        sessions_with_analysis = self.db_session.execute(query).fetchall()

        if not sessions_with_analysis:
            return AnalysisDeletePreview(
                sessions_with_analysis=0,
                total_analysis_records=0,
                records_to_delete=0,
                patterns_count=0,
                session_details=[],
            )

        session_ids_list = [s.id for s in sessions_with_analysis]

        analysis_counts = self.db_session.execute(
            select(models.AnalysisResult.session_id, func.count())
            .where(models.AnalysisResult.session_id.in_(session_ids_list))
            .group_by(models.AnalysisResult.session_id)
        ).fetchall()

        analysis_count_dict = {row[0]: int(row[1]) for row in analysis_counts}

        total_analysis_records = sum(analysis_count_dict.values())
        records_to_delete = (
            total_analysis_records if all_versions else len(sessions_with_analysis)
        )

        patterns_count = self.db_session.execute(
            select(func.count())
            .select_from(models.DetectedPattern)
            .where(
                models.DetectedPattern.analysis_result_id.in_(
                    select(models.AnalysisResult.id).where(
                        models.AnalysisResult.session_id.in_(session_ids_list)
                    )
                )
            )
        ).scalar()

        session_details = [
            AnalysisSessionDetail(
                id=s.id,
                start_time=s.start_time,
                manufacturer=s.manufacturer,
                model=s.model,
                version_count=analysis_count_dict.get(s.id, 0),
            )
            for s in sessions_with_analysis
        ]

        return AnalysisDeletePreview(
            sessions_with_analysis=len(sessions_with_analysis),
            total_analysis_records=total_analysis_records,
            records_to_delete=records_to_delete,
            patterns_count=patterns_count or 0,
            session_details=session_details,
        )

    def delete_analysis(
        self,
        session_ids: list[int],
        all_versions: bool = False,
    ) -> int:
        """Delete analysis results for given sessions.

        Args:
            session_ids: Session IDs to delete analysis for
            all_versions: If True, delete all versions. If False, only latest.

        Returns:
            Number of analysis records deleted
        """
        if all_versions:
            # Delete all analysis results for these sessions.
            result = self.db_session.execute(
                delete(models.AnalysisResult).where(
                    models.AnalysisResult.session_id.in_(session_ids)
                )
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
        else:
            # Delete only the latest (highest created_at) result per session.
            # Identify the latest result IDs first, then delete by PK.
            ranked = (
                select(
                    models.AnalysisResult.id,
                    func.row_number()
                    .over(
                        partition_by=models.AnalysisResult.session_id,
                        order_by=models.AnalysisResult.created_at.desc(),
                    )
                    .label("rn"),
                )
                .where(models.AnalysisResult.session_id.in_(session_ids))
                .subquery()
            )
            latest_ids = (
                self.db_session.execute(select(ranked.c.id).where(ranked.c.rn == 1))
                .scalars()
                .all()
            )
            if not latest_ids:
                return 0
            result = self.db_session.execute(
                delete(models.AnalysisResult).where(
                    models.AnalysisResult.id.in_(latest_ids)
                )
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    def run_analysis(
        self,
        session_id: int,
        modes: list[str] | None = None,
        store_results: bool = True,
    ) -> AnalysisResult:
        """Run analysis on a session. Returns AnalysisResult (Pydantic model)."""
        from snore.analysis.service import AnalysisService

        svc = AnalysisService(self.db_session)
        return svc.analyze_session(session_id, modes=modes, store_results=store_results)

    def get_analysis_result(self, session_id: int) -> AnalysisResult | None:
        """Get stored analysis result for a session, or None if not found.

        Intentionally returns None (rather than raising NotFoundError like the
        resource lookups elsewhere): "not yet analyzed" is a normal state that
        callers branch on, not a 404 condition.
        """
        from snore.analysis.service import AnalysisService

        svc = AnalysisService(self.db_session)
        return svc.get_analysis_result(session_id)

    def run_batch_analysis(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        modes: Sequence[str] | None = None,
        store_results: bool = True,
        max_workers: int = 4,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> BatchAnalysisResult:
        """Run analysis on multiple sessions in parallel.

        Delegates to ``BatchAnalysisCoordinator`` so PR-2 can swap the executor
        internals (``ThreadPoolExecutor`` → async tasks) without touching callers.

        Args:
            from_date: Filter sessions from this datetime (inclusive)
            to_date: Filter sessions to this datetime (inclusive)
            modes: Detection modes to run (None = default)
            store_results: Whether to store results in the database
            max_workers: Max parallel threads (capped to session count)
            progress_callback: Called with (completed, total) after each session

        Returns:
            BatchAnalysisResult with per-session outcomes and aggregate counts
        """
        stmt = select(models.Session, models.Day.date.label("day_date")).join(
            models.Day, models.Session.day_id == models.Day.id
        )
        if from_date:
            stmt = stmt.where(models.Day.date >= from_date.date())
        if to_date:
            stmt = stmt.where(models.Day.date <= to_date.date())
        stmt = stmt.order_by(models.Day.date)

        rows = self.db_session.execute(stmt).all()
        session_dates: dict[int, date | None] = {
            row.Session.id: row.day_date for row in rows
        }
        session_ids = [row.Session.id for row in rows]

        if not session_ids:
            return BatchAnalysisResult(total=0, successful=0, failed=0, results=[])

        coordinator = BatchAnalysisCoordinator()
        self._batch_coordinator = coordinator
        return coordinator.submit(
            session_ids=session_ids,
            session_dates=session_dates,
            modes=modes,
            store_results=store_results,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )


# ---------------------------------------------------------------------------
# BatchAnalysisCoordinator (§7)
# ---------------------------------------------------------------------------


class BatchAnalysisCoordinator:
    """Thin coordinator that backs batch analysis scheduling.

    PR-1 implementation: ``ThreadPoolExecutor`` with detached I/O→compute→write
    pipelines.  PR-2 swaps the executor internals to ``asyncio`` tasks without
    touching callers — the ``submit`` interface is the stable boundary.

    The narrow interface (``submit / progress / cancel``) is intentional:
    - ``submit`` starts work synchronously and returns the aggregated result.
    - ``progress`` is reserved for PR-2's async streaming path.
    - ``cancel`` requests cooperative cancellation via a flag checked between
      sessions; PR-2 replaces it with task cancellation.
    """

    def __init__(self) -> None:
        self._cancel_requested = False
        self._completed = 0
        self._total = 0

    def cancel(self) -> None:
        """Request cooperative cancellation.  Checked between sessions."""
        self._cancel_requested = True

    @property
    def progress(self) -> tuple[int, int]:
        """Return (completed, total) session counts."""
        return self._completed, self._total

    def submit(
        self,
        *,
        session_ids: list[int],
        session_dates: dict[int, date | None],
        modes: Sequence[str] | None = None,
        store_results: bool = True,
        max_workers: int = 4,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> BatchAnalysisResult:
        """Execute batch analysis and return aggregated results.

        Each session is processed in a detached I/O → compute → write pipeline:

        1. **Read phase**: open a short ``session_scope()``, load the session
           record and analysis inputs, return a detached ``AnalysisResult`` DTO.
           The read session is closed *before* entering compute.
        2. **Compute phase**: pure Python / numpy, no ORM session held.
        3. **Write phase**: open a fresh ``session_scope()``, INSERT the result.
           The write lock is held only for the INSERT duration.

        This separation prevents SQLite write-lock contention when multiple
        workers finish concurrently under ``autocommit=False``.

        Args:
            session_ids: DB session IDs to analyse.
            session_dates: Maps session ID → calendar date (for result labelling).
            modes: Detection modes to run (``None`` = default).
            store_results: If True, write each result to the DB.
            max_workers: Thread-pool concurrency cap.
            progress_callback: Called with (completed, total) after each session.

        Returns:
            Aggregated ``BatchAnalysisResult``.
        """
        import time  # noqa: PLC0415

        from concurrent.futures import (  # noqa: PLC0415
            FIRST_COMPLETED,
            ThreadPoolExecutor,
            wait,
        )

        from snore.analysis.service import AnalysisService  # noqa: PLC0415
        from snore.database.session import session_scope  # noqa: PLC0415

        total = len(session_ids)
        self._total = total
        self._completed = 0
        # Do NOT reset _cancel_requested if cancel() was called before submit().
        # This allows callers to pre-cancel a coordinator before submitting work.

        batch_results: list[BatchSessionResult] = []
        modes_list: list[str] | None = list(modes) if modes is not None else None

        def analyze_one(sid: int) -> str:
            """Return 'cancelled', 'success', or 'error' for this session."""
            if self._cancel_requested:
                return "cancelled"

            # --- Read phase (session open only during DB I/O) ---
            # load_session_inputs_raw fetches raw blobs and returns immediately.
            # ALL NumPy work (deserialization, artifact detection) happens AFTER
            # the session is closed via prepare_inputs().
            t_start = time.monotonic()
            with session_scope() as read_session:
                svc = AnalysisService(read_session)
                raw = svc.load_session_inputs_raw(session_id=sid, modes=modes_list)
            # Session is now closed; all NumPy/scipy work below is sessionless.

            # --- Compute phase (no ORM session held) ---
            inputs = AnalysisService.prepare_inputs(raw)
            compute_svc = AnalysisService._make_compute_only()
            result = compute_svc.compute_analysis(inputs)
            processing_time_ms = int((time.monotonic() - t_start) * 1000)

            # --- Write phase (short session for INSERT only) ---
            if store_results and result is not None:
                with session_scope() as write_session:
                    write_svc = AnalysisService(write_session)
                    write_svc.store_result(result, processing_time_ms)

            return "success"

        # Bounded in-flight: submit at most max_workers futures at a time so the
        # executor never holds all session rows in memory simultaneously.
        effective_workers = min(max_workers, total)
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            # Sliding window: keep at most max_workers futures pending.
            from concurrent.futures import Future  # noqa: PLC0415

            pending: dict[Future[str], int] = {}
            sid_iter = iter(session_ids)

            def _submit_next() -> None:
                try:
                    sid = next(sid_iter)
                    fut = executor.submit(analyze_one, sid)
                    pending[fut] = sid
                except StopIteration:
                    pass

            # Prime the window.
            for _ in range(effective_workers):
                _submit_next()

            while pending:
                done, _ = wait(list(pending.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    sid = pending.pop(future)
                    try:
                        outcome = future.result()
                    except Exception as e:
                        logger.warning(
                            "Failed to analyze session %d: %s", sid, e, exc_info=True
                        )
                        outcome = "error"
                    cancelled = outcome == "cancelled"
                    success = outcome == "success"
                    error = (
                        None
                        if outcome != "error"
                        else f"Analysis failed for session {sid}"
                    )
                    batch_results.append(
                        BatchSessionResult(
                            session_id=sid,
                            session_date=session_dates.get(sid),
                            success=success,
                            cancelled=cancelled,
                            error=error,
                        )
                    )
                    self._completed += 1
                    if progress_callback:
                        progress_callback(self._completed, total)
                    # Slide the window forward.
                    _submit_next()

        successful = sum(1 for r in batch_results if r.success)
        cancelled_count = sum(1 for r in batch_results if r.cancelled)
        return BatchAnalysisResult(
            total=total,
            successful=successful,
            failed=total - successful - cancelled_count,
            cancelled=cancelled_count,
            results=batch_results,
        )
