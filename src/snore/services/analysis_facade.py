"""Analysis facade for listing and managing analysis results."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Query, Session

from snore.database import models
from snore.services.schemas import (
    AnalysisDeletePreview,
    AnalysisListItem,
    AnalysisSessionDetail,
    BatchAnalysisResult,
    BatchSessionResult,
)

__all__ = ["AnalysisFacade"]


class AnalysisFacade:
    """Facade for analysis listing and deletion operations."""

    def __init__(self, db_session: Session):
        """
        Initialize analysis facade.

        Args:
            db_session: SQLAlchemy database session
        """
        self.db_session = db_session

    def _status_query(
        self,
        start: datetime | None,
        end: datetime | None,
        analyzed_only: bool,
    ) -> Query[models.Session]:
        """Build the shared session query for list/count of analysis status."""
        query = self.db_session.query(models.Session).join(models.Day)

        if start:
            query = query.filter(models.Day.date >= start.date())
        if end:
            query = query.filter(models.Day.date <= end.date())

        if analyzed_only:
            query = query.filter(
                self.db_session.query(models.AnalysisResult)
                .filter(models.AnalysisResult.session_id == models.Session.id)
                .exists()
            )

        return query

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
        query = self._status_query(start, end, analyzed_only)

        sort_clauses: dict[str, Any] = {
            "date-asc": models.Day.date.asc(),
            "date-desc": models.Day.date.desc(),
            "session-id": models.Session.id.asc(),
        }

        sort_clause = sort_clauses.get(sort_by, models.Day.date.desc())
        query = query.order_by(sort_clause)

        if offset > 0:
            query = query.offset(offset)

        if limit > 0:
            query = query.limit(limit)

        sessions = query.all()

        latest_analysis = self._latest_analysis_ids([s.id for s in sessions])

        results = []
        for session in sessions:
            analysis_id = latest_analysis.get(session.id)

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
        return self._status_query(start, end, analyzed_only).count()

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
            result = self.db_session.execute(
                text(
                    "DELETE FROM analysis_results WHERE session_id IN :session_ids"
                ).bindparams(bindparam("session_ids", expanding=True)),
                {"session_ids": session_ids},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
        else:
            deleted_count = 0
            for session_id in session_ids:
                latest_result = self.db_session.execute(
                    text(
                        """
                        SELECT id FROM analysis_results
                        WHERE session_id = :session_id
                        ORDER BY created_at DESC
                        LIMIT 1
                    """
                    ),
                    {"session_id": session_id},
                ).fetchone()

                if latest_result:
                    self.db_session.execute(
                        text("DELETE FROM analysis_results WHERE id = :analysis_id"),
                        {"analysis_id": latest_result.id},
                    )
                    deleted_count += 1

            return deleted_count

    def run_analysis(
        self,
        session_id: int,
        modes: list[str] | None = None,
        store_results: bool = True,
    ) -> Any:
        """Run analysis on a session. Returns AnalysisResult (Pydantic model)."""
        from snore.analysis.service import AnalysisService

        svc = AnalysisService(self.db_session)
        return svc.analyze_session(session_id, modes=modes, store_results=store_results)

    def get_analysis_result(self, session_id: int) -> Any:
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
        modes: list[str] | None = None,
        store_results: bool = True,
        max_workers: int = 4,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> BatchAnalysisResult:
        """Run analysis on multiple sessions in parallel.

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
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from snore.analysis.service import AnalysisService
        from snore.database.session import session_scope

        query = self.db_session.query(models.Session).join(models.Day)
        if from_date:
            query = query.filter(models.Day.date >= from_date.date())
        if to_date:
            query = query.filter(models.Day.date <= to_date.date())

        sessions = query.order_by(models.Day.date).all()
        # Build date map before spawning threads to avoid DB access in workers
        session_dates: dict[int, date | None] = {
            s.id: s.day.date if s.day else s.start_time.date() for s in sessions
        }
        session_ids = [s.id for s in sessions]
        total = len(session_ids)

        if not session_ids:
            return BatchAnalysisResult(total=0, successful=0, failed=0, results=[])

        batch_results: list[BatchSessionResult] = []
        completed = 0

        def analyze_one(sid: int) -> None:
            with session_scope() as thread_session:
                svc = AnalysisService(thread_session)
                svc.analyze_session(
                    session_id=sid, modes=modes, store_results=store_results
                )

        with ThreadPoolExecutor(max_workers=min(max_workers, total)) as executor:
            futures = {executor.submit(analyze_one, sid): sid for sid in session_ids}
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    future.result()
                    success, error = True, None
                except Exception as e:
                    success, error = False, str(e)
                batch_results.append(
                    BatchSessionResult(
                        session_id=sid,
                        session_date=session_dates.get(sid),
                        success=success,
                        error=error,
                    )
                )
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        successful = sum(1 for r in batch_results if r.success)
        return BatchAnalysisResult(
            total=total,
            successful=successful,
            failed=total - successful,
            results=batch_results,
        )
