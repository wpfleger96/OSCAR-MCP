"""Analysis facade for listing and managing analysis results."""

import asyncio
import logging

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        """
        Initialize analysis facade.

        Args:
            db_session: SQLAlchemy database session
            profile_id: Active profile — all queries are scoped to this profile.
        """
        self.db_session = db_session
        self.profile_id = profile_id
        self._batch_coordinator: BatchAnalysisCoordinator | None = None

    def _profile_filter(self) -> ColumnElement[bool]:
        """WHERE predicate: limit sessions to this profile via device ownership."""
        return models.Device.profile_id == self.profile_id

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
        stmt = (
            select(models.Session)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(self._profile_filter())
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

    async def _latest_analysis_ids(self, session_ids: list[int]) -> dict[int, int]:
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
        rows = (
            await self.db_session.execute(
                select(ranked.c.session_id, ranked.c.id).where(
                    ranked.c.recency_rank == 1
                )
            )
        ).all()
        return {session_id: analysis_id for session_id, analysis_id in rows}

    async def list_sessions_with_status(
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
        sessions = (await self.db_session.execute(stmt)).unique().scalars().all()

        latest_analysis = await self._latest_analysis_ids([s.id for s in sessions])

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

    async def count_sessions_with_status(
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
        return (await self.db_session.execute(count_stmt)).scalar() or 0

    async def get_delete_preview(
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
            .where(self._profile_filter())
            .distinct()
        )

        if session_ids:
            query = query.where(models.Session.id.in_(session_ids))

        if from_date:
            query = query.where(models.Session.start_time >= from_date)

        if to_date:
            query = query.where(models.Session.start_time <= to_date)

        query = query.order_by(models.Session.start_time.desc())

        sessions_with_analysis = (await self.db_session.execute(query)).fetchall()

        if not sessions_with_analysis:
            return AnalysisDeletePreview(
                sessions_with_analysis=0,
                total_analysis_records=0,
                records_to_delete=0,
                patterns_count=0,
                session_details=[],
            )

        session_ids_list = [s.id for s in sessions_with_analysis]

        analysis_counts = (
            await self.db_session.execute(
                select(models.AnalysisResult.session_id, func.count())
                .where(models.AnalysisResult.session_id.in_(session_ids_list))
                .group_by(models.AnalysisResult.session_id)
            )
        ).fetchall()

        analysis_count_dict = {row[0]: int(row[1]) for row in analysis_counts}

        total_analysis_records = sum(analysis_count_dict.values())
        records_to_delete = (
            total_analysis_records if all_versions else len(sessions_with_analysis)
        )

        patterns_count = (
            await self.db_session.execute(
                select(func.count())
                .select_from(models.DetectedPattern)
                .where(
                    models.DetectedPattern.analysis_result_id.in_(
                        select(models.AnalysisResult.id).where(
                            models.AnalysisResult.session_id.in_(session_ids_list)
                        )
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

    async def delete_analysis(
        self,
        session_ids: list[int],
        all_versions: bool = False,
    ) -> int:
        """Delete analysis results for given sessions.

        Only analysis records whose sessions belong to this profile are deleted.
        Foreign session IDs are silently ignored — the caller sees the count of
        records actually removed (0 for a fully-foreign list).

        Args:
            session_ids: Session IDs to delete analysis for
            all_versions: If True, delete all versions. If False, only latest.

        Returns:
            Number of analysis records deleted
        """
        if not session_ids:
            return 0

        # Scope session_ids to this profile to prevent cross-profile deletion.
        owned_sessions_subq = (
            select(models.Session.id)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(
                models.Session.id.in_(session_ids),
                self._profile_filter(),
            )
            .subquery()
        )

        if all_versions:
            # Delete all analysis results for owned sessions.
            result = await self.db_session.execute(
                delete(models.AnalysisResult).where(
                    models.AnalysisResult.session_id.in_(
                        select(owned_sessions_subq.c.id)
                    )
                )
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
        else:
            # Delete only the latest (highest created_at) result per owned session.
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
                .where(
                    models.AnalysisResult.session_id.in_(
                        select(owned_sessions_subq.c.id)
                    )
                )
                .subquery()
            )
            latest_ids = (
                (
                    await self.db_session.execute(
                        select(ranked.c.id).where(ranked.c.rn == 1)
                    )
                )
                .scalars()
                .all()
            )
            if not latest_ids:
                return 0
            result = await self.db_session.execute(
                delete(models.AnalysisResult).where(
                    models.AnalysisResult.id.in_(latest_ids)
                )
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def run_analysis(
        self,
        session_id: int,
        modes: list[str] | None = None,
        store_results: bool = True,
    ) -> AnalysisResult:
        """Run analysis on a session.  Returns AnalysisResult (Pydantic model).

        Validates session ownership before running.  Raises NotFoundError for
        foreign or missing session IDs.

        Owns a short read scope for the I/O phase and closes it before compute,
        so the injected request session is never held across NumPy/scipy work.
        CPU-bound compute runs in a thread via asyncio.to_thread().
        """
        import time  # noqa: PLC0415

        from snore.analysis.service import AnalysisService  # noqa: PLC0415
        from snore.database.session import session_scope  # noqa: PLC0415
        from snore.exceptions import NotFoundError as _NotFoundError  # noqa: PLC0415

        t_start = time.monotonic()

        # I/O phase: open a dedicated short async scope — close it before compute.
        # Ownership check runs inside this scope so self.db_session is never used
        # (it may already be closed by the CLI before this method is called).
        async with session_scope() as read_db:
            owned = (
                await read_db.execute(
                    select(models.Session.id)
                    .join(models.Device, models.Session.device_id == models.Device.id)
                    .where(
                        models.Session.id == session_id,
                        models.Device.profile_id == self.profile_id,
                    )
                )
            ).scalar_one_or_none()
            if owned is None:
                raise _NotFoundError(f"Session {session_id} not found")

            read_svc = AnalysisService(read_db)
            raw = await read_svc.load_session_inputs_raw(session_id, modes=modes)
        # Session closed; prepare DTO (NumPy deserialization) — still sync/fast.
        inputs = AnalysisService.prepare_inputs(raw)

        # Compute phase: CPU-bound scipy/NumPy runs in a thread.
        compute_svc = AnalysisService()  # no db_session — compute only
        result = await asyncio.to_thread(compute_svc.compute_analysis, inputs)

        processing_time_ms = int((time.monotonic() - t_start) * 1000)

        if store_results:
            # Write phase: short INSERT-only scope.
            async with session_scope() as write_db:
                write_svc = AnalysisService(write_db)
                await write_svc.store_result(result, processing_time_ms)

        return result

    async def get_analysis_result(self, session_id: int) -> AnalysisResult | None:
        """Get stored analysis result for a session, or None if not found.

        Validates session ownership — returns None for foreign IDs (treating
        "not yet analyzed" and "not found" the same to avoid oracle attacks).
        """
        # Ownership check: confirm session belongs to this profile.
        owned = (
            await self.db_session.execute(
                select(models.Session.id)
                .join(models.Device, models.Session.device_id == models.Device.id)
                .where(
                    models.Session.id == session_id,
                    self._profile_filter(),
                )
            )
        ).scalar_one_or_none()
        if owned is None:
            return None

        analysis_row = (
            (
                await self.db_session.execute(
                    select(models.AnalysisResult)
                    .filter_by(session_id=session_id)
                    .order_by(models.AnalysisResult.created_at.desc())
                )
            )
            .scalars()
            .first()
        )

        if analysis_row is None:
            return None
        return AnalysisResult.model_validate(analysis_row.programmatic_result_json)

    async def run_batch_analysis(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        modes: Sequence[str] | None = None,
        store_results: bool = True,
        max_workers: int = 4,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> BatchAnalysisResult:
        """Run analysis on multiple sessions in parallel.

        Delegates to ``BatchAnalysisCoordinator`` so the executor
        internals can change without touching callers.

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
        stmt = (
            select(
                models.Session.id.label("session_id"),
                models.Day.date.label("day_date"),
            )
            .join(models.Device, models.Session.device_id == models.Device.id)
            .join(models.Day, models.Session.day_id == models.Day.id)
            .where(self._profile_filter())
        )
        if from_date:
            stmt = stmt.where(models.Day.date >= from_date.date())
        if to_date:
            stmt = stmt.where(models.Day.date <= to_date.date())
        stmt = stmt.order_by(models.Day.date)

        # Count matched sessions up front without materializing rows so
        # `total` reflects matched, not started (honest cancellation accounting).
        count_stmt = select(func.count()).select_from(stmt.subquery())
        matched_total: int = (await self.db_session.execute(count_stmt)).scalar_one()
        if matched_total == 0:
            return BatchAnalysisResult(
                total=0, successful=0, failed=0, cancelled=0, results=[]
            )

        # Stream rows lazily — never materialize a full 10k list.
        async_result = await self.db_session.stream(stmt)

        coordinator = BatchAnalysisCoordinator()
        self._batch_coordinator = coordinator
        return await coordinator.submit(
            matched_total=matched_total,
            session_stream=async_result,
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

    Uses ``asyncio.to_thread()`` for per-session CPU-bound work (NumPy/scipy).
    Database reads and writes happen on the event loop through short-lived
    ``AsyncSession`` scopes; only detached ``RawSessionBlobs`` DTOs cross the
    thread boundary.  The ``submit`` interface is the stable boundary — callers
    are unchanged.
    """

    def __init__(self) -> None:
        self._cancel_requested = False
        self._completed = 0
        self._total = 0
        self.session_dates: dict[int, Any] = {}  # sid → day_date; window-bounded

    def cancel(self) -> None:
        """Request cooperative cancellation.  Checked between sessions."""
        self._cancel_requested = True

    @property
    def progress(self) -> tuple[int, int]:
        """Return (completed, total) session counts."""
        return self._completed, self._total

    async def submit(
        self,
        *,
        matched_total: int,
        session_stream: Any,  # AsyncResult from session.stream(); yields rows lazily
        modes: Sequence[str] | None = None,
        store_results: bool = True,
        max_workers: int = 4,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> BatchAnalysisResult:
        """Execute batch analysis and return aggregated results.

        Each session is processed in three async phases: (1) read — fetch raw
        blobs on the event loop via short-lived ``AsyncSession`` scopes, (2)
        compute — run CPU-bound NumPy/scipy work in ``asyncio.to_thread()`` with
        only a detached ``RawSessionBlobs`` DTO crossing the thread boundary
        (zero DB access in thread), (3) write — persist the result on the event
        loop via ``AsyncSession``.

        Args:
            matched_total: Count of matched sessions (from COUNT query); defines
                ``total`` in the result so cancelled sessions are never silently dropped.
            session_stream: Async stream from ``session.stream(stmt)``; yields rows lazily.
                Scalars only — no ORM objects passed to workers.
            modes: Detection modes to run (``None`` = default).
            store_results: If True, write each result to the DB.
            max_workers: Concurrency cap (number of simultaneous coroutines).
            progress_callback: Called with (completed, total) after each session.

        Returns:
            Aggregated ``BatchAnalysisResult``.
        """
        import time  # noqa: PLC0415

        from snore.analysis.service import AnalysisService  # noqa: PLC0415
        from snore.database.session import session_scope  # noqa: PLC0415

        self._total = matched_total
        self._completed = 0
        # Do NOT reset _cancel_requested if cancel() was called before submit().

        modes_list: list[str] | None = list(modes) if modes is not None else None

        def _compute_only(raw: Any) -> Any:
            """Pure-compute phase — NumPy/scipy only, zero DB access."""
            inputs = AnalysisService.prepare_inputs(raw)
            return AnalysisService().compute_analysis(inputs)

        # Build a lazy async iterator from the stream — never materialize all rows.
        # The sliding window pulls pairs one at a time; at most max_workers jobs
        # are in flight simultaneously.  session_dates is pruned as tasks complete
        # so retained metadata stays window-bounded.
        stream_iter = session_stream.__aiter__()
        stream_exhausted = False

        batch_results: list[BatchSessionResult] = []
        pending: dict[asyncio.Task[str], int] = {}  # task → sid
        session_dates = self.session_dates  # instance attribute for observability
        session_dates.clear()
        sem = asyncio.Semaphore(max_workers)

        async def _run_one(sid: int) -> str:
            """Read → thread-compute → write, all async; semaphore caps concurrency."""
            async with sem:
                if self._cancel_requested:
                    return "cancelled"
                try:
                    t_start = time.monotonic()

                    # --- I/O read phase: fetch raw blobs on the event loop ---
                    async with session_scope() as read_session:
                        svc = AnalysisService(
                            read_session,
                            # Reuse modes_list from outer scope (snapshot).
                        )
                        raw = await svc.load_session_inputs_raw(
                            sid,
                            modes=modes_list,
                        )

                    # --- Compute phase: NumPy only in a thread, no session held ---
                    result = await asyncio.to_thread(_compute_only, raw)
                    processing_time_ms = int((time.monotonic() - t_start) * 1000)

                    # --- Write phase: persist result on the event loop ---
                    if store_results and result is not None:
                        async with session_scope() as write_session:
                            write_svc = AnalysisService(write_session)
                            await write_svc.store_result(result, processing_time_ms)

                    return "success"
                except Exception as e:
                    logger.warning(
                        "Failed to analyze session %d: %s", sid, e, exc_info=True
                    )
                    return "error"

        async def _next_pair() -> tuple[int, date | None] | None:
            """Pull one row from the stream; return None when exhausted."""
            nonlocal stream_exhausted
            if stream_exhausted:
                return None
            try:
                row = await stream_iter.__anext__()
                return (row.session_id, row.day_date)
            except StopAsyncIteration:
                stream_exhausted = True
                return None

        async def _fill_window() -> None:
            """Enqueue pairs until max_workers tasks are in flight or stream exhausted."""
            while len(pending) < max_workers:
                if self._cancel_requested:
                    break
                pair = await _next_pair()
                if pair is None:
                    break
                sid, day_date = pair
                session_dates[sid] = day_date
                task: asyncio.Task[str] = asyncio.create_task(_run_one(sid))
                pending[task] = sid

        await _fill_window()
        while pending:
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                sid = pending.pop(task)
                outcome = task.result()
                cancelled = outcome == "cancelled"
                success = outcome == "success"
                error = (
                    None if outcome != "error" else f"Analysis failed for session {sid}"
                )
                batch_results.append(
                    BatchSessionResult(
                        session_id=sid,
                        session_date=session_dates.pop(sid, None),
                        success=success,
                        cancelled=cancelled,
                        error=error,
                    )
                )
                self._completed += 1
                if progress_callback:
                    progress_callback(self._completed, matched_total)
            # Refill the window after each completion.
            await _fill_window()

        # Drain any rows remaining in the stream after cancellation.
        # Each unconsumed row is counted as cancelled so `total` stays honest.
        if self._cancel_requested:
            while True:
                pair = await _next_pair()
                if pair is None:
                    break
                sid, day_date = pair
                batch_results.append(
                    BatchSessionResult(
                        session_id=sid,
                        session_date=day_date,
                        success=False,
                        cancelled=True,
                        error=None,
                    )
                )

        successful = sum(1 for r in batch_results if r.success)
        cancelled_count = sum(1 for r in batch_results if r.cancelled)
        return BatchAnalysisResult(
            total=matched_total,
            successful=successful,
            failed=matched_total - successful - cancelled_count,
            cancelled=cancelled_count,
            results=batch_results,
        )
