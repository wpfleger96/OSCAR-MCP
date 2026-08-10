"""
Batch validation logic for running validation across multiple sessions.
"""

import logging

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.modes.config import AASM_CONFIG
from snore.analysis.modes.detector import EventDetector
from snore.analysis.utils import convert_machine_events
from snore.database import models
from snore.validation.report import (
    AggregateMetrics,
    SessionValidation,
    ValidationReport,
)

logger = logging.getLogger(__name__)


class BatchValidator:
    """Runs validation across multiple sessions."""

    def __init__(self, db_session: AsyncSession, profile_id: int):
        """
        Initialize batch validator.

        Args:
            db_session: Async database session
            profile_id: Profile ID to scope all queries — required, never global.
        """
        self.db_session = db_session
        self.profile_id = profile_id

    async def validate_date_range(
        self,
        date_from: str,
        date_to: str,
        mode: str = "aasm",
    ) -> ValidationReport:
        """
        Run validation across a date range.

        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            mode: Detection mode to validate (default: aasm)

        Returns:
            ValidationReport with aggregate and per-session metrics
        """
        stmt = (
            select(models.Session)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(
                models.Device.profile_id == self.profile_id,
                models.Session.start_time >= datetime.fromisoformat(date_from),
                models.Session.start_time
                <= datetime.fromisoformat(f"{date_to} 23:59:59"),
            )
        )

        sessions = (
            (await self.db_session.execute(stmt.order_by(models.Session.start_time)))
            .scalars()
            .all()
        )

        logger.info(f"Found {len(sessions)} sessions between {date_from} and {date_to}")

        # Bulk-fetch all Statistics rows for the session set in one query.
        session_ids = [s.id for s in sessions]
        stats_by_session_id: dict[int, models.Statistics] = {}
        if session_ids:
            stat_rows = (
                (
                    await self.db_session.execute(
                        select(models.Statistics).where(
                            models.Statistics.session_id.in_(session_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            stats_by_session_id = {int(r.session_id): r for r in stat_rows}

        session_validations = []

        for session in sessions:
            try:
                validation = await self._validate_session(
                    session.id, mode, stats_by_session_id
                )
                if validation:
                    session_validations.append(validation)
            except Exception as e:
                logger.warning(f"Failed to validate session {session.id}: {e}")
                continue

        aggregate = self._calculate_aggregate(session_validations)

        return ValidationReport(
            report_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            date_range_start=date_from,
            date_range_end=date_to,
            aggregate=aggregate,
            sessions=session_validations,
        )

    async def _validate_session(
        self,
        session_id: int,
        mode: str,
        stats_by_session_id: dict[int, models.Statistics],
    ) -> SessionValidation | None:
        """
        Validate a single session.

        Args:
            session_id: Session ID to validate
            mode: Detection mode
            stats_by_session_id: Pre-fetched Statistics rows keyed by session_id;
                avoids a per-session query inside the validation loop.

        Returns:
            SessionValidation or None if validation fails
        """
        # Use an already-scoped facade so ownership is enforced on every path.
        from snore.services.analysis_facade import AnalysisFacade  # noqa: PLC0415

        facade = AnalysisFacade(self.db_session, self.profile_id)

        session = await self.db_session.get(models.Session, session_id)
        if not session:
            return None

        analysis_result = await facade.get_analysis_result(session_id)
        if not analysis_result:
            logger.info(f"Running analysis for session {session_id}...")
            analysis_result = await facade.run_analysis(session_id, modes=[mode])

        if mode not in analysis_result.mode_results:
            logger.warning(
                f"Mode {mode} not found in analysis results for {session_id}"
            )
            return None

        mode_result = analysis_result.mode_results[mode]
        machine_events = analysis_result.machine_events

        machine_apneas, machine_hypopneas = convert_machine_events(machine_events)

        detector = EventDetector(AASM_CONFIG)
        validation = detector.validate_against_machine_events(
            mode_result.apneas,
            mode_result.hypopneas,
            machine_apneas,
            machine_hypopneas,
        )

        apnea_val = validation["apnea_validation"]
        hypopnea_val = validation["hypopnea_validation"]

        notes = None
        if apnea_val.sensitivity < 0.6 or hypopnea_val.sensitivity < 0.6:
            notes = "Low sensitivity - investigate this session"

        # Device-reported nightly indices from pre-fetched Statistics map.
        # Null-safe: APAP and vAuto record different index subsets.
        stats_row = stats_by_session_id.get(session_id)

        return SessionValidation(
            session_id=session_id,
            date=session.start_time.strftime("%Y-%m-%d"),
            duration_hours=analysis_result.session_duration_hours,
            machine_event_count=len(machine_events),
            programmatic_event_count=len(mode_result.apneas)
            + len(mode_result.hypopneas),
            apnea_sensitivity=apnea_val.sensitivity,
            apnea_precision=apnea_val.precision,
            apnea_f1=apnea_val.f1_score,
            hypopnea_sensitivity=hypopnea_val.sensitivity,
            hypopnea_precision=hypopnea_val.precision,
            hypopnea_f1=hypopnea_val.f1_score,
            notes=notes,
            device_ahi=stats_row.ahi if stats_row is not None else None,
            device_oai=stats_row.oai if stats_row is not None else None,
            device_cai=stats_row.cai if stats_row is not None else None,
            device_hi=stats_row.hi if stats_row is not None else None,
            device_uai=stats_row.uai if stats_row is not None else None,
        )

    def _calculate_aggregate(
        self, sessions: list[SessionValidation]
    ) -> AggregateMetrics:
        """
        Calculate aggregate metrics across sessions.

        Args:
            sessions: List of session validations

        Returns:
            AggregateMetrics
        """
        if not sessions:
            return AggregateMetrics(
                total_sessions=0,
                total_machine_events=0,
                total_programmatic_events=0,
                avg_apnea_sensitivity=0.0,
                avg_apnea_precision=0.0,
                avg_apnea_f1=0.0,
                avg_hypopnea_sensitivity=0.0,
                avg_hypopnea_precision=0.0,
                avg_hypopnea_f1=0.0,
                low_sensitivity_sessions=[],
            )

        total_machine = sum(s.machine_event_count for s in sessions)
        total_prog = sum(s.programmatic_event_count for s in sessions)

        avg_apnea_sens = sum(s.apnea_sensitivity for s in sessions) / len(sessions)
        avg_apnea_prec = sum(s.apnea_precision for s in sessions) / len(sessions)
        avg_apnea_f1 = sum(s.apnea_f1 for s in sessions) / len(sessions)

        avg_hypopnea_sens = sum(s.hypopnea_sensitivity for s in sessions) / len(
            sessions
        )
        avg_hypopnea_prec = sum(s.hypopnea_precision for s in sessions) / len(sessions)
        avg_hypopnea_f1 = sum(s.hypopnea_f1 for s in sessions) / len(sessions)

        low_sens_sessions = [
            s.session_id
            for s in sessions
            if s.apnea_sensitivity < 0.6 or s.hypopnea_sensitivity < 0.6
        ]

        return AggregateMetrics(
            total_sessions=len(sessions),
            total_machine_events=total_machine,
            total_programmatic_events=total_prog,
            avg_apnea_sensitivity=avg_apnea_sens,
            avg_apnea_precision=avg_apnea_prec,
            avg_apnea_f1=avg_apnea_f1,
            avg_hypopnea_sensitivity=avg_hypopnea_sens,
            avg_hypopnea_precision=avg_hypopnea_prec,
            avg_hypopnea_f1=avg_hypopnea_f1,
            low_sensitivity_sessions=low_sens_sessions,
        )
