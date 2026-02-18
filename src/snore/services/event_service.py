"""Event matching service for comparing machine vs programmatic detections."""

from __future__ import annotations

import bisect

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from snore.services.schemas import EventMatchResult

__all__ = ["EventService"]

EVENT_MATCH_TOLERANCE_SECONDS = 5.0


class EventService:
    """Service for event matching and comparison.

    Pass db_session to use DB-backed methods (list_session_events,
    get_machine_event_times). Omit for purely algorithmic operations
    (match_events, classify_matches).
    """

    def __init__(self, db_session: Session | None = None):
        self.db_session = db_session

    def list_session_events(
        self,
        session_id: int,
        event_type: str | None = None,
    ) -> tuple[list[Any], datetime] | None:
        """Return (events, session_start) for a session, or None if session not found.

        Args:
            session_id: Session to query events for.
            event_type: Optional filter by event type string.

        Returns:
            Tuple of (list of Event ORM objects, session start datetime), or None.
        """
        from snore.database import models

        session = (
            self.db_session.query(models.Session)  # type: ignore[union-attr]
            .filter(models.Session.id == session_id)
            .first()
        )
        if session is None:
            return None

        query = self.db_session.query(models.Event).filter(  # type: ignore[union-attr]
            models.Event.session_id == session_id
        )
        if event_type:
            query = query.filter(models.Event.event_type == event_type)
        events = query.order_by(models.Event.start_time).all()
        return events, session.start_time

    def get_machine_event_times(self, session_id: int) -> list[float] | None:
        """Return sorted machine event timestamps for a session, or None if not found.

        Args:
            session_id: Session to query events for.

        Returns:
            Sorted list of Unix timestamps, or None if session not found.
        """
        from snore.database import models

        session = (
            self.db_session.query(models.Session)  # type: ignore[union-attr]
            .filter(models.Session.id == session_id)
            .first()
        )
        if session is None:
            return None

        events = (
            self.db_session.query(models.Event)  # type: ignore[union-attr]
            .filter(models.Event.session_id == session_id)
            .all()
        )
        return sorted(e.start_time.timestamp() for e in events)

    def match_events(
        self,
        machine_times: list[float],
        programmatic_times: list[float],
        tolerance: float = EVENT_MATCH_TOLERANCE_SECONDS,
    ) -> EventMatchResult:
        """Match machine vs programmatic events using bisect-based tolerance matching.

        Args:
            machine_times: Sorted list of machine event timestamps (seconds)
            programmatic_times: Sorted list of programmatic event timestamps (seconds)
            tolerance: Maximum time difference for a match (default 5.0s)

        Returns:
            EventMatchResult with counts of matched, false positives, false negatives
        """
        sorted_machine = sorted(machine_times)
        sorted_prog = sorted(programmatic_times)

        false_negatives = 0
        for t in sorted_machine:
            idx = bisect.bisect_left(sorted_prog, t - tolerance)
            matched = any(
                abs(t - sorted_prog[j]) <= tolerance
                for j in range(idx, min(idx + 10, len(sorted_prog)))
            )
            if not matched:
                false_negatives += 1

        false_positives = 0
        for t in sorted_prog:
            idx = bisect.bisect_left(sorted_machine, t - tolerance)
            matched = any(
                abs(t - sorted_machine[j]) <= tolerance
                for j in range(idx, min(idx + 10, len(sorted_machine)))
            )
            if not matched:
                false_positives += 1

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

    def classify_matches(
        self,
        machine_times: list[float],
        programmatic_times: list[float],
        tolerance: float = EVENT_MATCH_TOLERANCE_SECONDS,
    ) -> tuple[list[bool], list[bool]]:
        """Classify each event as matched or unmatched.

        Args:
            machine_times: Sorted list of machine event timestamps (seconds)
            programmatic_times: Sorted list of programmatic event timestamps (seconds)
            tolerance: Maximum time difference for a match (default 5.0s)

        Returns:
            Tuple of (machine_matched, programmatic_matched) where each is a list of booleans
        """
        sorted_machine = sorted(machine_times)
        sorted_prog = sorted(programmatic_times)

        machine_matched = []
        for t in sorted_machine:
            idx = bisect.bisect_left(sorted_prog, t - tolerance)
            matched = any(
                abs(t - sorted_prog[j]) <= tolerance
                for j in range(idx, min(idx + 10, len(sorted_prog)))
            )
            machine_matched.append(matched)

        prog_matched = []
        for t in sorted_prog:
            idx = bisect.bisect_left(sorted_machine, t - tolerance)
            matched = any(
                abs(t - sorted_machine[j]) <= tolerance
                for j in range(idx, min(idx + 10, len(sorted_machine)))
            )
            prog_matched.append(matched)

        return machine_matched, prog_matched
