"""Offline threshold-sweep harness for FL/RERA tuning.

This module measures; it does not tune.  It loads stored data **once** into
in-memory arrays, then re-scores a Cartesian grid of *query-time* tunables by
driving the pure validation seams (``score_fl_arrays``,
``proxy_reras_from_breath_arrays`` / ``score_rera_definition``,
``correlate_night_pairs``) — no DB access inside the grid loop, no writes, no
jobs, no UI.  It exists so future FL/RERA tuning is data-driven.

Three reference targets, one objective each:

- ``flg``   — mean AUC of ``flattening_severity`` vs the device FLG signal at the
              low breakpoint (AUC at the high breakpoint reported alongside).
- ``re``    — pooled FL-run-proxy sensitivity vs machine RE, ranked *above* the
              chance-precision floor (floor = pooled machine-RE-per-second × 2 ×
              tolerance, the same figure ``rera_report`` reports).
- ``apple`` — Spearman rho of the nightly FL-run-proxy RERA index vs Apple
              sleeping-breathing-disturbances (an independent second axis).

Every grid row also reports resulting event counts / densities so degenerate
configurations (a threshold so low everything fires) are visible.

**Not sweepable here.**  Classifier-internal cutoffs and the flattening
computation itself are baked into the *stored* breaths at analysis time.
Changing those requires a version-bumped re-analysis pass, not a query-time
sweep — see ``NOT_SWEEPABLE_NOTICE``.
"""

from __future__ import annotations

import csv
import itertools
import logging

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snore.analysis.data.waveform_loader import deserialize_waveform_blob
from snore.analysis.modes.postprocess import EVENT_MATCH_TOLERANCE_SECONDS
from snore.analysis.shared.versioning import AnalysisStatus
from snore.analysis.utils import convert_machine_reras
from snore.constants import FLOW_LIMITATION_CLASSES, RERAProxyConstants
from snore.constants import FlowLimitationConstants as FLC
from snore.database import models
from snore.database.day_manager import DayManager
from snore.services.breath_service import BreathService
from snore.services.health_service import HealthService
from snore.validation.alignment import average_waveform_over_breaths
from snore.validation.apple_cross_report import correlate_night_pairs
from snore.validation.fl_validator import (
    FLG_AUC_HIGH_THRESHOLD_DEFAULT,
    FLG_AUC_LOW_THRESHOLD_DEFAULT,
    score_fl_arrays,
)
from snore.validation.rera_validator import (
    proxy_reras_from_breath_arrays,
    score_rera_definition,
)

logger = logging.getLogger(__name__)

_FLG_WAVEFORM_TYPE = "fl"

TARGET_FLG = "flg"
TARGET_RE = "re"
TARGET_APPLE = "apple"
TARGETS = (TARGET_FLG, TARGET_RE, TARGET_APPLE)

NOT_SWEEPABLE_NOTICE = (
    "Not swept: classifier-internal cutoffs and the mid-insp flattening "
    "computation are baked into the stored breaths at analysis time. Changing "
    "them requires a version-bumped re-analysis pass, not this query-time sweep."
)

# Current-default knob values, sourced from the authoritative constants so the
# highlighted "defaults" row can never drift from production behaviour.
DEFAULT_KNOBS: dict[str, dict[str, float]] = {
    TARGET_FLG: {
        "flg_low_threshold": FLG_AUC_LOW_THRESHOLD_DEFAULT,
        "flg_high_threshold": FLG_AUC_HIGH_THRESHOLD_DEFAULT,
    },
    TARGET_RE: {
        "fl_class_threshold": float(RERAProxyConstants.FL_CLASS_THRESHOLD),
        "min_fl_run_length": float(RERAProxyConstants.MIN_FL_RUN_LENGTH),
        "recovery_amplitude_margin": RERAProxyConstants.RECOVERY_AMPLITUDE_MARGIN,
    },
}
DEFAULT_KNOBS[TARGET_APPLE] = dict(DEFAULT_KNOBS[TARGET_RE])

# Default value lists per knob — each brackets its current constant so the sweep
# explores both directions around production defaults.
_PROXY_GRID: dict[str, list[float]] = {
    "fl_class_threshold": [3, 4, 5],
    "min_fl_run_length": [1, 2, 3],
    "recovery_amplitude_margin": [0.10, 0.20, 0.30],
}
DEFAULT_GRIDS: dict[str, dict[str, list[float]]] = {
    TARGET_FLG: {
        "flg_low_threshold": [0.15, 0.20, 0.25, 0.30, 0.35],
        "flg_high_threshold": [0.40, 0.50, 0.60],
    },
    TARGET_RE: dict(_PROXY_GRID),
    TARGET_APPLE: dict(_PROXY_GRID),
}


# ---------------------------------------------------------------------------
# Cached, load-once data structures (one per target)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlgSessionArrays:
    """Aligned per-breath FL arrays for one session (FLG target)."""

    session_id: int
    mid_insp_flattening: np.ndarray
    flatness_index: np.ndarray
    class_weight: np.ndarray
    rule_matched: np.ndarray
    breath_flg: np.ndarray
    session_flg_values: np.ndarray


@dataclass(frozen=True)
class ProxySessionArrays:
    """Proxy breath arrays + reference data for one session (RE / Apple target)."""

    session_id: int
    therapy_date: date
    duration_hours: float
    flow_class: list[int | None]
    is_recovery_breath: list[bool | None]
    peak_flow_lpm: list[float | None]
    start_offset_s: list[float]
    machine_starts: list[float]


@dataclass(frozen=True)
class SweepData:
    """Cached inputs for one sweep target — filled by the load-once loaders."""

    target: str
    flg_sessions: list[FlgSessionArrays] = field(default_factory=list)
    proxy_sessions: list[ProxySessionArrays] = field(default_factory=list)
    apple_bd_by_night: dict[date, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Result structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepRow:
    """One evaluated grid point: its knobs, ranking objective, and metrics."""

    knobs: dict[str, float]
    objective: float | None
    metrics: dict[str, float | int | None]
    is_default: bool


@dataclass(frozen=True)
class SweepResult:
    """A ranked sweep: best-first rows plus display/export metadata."""

    target: str
    rows: list[SweepRow]
    objective_label: str
    metric_columns: list[str]
    unit_label: str
    n_units_loaded: int
    reference: dict[str, float | int | None]
    notice: str = NOT_SWEEPABLE_NOTICE


# ---------------------------------------------------------------------------
# Grid enumeration
# ---------------------------------------------------------------------------


def enumerate_grid(grid: dict[str, list[float]]) -> list[dict[str, float]]:
    """Cartesian product of the per-knob value lists, as knob→value dicts.

    Keys keep the ``grid`` insertion order; an empty grid yields one empty combo.
    """
    keys = list(grid.keys())
    if not keys:
        return [{}]
    return [
        dict(zip(keys, combo, strict=True))
        for combo in itertools.product(*grid.values())
    ]


def _is_default(knobs: dict[str, float], target: str) -> bool:
    defaults = DEFAULT_KNOBS[target]
    return all(
        key in knobs and float(knobs[key]) == float(val)
        for key, val in defaults.items()
    )


def _rank(rows: list[SweepRow]) -> list[SweepRow]:
    """Sort best-first: higher objective wins; ``None`` objectives sink last."""
    return sorted(
        rows,
        key=lambda r: (
            r.objective is not None,
            r.objective if r.objective is not None else 0.0,
        ),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Pure per-target scorers (no DB) — driven over the cached arrays
# ---------------------------------------------------------------------------


def _score_flg_grid(
    sessions: Sequence[FlgSessionArrays], grid: dict[str, list[float]]
) -> tuple[list[SweepRow], list[str], str]:
    metric_columns = [
        "mean_auc_low",
        "mean_auc_high",
        "n_sessions_scored",
        "pos_rate_low",
        "total_breaths",
    ]
    rows: list[SweepRow] = []
    for knobs in enumerate_grid(grid):
        low = float(knobs["flg_low_threshold"])
        high = float(knobs["flg_high_threshold"])
        auc_low: list[float] = []
        auc_high: list[float] = []
        n_pos = 0
        n_total = 0
        for s in sessions:
            scores = score_fl_arrays(
                s.mid_insp_flattening,
                s.flatness_index,
                s.class_weight,
                s.rule_matched,
                s.breath_flg,
                s.session_flg_values,
                flg_low_threshold=low,
                flg_high_threshold=high,
            )
            valid = s.breath_flg[~np.isnan(s.breath_flg)]
            n_total += int(valid.size)
            n_pos += int(np.count_nonzero(valid >= low))
            if scores is None:
                continue
            if scores.auc_t25 is not None:
                auc_low.append(scores.auc_t25)
            if scores.auc_t50 is not None:
                auc_high.append(scores.auc_t50)
        objective = float(np.mean(auc_low)) if auc_low else None
        rows.append(
            SweepRow(
                knobs=knobs,
                objective=objective,
                metrics={
                    "mean_auc_low": objective,
                    "mean_auc_high": float(np.mean(auc_high)) if auc_high else None,
                    "n_sessions_scored": len(auc_low),
                    "pos_rate_low": (n_pos / n_total) if n_total else None,
                    "total_breaths": n_total,
                },
                is_default=_is_default(knobs, TARGET_FLG),
            )
        )
    return _rank(rows), metric_columns, "mean AUC @ FLG >= low"


def _proxy_starts(s: ProxySessionArrays, knobs: dict[str, float]) -> list[float]:
    return proxy_reras_from_breath_arrays(
        s.flow_class,
        s.is_recovery_breath,
        s.peak_flow_lpm,
        s.start_offset_s,
        fl_class_threshold=int(knobs["fl_class_threshold"]),
        min_fl_run_length=int(knobs["min_fl_run_length"]),
        recovery_amplitude_margin=float(knobs["recovery_amplitude_margin"]),
    )


def _chance_precision_floor(sessions: Sequence[ProxySessionArrays]) -> float | None:
    """Pooled machine-RE-per-second × 2 × tolerance — matches ``rera_report``."""
    total_hours = sum(s.duration_hours for s in sessions if s.duration_hours > 0)
    if total_hours <= 0:
        return None
    total_machine = sum(len(s.machine_starts) for s in sessions)
    machine_re_per_second = total_machine / (total_hours * 3600.0)
    return machine_re_per_second * 2 * EVENT_MATCH_TOLERANCE_SECONDS


def _score_re_grid(
    sessions: Sequence[ProxySessionArrays], grid: dict[str, list[float]]
) -> tuple[list[SweepRow], list[str], str, dict[str, float | int | None]]:
    metric_columns = [
        "sensitivity",
        "precision",
        "sens_above_floor",
        "total_proxy",
        "proxy_density_per_h",
    ]
    floor = _chance_precision_floor(sessions)
    total_machine = sum(len(s.machine_starts) for s in sessions)
    total_hours = sum(s.duration_hours for s in sessions if s.duration_hours > 0)
    reference: dict[str, float | int | None] = {
        "chance_precision_floor": floor,
        "total_machine_re": total_machine,
        "match_tolerance_seconds": EVENT_MATCH_TOLERANCE_SECONDS,
    }

    rows: list[SweepRow] = []
    for knobs in enumerate_grid(grid):
        matched = 0
        scored_prog = 0  # proxy events on sessions carrying machine RE (precision)
        total_proxy = 0  # proxy events everywhere (firing density)
        for s in sessions:
            starts = _proxy_starts(s, knobs)
            total_proxy += len(starts)
            if s.machine_starts:
                score = score_rera_definition(starts, s.machine_starts)
                matched += score.matched
                scored_prog += score.programmatic_count
        sensitivity = matched / total_machine if total_machine else None
        precision = matched / scored_prog if scored_prog else None
        proxy_density = total_proxy / total_hours if total_hours > 0 else None
        objective = (
            sensitivity - floor
            if sensitivity is not None and floor is not None
            else sensitivity
        )
        rows.append(
            SweepRow(
                knobs=knobs,
                objective=objective,
                metrics={
                    "sensitivity": sensitivity,
                    "precision": precision,
                    "sens_above_floor": objective,
                    "total_proxy": total_proxy,
                    "proxy_density_per_h": proxy_density,
                },
                is_default=_is_default(knobs, TARGET_RE),
            )
        )
    return _rank(rows), metric_columns, "proxy sensitivity − chance floor", reference


def _score_apple_grid(
    sessions: Sequence[ProxySessionArrays],
    apple_bd_by_night: dict[date, float],
    grid: dict[str, list[float]],
) -> tuple[list[SweepRow], list[str], str, dict[str, float | int | None]]:
    metric_columns = [
        "rho",
        "p_value",
        "n_paired_nights",
        "total_proxy",
        "mean_nightly_index",
    ]
    reference: dict[str, float | int | None] = {
        "n_apple_bd_nights": len(apple_bd_by_night),
    }

    rows: list[SweepRow] = []
    for knobs in enumerate_grid(grid):
        night_proxy: dict[date, int] = {}
        night_hours: dict[date, float] = {}
        total_proxy = 0
        for s in sessions:
            count = len(_proxy_starts(s, knobs))
            total_proxy += count
            night_proxy[s.therapy_date] = night_proxy.get(s.therapy_date, 0) + count
            night_hours[s.therapy_date] = (
                night_hours.get(s.therapy_date, 0.0) + s.duration_hours
            )
        nightly_index = {
            d: night_proxy[d] / night_hours[d]
            for d in night_proxy
            if night_hours[d] > 0
        }
        corr = correlate_night_pairs(nightly_index, apple_bd_by_night)
        mean_index = (
            float(np.mean(list(nightly_index.values()))) if nightly_index else None
        )
        rows.append(
            SweepRow(
                knobs=knobs,
                objective=corr.rho,
                metrics={
                    "rho": corr.rho,
                    "p_value": corr.p_value,
                    "n_paired_nights": corr.n_paired_nights,
                    "total_proxy": total_proxy,
                    "mean_nightly_index": mean_index,
                },
                is_default=_is_default(knobs, TARGET_APPLE),
            )
        )
    return _rank(rows), metric_columns, "Spearman rho vs Apple BD", reference


# ---------------------------------------------------------------------------
# Public pure entry point
# ---------------------------------------------------------------------------


def evaluate_grid(
    data: SweepData, grid: dict[str, list[float]] | None = None
) -> SweepResult:
    """Re-score the cached data across the grid and rank the results (pure).

    ``grid`` defaults to ``DEFAULT_GRIDS[data.target]``.  No DB access — this is
    the harness's measurement core and the direct unit-test seam.
    """
    target = data.target
    if target not in TARGETS:
        raise ValueError(f"unknown sweep target: {target!r}")
    grid = grid if grid is not None else DEFAULT_GRIDS[target]

    if target == TARGET_FLG:
        rows, cols, obj_label = _score_flg_grid(data.flg_sessions, grid)
        return SweepResult(
            target=target,
            rows=rows,
            objective_label=obj_label,
            metric_columns=cols,
            unit_label="sessions",
            n_units_loaded=len(data.flg_sessions),
            reference={},
        )
    if target == TARGET_RE:
        rows, cols, obj_label, reference = _score_re_grid(data.proxy_sessions, grid)
        return SweepResult(
            target=target,
            rows=rows,
            objective_label=obj_label,
            metric_columns=cols,
            unit_label="sessions",
            n_units_loaded=len(data.proxy_sessions),
            reference=reference,
        )
    rows, cols, obj_label, reference = _score_apple_grid(
        data.proxy_sessions, data.apple_bd_by_night, grid
    )
    n_nights = len({s.therapy_date for s in data.proxy_sessions})
    return SweepResult(
        target=target,
        rows=rows,
        objective_label=obj_label,
        metric_columns=cols,
        unit_label="nights",
        n_units_loaded=n_nights,
        reference=reference,
    )


# ---------------------------------------------------------------------------
# Load-once loaders (the only DB-touching code; never invoked in the grid loop)
# ---------------------------------------------------------------------------


async def _range_sessions(
    db: AsyncSession, profile_id: int, date_from: str, date_to: str
) -> list[models.Session]:
    """Profile-scoped sessions in [date_from, date_to], ordered by start time."""
    stmt = (
        select(models.Session)
        .join(models.Device, models.Session.device_id == models.Device.id)
        .where(
            models.Device.profile_id == profile_id,
            models.Session.start_time >= datetime.fromisoformat(date_from),
            models.Session.start_time <= datetime.fromisoformat(f"{date_to} 23:59:59"),
        )
        .order_by(models.Session.start_time)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _load_flg_session(
    db: AsyncSession, breath_svc: BreathService, session: models.Session
) -> FlgSessionArrays | None:
    """Build one session's aligned FL arrays, or None if it cannot be scored."""
    waveform_row = (
        (
            await db.execute(
                select(models.Waveform).filter_by(
                    session_id=session.id, waveform_type=_FLG_WAVEFORM_TYPE
                )
            )
        )
        .scalars()
        .first()
    )
    if waveform_row is None or waveform_row.data_blob is None:
        return None
    sample_count = waveform_row.sample_count or 0
    if sample_count == 0:
        return None

    status, _algo, ar_id = await breath_svc.latest_analysis_for_session(session.id)
    if status != AnalysisStatus.OK or ar_id is None:
        return None

    breath_stmt = (
        select(models.Breath)
        .where(
            models.Breath.analysis_result_id == ar_id,
            models.Breath.leak_valid.is_(True),
            models.Breath.mid_insp_flattening.is_not(None),
            models.Breath.flatness_index.is_not(None),
            models.Breath.start_offset_s.is_not(None),
            models.Breath.end_offset_s.is_not(None),
        )
        .order_by(models.Breath.breath_number)
    )
    breaths = (await db.execute(breath_stmt)).scalars().all()
    if not breaths:
        return None

    flg_timestamps, flg_values = deserialize_waveform_blob(
        waveform_row.data_blob, sample_count
    )
    valid_mask = (flg_values >= 0.0) & np.isfinite(flg_values)
    flg_timestamps = flg_timestamps[valid_mask]
    flg_values = np.clip(flg_values[valid_mask], 0.0, 1.0)

    starts = np.array([b.start_offset_s for b in breaths], dtype=np.float64)
    ends = np.array([b.end_offset_s for b in breaths], dtype=np.float64)
    mid_insp = np.array([b.mid_insp_flattening for b in breaths], dtype=np.float64)
    flatness = np.array([b.flatness_index for b in breaths], dtype=np.float64)
    class_weight = np.array(
        [
            FLOW_LIMITATION_CLASSES[b.flow_class]["weight"]
            if b.flow_class in FLOW_LIMITATION_CLASSES
            else np.nan
            for b in breaths
        ],
        dtype=np.float64,
    )
    rule_matched = np.array(
        [
            b.flow_confidence is not None
            and b.flow_confidence > FLC.FL_DEFAULT_CONFIDENCE
            for b in breaths
        ],
        dtype=bool,
    )
    breath_flg = average_waveform_over_breaths(
        starts, ends, flg_timestamps.astype(np.float64), flg_values.astype(np.float64)
    )
    if np.isnan(breath_flg).all():
        return None

    return FlgSessionArrays(
        session_id=session.id,
        mid_insp_flattening=mid_insp,
        flatness_index=flatness,
        class_weight=class_weight,
        rule_matched=rule_matched,
        breath_flg=breath_flg,
        session_flg_values=flg_values,
    )


async def _load_proxy_session(
    db: AsyncSession,
    breath_svc: BreathService,
    profile_id: int,
    session: models.Session,
    *,
    with_machine_re: bool,
) -> ProxySessionArrays | None:
    """Build one session's proxy breath arrays (+ machine RE when requested)."""
    status, _algo, ar_id = await breath_svc.latest_analysis_for_session(session.id)
    if status != AnalysisStatus.OK or ar_id is None:
        return None

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
    breath_rows = (await db.execute(breath_stmt)).all()
    if not breath_rows:
        return None

    machine_starts: list[float] = []
    if with_machine_re:
        from snore.services.analysis_facade import AnalysisFacade  # noqa: PLC0415

        facade = AnalysisFacade(db, profile_id)
        analysis = await facade.get_analysis_result(session.id)
        if analysis is not None:
            machine_starts = [
                r.start_time for r in convert_machine_reras(analysis.machine_events)
            ]

    return ProxySessionArrays(
        session_id=session.id,
        therapy_date=DayManager.get_day_for_session(session.start_time),
        duration_hours=(session.duration_seconds or 0) / 3600.0,
        flow_class=[b.flow_class for b in breath_rows],
        is_recovery_breath=[b.is_recovery_breath for b in breath_rows],
        peak_flow_lpm=[b.peak_flow_lpm for b in breath_rows],
        start_offset_s=[b.start_offset_s for b in breath_rows],
        machine_starts=machine_starts,
    )


async def load_sweep_data(
    db: AsyncSession,
    profile_id: int,
    date_from: str,
    date_to: str,
    target: str,
) -> SweepData:
    """Load-once: fetch exactly the arrays ``target`` needs into memory.

    The only DB-touching entry point.  ``evaluate_grid`` then re-scores the
    returned cache with zero further DB access.
    """
    if target not in TARGETS:
        raise ValueError(f"unknown sweep target: {target!r}")

    sessions = await _range_sessions(db, profile_id, date_from, date_to)
    breath_svc = BreathService(db, profile_id)
    logger.info("sweep: loaded %d sessions for target=%s", len(sessions), target)

    if target == TARGET_FLG:
        flg = [
            flg_arrays
            for session in sessions
            if (flg_arrays := await _load_flg_session(db, breath_svc, session))
            is not None
        ]
        return SweepData(target=target, flg_sessions=flg)

    with_machine = target == TARGET_RE
    proxy = [
        proxy_arrays
        for session in sessions
        if (
            proxy_arrays := await _load_proxy_session(
                db, breath_svc, profile_id, session, with_machine_re=with_machine
            )
        )
        is not None
    ]

    apple_bd: dict[date, float] = {}
    if target == TARGET_APPLE:
        health = HealthService(db, profile_id)
        apple_bd = await health.get_breathing_disturbance_by_night(
            date.fromisoformat(date_from), date.fromisoformat(date_to)
        )

    return SweepData(target=target, proxy_sessions=proxy, apple_bd_by_night=apple_bd)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_sweep_csv(result: SweepResult, output_path: Path) -> None:
    """Write the FULL ranked grid (one row per combo) as CSV, best-first.

    Columns: each swept knob, ``rank``, ``objective``, every reported metric,
    and ``is_default``.  Numeric cells use 6 significant places; ``None`` is "".
    """
    knob_cols = list(result.rows[0].knobs.keys()) if result.rows else []
    fieldnames = (
        knob_cols + ["rank", "objective"] + result.metric_columns + ["is_default"]
    )

    def _fmt(v: float | int | None) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, row in enumerate(result.rows, start=1):
            record: dict[str, str] = {k: _fmt(row.knobs[k]) for k in knob_cols}
            record["rank"] = str(rank)
            record["objective"] = _fmt(row.objective)
            for col in result.metric_columns:
                record[col] = _fmt(row.metrics.get(col))
            record["is_default"] = "true" if row.is_default else "false"
            writer.writerow(record)
