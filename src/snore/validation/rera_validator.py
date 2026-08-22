"""
RERA date-range validator.

Scores SNORE's two programmatic RERA definitions — the analysis-time
amplitude-crescendo detector (``mode_result.reras``) and the query-time FL-run
proxy (recomputed from stored breaths) — against the ResMed device's
machine-flagged RE events, independently and per session.

The device flags RE conservatively, so most sessions carry zero machine RE and
are skipped (``no_machine_re_events``); near-zero precision on the rest is the
expected, correct output.  The aggregate carries a chance-precision floor so
those scores read as context.  This module changes no algorithm or threshold —
it only measures.

Design seam
-----------
The scoring core is two pure, module-level, array-in functions with no DB or
session dependencies, so an offline tuning sweep can import them directly:

- ``score_rera_definition(prog_starts, machine_starts, tolerance) -> ReraScore``
- ``proxy_reras_from_breath_arrays(..., *, fl_class_threshold, min_fl_run_length,
  recovery_amplitude_margin) -> list[float]``

The validator methods are thin wrappers over these.
"""

from __future__ import annotations

import logging

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.modes.postprocess import (
    EVENT_MATCH_TOLERANCE_SECONDS,
    validate_event_type,
)
from snore.analysis.shared.types import RERAEvent
from snore.analysis.shared.versioning import AlgoVersions, AnalysisStatus
from snore.analysis.types import AnalysisResult
from snore.analysis.utils import convert_machine_reras
from snore.constants import RERAProxyConstants
from snore.database import models
from snore.services.breath.algorithms import iter_fl_run_recoveries
from snore.services.breath_service import BreathService
from snore.validation.rera_report import (
    ReraAggregateMetrics,
    ReraSessionValidation,
    ReraValidationReport,
)
from snore.validation.stats import mean_or_none

logger = logging.getLogger(__name__)

# Reasons attached to null metric fields (null-with-reason convention).
_REASON_NO_MACHINE_RE = "no_machine_re_events"
_REASON_NO_PROGRAMMATIC = "no_programmatic_events"
_REASON_ZERO_DURATION = "zero_duration"


# ---------------------------------------------------------------------------
# Pure scoring core — no DB/session dependencies; imported by the sweep harness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReraScore:
    """One programmatic RERA definition scored against machine RE.

    ``sensitivity`` is null only when there are no machine events; ``precision``
    and ``f1`` are null when the definition produced no events (both undefined).
    """

    machine_count: int
    programmatic_count: int
    matched: int
    sensitivity: float | None
    precision: float | None
    f1: float | None


def _as_rera_events(starts: Sequence[float]) -> list[RERAEvent]:
    """Wrap start times as schema-valid RERAEvents; only start_time is matched."""
    return [
        RERAEvent(
            start_time=t,
            end_time=t,
            duration=0.0,
            obstructed_breath_count=2,
            recovery_amplitude_increase_pct=0.0,
            confidence=1.0,
            baseline_flow=0.0,
        )
        for t in starts
    ]


def score_rera_definition(
    prog_starts: Sequence[float],
    machine_starts: Sequence[float],
    tolerance: float = EVENT_MATCH_TOLERANCE_SECONDS,
) -> ReraScore:
    """Score programmatic RERA start times against machine RE start times.

    Matching is delegated to ``validate_event_type`` (the shared per-type
    start-time matcher).  Sensitivity/precision are re-derived from the matched
    count so undefined ratios surface as ``None`` (rather than the matcher's
    vacuous 1.0), which the report renders as null-with-reason.
    """
    result, _matched = validate_event_type(
        _as_rera_events(prog_starts), _as_rera_events(machine_starts), tolerance
    )
    machine_count = result.machine_event_count
    prog_count = result.programmatic_event_count
    matched = result.matched_events

    sensitivity = matched / machine_count if machine_count else None
    precision = matched / prog_count if prog_count else None
    if sensitivity is None or precision is None:
        f1: float | None = None
    elif sensitivity + precision == 0.0:
        f1 = 0.0
    else:
        f1 = 2 * precision * sensitivity / (precision + sensitivity)

    return ReraScore(
        machine_count=machine_count,
        programmatic_count=prog_count,
        matched=matched,
        sensitivity=sensitivity,
        precision=precision,
        f1=f1,
    )


@dataclass(frozen=True)
class _ProxyBreath:
    """Minimal breath row exposing only the fields the FL-run proxy reads."""

    flow_class: int | None
    is_recovery_breath: bool | None
    peak_flow_lpm: float | None


def proxy_reras_from_breath_arrays(
    flow_class: Sequence[int | None],
    is_recovery_breath: Sequence[bool | None],
    peak_flow_lpm: Sequence[float | None],
    start_offset_s: Sequence[float],
    *,
    fl_class_threshold: int = RERAProxyConstants.FL_CLASS_THRESHOLD,
    min_fl_run_length: int = RERAProxyConstants.MIN_FL_RUN_LENGTH,
    recovery_amplitude_margin: float = RERAProxyConstants.RECOVERY_AMPLITUDE_MARGIN,
) -> list[float]:
    """Recompute FL-run-proxy RERA start offsets from parallel breath arrays.

    Wraps the arrays as breath rows and delegates to ``iter_fl_run_recoveries``
    (the single implementation of the proxy criterion), mapping each yielded run
    to the ``start_offset_s`` of its first flow-limited breath.  All four arrays
    must be ordered by ``breath_number`` and equal length.  Tunables default to
    ``RERAProxyConstants`` and are forwarded verbatim.
    """
    rows = [
        _ProxyBreath(flow_class=fc, is_recovery_breath=rec, peak_flow_lpm=pk)
        for fc, rec, pk in zip(
            flow_class, is_recovery_breath, peak_flow_lpm, strict=True
        )
    ]
    return [
        start_offset_s[run_start]
        for run_start, _run_last, _recovery in iter_fl_run_recoveries(
            rows,
            fl_class_threshold=fl_class_threshold,
            min_fl_run_length=min_fl_run_length,
            recovery_amplitude_margin=recovery_amplitude_margin,
        )
    ]


# ---------------------------------------------------------------------------
# Validator — thin DB wrapper over the pure scoring core
# ---------------------------------------------------------------------------


class ReraValidator:
    """Validates SNORE's two RERA definitions against machine RE events."""

    def __init__(self, db_session: AsyncSession, profile_id: int) -> None:
        self._db = db_session
        self._profile_id = profile_id

    async def validate_date_range(
        self,
        date_from: str,
        date_to: str,
    ) -> ReraValidationReport:
        """Run RERA validation across a date range.

        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)

        Returns:
            ReraValidationReport with aggregate and per-session metrics.
        """
        stmt = (
            select(models.Session)
            .join(models.Device, models.Session.device_id == models.Device.id)
            .where(
                models.Device.profile_id == self._profile_id,
                models.Session.start_time >= datetime.fromisoformat(date_from),
                models.Session.start_time
                <= datetime.fromisoformat(f"{date_to} 23:59:59"),
            )
            .order_by(models.Session.start_time)
        )
        sessions = (await self._db.execute(stmt)).scalars().all()
        logger.info(f"Found {len(sessions)} sessions between {date_from} and {date_to}")

        results: list[ReraSessionValidation] = []
        for session in sessions:
            try:
                results.append(await self._validate_session(session))
            except Exception as e:
                logger.warning(f"Failed to validate session {session.id}: {e}")
                results.append(
                    ReraSessionValidation(
                        session_id=session.id,
                        date=session.start_time.strftime("%Y-%m-%d"),
                        duration_hours=(session.duration_seconds or 0) / 3600.0,
                        skipped_reason="error",
                    )
                )

        aggregate = self._calculate_aggregate(results)
        return ReraValidationReport(
            report_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            date_range_start=date_from,
            date_range_end=date_to,
            aggregate=aggregate,
            sessions=results,
        )

    async def _validate_session(self, session: models.Session) -> ReraSessionValidation:
        """Validate one session against machine RE (possibly skipped)."""
        duration_hours = (session.duration_seconds or 0) / 3600.0
        date_str = session.start_time.strftime("%Y-%m-%d")

        def _skip(reason: str) -> ReraSessionValidation:
            return ReraSessionValidation(
                session_id=session.id,
                date=date_str,
                duration_hours=duration_hours,
                skipped_reason=reason,
            )

        # 1. Guard analysis status (OK + latest run) like fl_validator does.
        breath_svc = BreathService(self._db, self._profile_id)
        status, algo, ar_id = await breath_svc.latest_analysis_for_session(session.id)
        if status != AnalysisStatus.OK or ar_id is None:
            return _skip("no_analysis")

        # 2. Fetch the stored analysis DTO (amplitude RERAs + machine events) by
        #    its known id — ownership is already assured by the profile-scoped
        #    session query above, so a narrow by-id read avoids the facade's
        #    redundant ownership re-check and latest-run re-resolution.
        analysis = await self._analysis_by_id(ar_id)
        if analysis is None or not analysis.mode_results:
            return _skip("no_analysis")

        mode_name = self._select_mode(algo, list(analysis.mode_results.keys()))
        amplitude_starts = [
            r.start_time for r in analysis.mode_results[mode_name].reras
        ]

        # 3. Recompute the FL-run proxy from stored breaths.
        breath_stmt = (
            select(
                models.Breath.flow_class,
                models.Breath.is_recovery_breath,
                models.Breath.peak_flow_lpm,
                models.Breath.start_offset_s,
            )
            .where(models.Breath.analysis_result_id == ar_id)
            .order_by(models.Breath.breath_number)
        )
        breath_rows = (await self._db.execute(breath_stmt)).all()
        if not breath_rows:
            return _skip("no_valid_breaths")

        proxy_starts = proxy_reras_from_breath_arrays(
            [b.flow_class for b in breath_rows],
            [b.is_recovery_breath for b in breath_rows],
            [b.peak_flow_lpm for b in breath_rows],
            [b.start_offset_s for b in breath_rows],
        )

        # 4. Machine RE (session-relative) from the stored analysis events.
        machine_starts = [
            r.start_time for r in convert_machine_reras(analysis.machine_events)
        ]

        machine_re_count = len(machine_starts)
        amplitude_count = len(amplitude_starts)
        proxy_count = len(proxy_starts)

        def _density(count: int) -> tuple[float | None, str | None]:
            if duration_hours <= 0:
                return None, _REASON_ZERO_DURATION
            return count / duration_hours, None

        machine_density, machine_density_reason = _density(machine_re_count)
        amplitude_density, amplitude_density_reason = _density(amplitude_count)
        proxy_density, proxy_density_reason = _density(proxy_count)

        base = ReraSessionValidation(
            session_id=session.id,
            date=date_str,
            duration_hours=duration_hours,
            machine_re_count=machine_re_count,
            amplitude_rera_count=amplitude_count,
            proxy_rera_count=proxy_count,
            machine_re_density=machine_density,
            machine_re_density_reason=machine_density_reason,
            amplitude_density=amplitude_density,
            amplitude_density_reason=amplitude_density_reason,
            proxy_density=proxy_density,
            proxy_density_reason=proxy_density_reason,
        )

        # 5. No machine RE (the dominant case): keep counts/densities, null the
        #    scores with a reason, and mark skipped for aggregate exclusion.
        if machine_re_count == 0:
            for prefix in ("amplitude", "proxy"):
                for metric in ("sensitivity", "precision", "f1"):
                    setattr(base, f"{prefix}_{metric}_reason", _REASON_NO_MACHINE_RE)
            base.skipped_reason = _REASON_NO_MACHINE_RE
            return base

        # 6. Score both definitions independently against machine RE.
        self._apply_score(
            base, "amplitude", score_rera_definition(amplitude_starts, machine_starts)
        )
        self._apply_score(
            base, "proxy", score_rera_definition(proxy_starts, machine_starts)
        )
        return base

    async def _analysis_by_id(self, analysis_id: int) -> AnalysisResult | None:
        """Load the stored analysis DTO by its known result id.

        A narrow primary-key read: the caller already holds a profile-scoped
        ``analysis_id``, so no ownership re-check or latest-run resolution is
        needed.  Returns None if the row has vanished.
        """
        row = await self._db.get(models.AnalysisResult, analysis_id)
        if row is None:
            return None
        return AnalysisResult.from_stored_json(row.programmatic_result_json)

    @staticmethod
    def _apply_score(
        record: ReraSessionValidation, prefix: str, score: ReraScore
    ) -> None:
        """Write one definition's matched/sensitivity/precision/F1 (+ reasons)."""
        setattr(record, f"{prefix}_matched", score.matched)
        setattr(record, f"{prefix}_sensitivity", score.sensitivity)
        setattr(record, f"{prefix}_precision", score.precision)
        setattr(record, f"{prefix}_f1", score.f1)
        # Undefined precision/F1 mean the definition produced zero events.
        if score.precision is None:
            setattr(record, f"{prefix}_precision_reason", _REASON_NO_PROGRAMMATIC)
        if score.f1 is None:
            setattr(record, f"{prefix}_f1_reason", _REASON_NO_PROGRAMMATIC)

    @staticmethod
    def _select_mode(algo: AlgoVersions | None, available: list[str]) -> str:
        """Pick the analysis mode to read amplitude RERAs from.

        Prefers the run's persisted primary mode; otherwise falls back to
        ``aasm`` when present, else the first available mode.
        """
        primary = algo.run.primary_mode if algo is not None else None
        if primary in available:
            return primary
        return "aasm" if "aasm" in available else available[0]

    @staticmethod
    def _calculate_aggregate(
        sessions: list[ReraSessionValidation],
    ) -> ReraAggregateMetrics:
        scored = [
            s for s in sessions if s.skipped_reason is None and s.machine_re_count > 0
        ]

        def _count_skip(reason: str) -> int:
            return sum(1 for s in sessions if s.skipped_reason == reason)

        # Pooled densities over sessions that were genuinely evaluated for RE —
        # scored plus skipped-for-no-machine-RE.  Sessions skipped for no
        # analysis / no breaths / error carry structurally-zero event counts, so
        # their hours would dilute every density and understate the chance floor.
        with_hours = [
            s
            for s in sessions
            if s.duration_hours > 0
            and s.skipped_reason in (None, _REASON_NO_MACHINE_RE)
        ]
        total_hours = sum(s.duration_hours for s in with_hours)
        total_machine_re = sum(s.machine_re_count for s in sessions)
        total_amplitude = sum(s.amplitude_rera_count for s in sessions)
        total_proxy = sum(s.proxy_rera_count for s in sessions)

        def _pooled(total: int) -> float | None:
            return total / total_hours if total_hours > 0 else None

        def _floor(machine_re: int, hours: float) -> float | None:
            if hours <= 0:
                return None
            per_second = machine_re / (hours * 3600.0)
            return per_second * 2 * EVENT_MATCH_TOLERANCE_SECONDS

        # Whole-dataset floor (density context): machine-RE rate over every
        # evaluated therapy hour, most of which carry zero RE.
        chance_floor = _floor(total_machine_re, total_hours)

        # Scored-population floor: machine-RE rate over scored-session hours
        # only, whose RE density far exceeds the dataset average.  This is the
        # honest baseline for the precision/sensitivity reported beside it, which
        # likewise cover only scored sessions.
        scored_hours = sum(s.duration_hours for s in scored if s.duration_hours > 0)
        scored_machine_re = sum(s.machine_re_count for s in scored)
        scored_chance_floor = _floor(scored_machine_re, scored_hours)

        def _pooled_ratio(matched_attr: str, denominator: int) -> float | None:
            matched = sum(getattr(s, matched_attr) or 0 for s in scored)
            return matched / denominator if denominator else None

        scored_amplitude = sum(s.amplitude_rera_count for s in scored)
        scored_proxy = sum(s.proxy_rera_count for s in scored)

        def _mean(attr: str) -> float | None:
            vals = [v for s in scored if (v := getattr(s, attr)) is not None]
            return mean_or_none(vals)

        return ReraAggregateMetrics(
            total_sessions=len(sessions),
            sessions_with_machine_re=len(scored),
            sessions_skipped_no_machine_re=_count_skip(_REASON_NO_MACHINE_RE),
            sessions_skipped_no_analysis=_count_skip("no_analysis"),
            sessions_skipped_no_valid_breaths=_count_skip("no_valid_breaths"),
            sessions_skipped_error=_count_skip("error"),
            total_machine_re=total_machine_re,
            total_amplitude_reras=total_amplitude,
            total_proxy_reras=total_proxy,
            machine_re_density=_pooled(total_machine_re),
            amplitude_density=_pooled(total_amplitude),
            proxy_density=_pooled(total_proxy),
            match_tolerance_seconds=EVENT_MATCH_TOLERANCE_SECONDS,
            chance_precision_floor=chance_floor,
            scored_chance_precision_floor=scored_chance_floor,
            mean_amplitude_sensitivity=_mean("amplitude_sensitivity"),
            mean_amplitude_precision=_mean("amplitude_precision"),
            mean_amplitude_f1=_mean("amplitude_f1"),
            mean_proxy_sensitivity=_mean("proxy_sensitivity"),
            mean_proxy_precision=_mean("proxy_precision"),
            mean_proxy_f1=_mean("proxy_f1"),
            pooled_amplitude_sensitivity=_pooled_ratio(
                "amplitude_matched", scored_machine_re
            ),
            pooled_amplitude_precision=_pooled_ratio(
                "amplitude_matched", scored_amplitude
            ),
            pooled_proxy_sensitivity=_pooled_ratio("proxy_matched", scored_machine_re),
            pooled_proxy_precision=_pooled_ratio("proxy_matched", scored_proxy),
        )
