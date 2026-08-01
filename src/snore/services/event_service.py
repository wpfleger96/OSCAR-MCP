"""Event matching service for comparing machine vs programmatic detections."""

from __future__ import annotations

import bisect

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.modes.postprocess import EVENT_MATCH_TOLERANCE_SECONDS
from snore.exceptions import NotFoundError
from snore.services.schemas import EventMatchResult

__all__ = ["EVENT_MATCH_TOLERANCE_SECONDS", "EventService"]


class EventService:
    """Service for event matching and comparison."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def list_session_events(
        self,
        session_id: int,
        event_type: str | None = None,
    ) -> tuple[list[Any], datetime]:
        """Return (events, session_start) for a session."""
        from snore.database import models  # noqa: PLC0415

        session = (
            (
                await self.db_session.execute(
                    select(models.Session).where(models.Session.id == session_id)
                )
            )
            .scalars()
            .first()
        )
        if session is None:
            raise NotFoundError(f"Session {session_id} not found")

        stmt = select(models.Event).where(models.Event.session_id == session_id)
        if event_type:
            stmt = stmt.where(models.Event.event_type == event_type)
        stmt = stmt.order_by(models.Event.start_time)
        events = list((await self.db_session.execute(stmt)).scalars().all())
        return events, session.start_time

    async def get_machine_event_times(self, session_id: int) -> list[float]:
        """Return sorted machine event timestamps for a session."""
        from snore.database import models  # noqa: PLC0415

        session = (
            (
                await self.db_session.execute(
                    select(models.Session).where(models.Session.id == session_id)
                )
            )
            .scalars()
            .first()
        )
        if session is None:
            raise NotFoundError(f"Session {session_id} not found")

        events = (
            (
                await self.db_session.execute(
                    select(models.Event).where(models.Event.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        return sorted(e.start_time.timestamp() for e in events)

    @staticmethod
    def _within_tolerance(
        t: float, sorted_other: list[float], tolerance: float
    ) -> bool:
        idx = bisect.bisect_left(sorted_other, t - tolerance)
        return any(
            abs(t - sorted_other[j]) <= tolerance
            for j in range(idx, min(idx + 10, len(sorted_other)))
        )

    @staticmethod
    def match_events(
        machine_times: list[float],
        programmatic_times: list[float],
        tolerance: float = EVENT_MATCH_TOLERANCE_SECONDS,
    ) -> EventMatchResult:
        """Match machine vs programmatic events using bisect-based tolerance matching."""
        sorted_machine = sorted(machine_times)
        sorted_prog = sorted(programmatic_times)

        false_negatives = sum(
            not EventService._within_tolerance(t, sorted_prog, tolerance)
            for t in sorted_machine
        )
        false_positives = sum(
            not EventService._within_tolerance(t, sorted_machine, tolerance)
            for t in sorted_prog
        )

        machine_count = len(sorted_machine)
        prog_count = len(sorted_prog)
        matched_count = machine_count - false_negatives

        return EventMatchResult(
            machine_count=machine_count,
            programmatic_count=prog_count,
            matched=matched_count,
            false_positives=false_positives,
            false_negatives=false_negatives,
        )

    @staticmethod
    def classify_matches(
        machine_times: list[float],
        programmatic_times: list[float],
        tolerance: float = EVENT_MATCH_TOLERANCE_SECONDS,
    ) -> tuple[list[bool], list[bool]]:
        """Classify each event as matched or unmatched."""
        sorted_machine = sorted(machine_times)
        sorted_prog = sorted(programmatic_times)

        machine_matched = [
            EventService._within_tolerance(t, sorted_prog, tolerance)
            for t in sorted_machine
        ]
        prog_matched = [
            EventService._within_tolerance(t, sorted_machine, tolerance)
            for t in sorted_prog
        ]

        return machine_matched, prog_matched
