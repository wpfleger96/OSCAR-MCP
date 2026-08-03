"""Trigger/cycle inference heuristic module.

Experimental heuristic for inferring whether ventilator trigger and cycle
events are normal, premature, or delayed, based on inspiratory flow
morphology.

IMPORTANT: This heuristic is tuned on ResMed flow waveforms. Other vendors
receive ``applicability=UNVALIDATED_DEVICE`` with null confidence/type
values (plan step 4 / G2 capability-honest).

Algorithm versioning
--------------------
Constants in ``versioning.py`` stamp each row so that bumping any threshold
marks older rows STALE_VERSION on re-analysis.

Trigger heuristics (v1)
-----------------------
- PREMATURE: breath started very early relative to its predecessor; Ti is
  unusually short (< MIN_TI_PREMATURE_TRIGGER_S) or expiratory flow was
  still significantly positive at breath start (expiratory carryover proxy
  not available from segmented per-breath data, so Ti-only heuristic used).
- DELAYED: breath started unusually late (next_gap > DELAYED_TRIGGER_GAP_S).
- NORMAL: neither condition.

Cycle heuristics (v1)
---------------------
- PREMATURE: flow at end-of-inspiration was above
  PREMATURE_CYCLE_FLOW_FRACTION * peak_insp_flow, suggesting the device
  terminated inspiration while flow was still significant.
- NORMAL: flow had fallen well below the fraction by cycle.

Both heuristics produce a confidence score (0–1).  Confidence is LOW for
heuristic data (no ground-truth EEG or pressure-effort signal).

Applicability
-------------
The caller must pass ``vendor_applicability`` as ``VALIDATED`` for ResMed
devices or ``UNVALIDATED_DEVICE`` for all others.  When applicability is
``UNVALIDATED_DEVICE`` the function returns all-null inference fields with
the appropriate reason code.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Versioned thresholds (bumping these triggers STALE_VERSION on re-analysis)
# ---------------------------------------------------------------------------

# Trigger heuristics
MIN_TI_PREMATURE_TRIGGER_S: float = 0.5
"""Ti below this threshold flags a potentially premature trigger (seconds)."""

DELAYED_TRIGGER_GAP_S: float = 4.0
"""Inter-breath gap above this flags a delayed trigger (seconds)."""

# Cycle heuristics
PREMATURE_CYCLE_FLOW_FRACTION: float = 0.25
"""Fraction of peak insp flow; if end-insp flow exceeds this, cycle is premature."""

# Confidence scaling
# Heuristic data carries low ceiling confidence — no EEG, no pressure signal.
MAX_TRIGGER_CONFIDENCE: float = 0.65
MAX_CYCLE_CONFIDENCE: float = 0.60

# Vendor applicability constants — the caller supplies one of these strings.
# ``BreathService`` (PR-A §13) re-exports these; they live here to avoid
# importing from the services layer inside the analysis layer.
APPLICABILITY_VALIDATED: str = "validated"
APPLICABILITY_UNVALIDATED_DEVICE: str = "unvalidated_device"


# ---------------------------------------------------------------------------
# Result types (plain dataclasses — no DB; persisted as individual columns)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerInference:
    """Inferred trigger classification for one breath."""

    trigger_type: str | None  # "normal" | "premature" | "delayed" | None
    trigger_confidence: float | None  # 0–1; None when not applicable
    trigger_cycle_applicable: bool  # False → all inference fields are null
    trigger_cycle_reason: str | None  # NullReason code when not applicable


@dataclass(frozen=True)
class CycleInference:
    """Inferred cycle classification for one breath."""

    cycle_type: str | None  # "normal" | "premature" | None
    cycle_confidence: float | None  # 0–1; None when not applicable


@dataclass(frozen=True)
class TriggerCycleResult:
    """Combined trigger + cycle inference for one breath."""

    inferred_trigger_type: str | None
    trigger_confidence: float | None
    inferred_cycle_type: str | None
    cycle_confidence: float | None
    trigger_cycle_applicable: bool
    trigger_cycle_reason: str | None  # NullReason code


# ---------------------------------------------------------------------------
# Inference functions
# ---------------------------------------------------------------------------


def infer_trigger_cycle(
    *,
    inspiration_time_s: float | None,
    gap_before_s: float | None,
    insp_flow_array: np.ndarray | None,
    vendor_applicability: str,  # TriggerCycleApplicability value
) -> TriggerCycleResult:
    """Infer trigger and cycle types for one breath.

    Args:
        inspiration_time_s: Duration of the inspiratory phase (Ti), seconds.
            None → trigger/cycle both null.
        gap_before_s: Time gap between the previous breath's end and this
            breath's start (Te of previous breath), seconds.  None → trigger
            inference only uses Ti.
        insp_flow_array: NumPy array of inspiratory flow values (positive,
            L/min), time-ordered.  None or empty → cycle inference null.
        vendor_applicability: ``"validated"`` or ``"unvalidated_device"``.
            Non-validated vendors receive null inference.

    Returns:
        ``TriggerCycleResult`` with all fields populated.
    """
    if vendor_applicability == APPLICABILITY_UNVALIDATED_DEVICE:
        return TriggerCycleResult(
            inferred_trigger_type=None,
            trigger_confidence=None,
            inferred_cycle_type=None,
            cycle_confidence=None,
            trigger_cycle_applicable=False,
            trigger_cycle_reason="unvalidated_device",
        )

    trigger = _infer_trigger(inspiration_time_s, gap_before_s)
    cycle = _infer_cycle(insp_flow_array)

    return TriggerCycleResult(
        inferred_trigger_type=trigger.trigger_type,
        trigger_confidence=trigger.trigger_confidence,
        inferred_cycle_type=cycle.cycle_type,
        cycle_confidence=cycle.cycle_confidence,
        trigger_cycle_applicable=True,
        trigger_cycle_reason=None,
    )


def _infer_trigger(
    inspiration_time_s: float | None,
    gap_before_s: float | None,
) -> TriggerInference:
    """Internal trigger heuristic (v1)."""
    if inspiration_time_s is None:
        return TriggerInference(
            trigger_type=None,
            trigger_confidence=None,
            trigger_cycle_applicable=True,
            trigger_cycle_reason=None,
        )

    # Premature trigger: very short Ti
    if inspiration_time_s < MIN_TI_PREMATURE_TRIGGER_S:
        # Confidence scales from 0.65 at Ti=0 to 0.35 at the threshold
        ratio = inspiration_time_s / MIN_TI_PREMATURE_TRIGGER_S
        confidence = MAX_TRIGGER_CONFIDENCE * (1.0 - ratio * 0.5)
        return TriggerInference(
            trigger_type="premature",
            trigger_confidence=round(float(np.clip(confidence, 0.0, 1.0)), 3),
            trigger_cycle_applicable=True,
            trigger_cycle_reason=None,
        )

    # Delayed trigger: large gap since last breath
    if gap_before_s is not None and gap_before_s > DELAYED_TRIGGER_GAP_S:
        ratio = min(gap_before_s / (DELAYED_TRIGGER_GAP_S * 2), 1.0)
        confidence = MAX_TRIGGER_CONFIDENCE * ratio
        return TriggerInference(
            trigger_type="delayed",
            trigger_confidence=round(float(np.clip(confidence, 0.0, 1.0)), 3),
            trigger_cycle_applicable=True,
            trigger_cycle_reason=None,
        )

    return TriggerInference(
        trigger_type="normal",
        trigger_confidence=0.5,  # heuristic ceiling on normal
        trigger_cycle_applicable=True,
        trigger_cycle_reason=None,
    )


def _infer_cycle(
    insp_flow_array: np.ndarray | None,
) -> CycleInference:
    """Internal cycle heuristic (v1).

    Premature if the last ~10% of inspiration still shows high flow.
    """
    if insp_flow_array is None or len(insp_flow_array) < 5:
        return CycleInference(cycle_type=None, cycle_confidence=None)

    peak_flow = float(np.max(insp_flow_array))
    if peak_flow <= 0:
        return CycleInference(cycle_type=None, cycle_confidence=None)

    # Look at the last 10% of the inspiration window
    tail_start = max(0, int(len(insp_flow_array) * 0.90))
    tail_flow = insp_flow_array[tail_start:]
    if len(tail_flow) == 0:
        return CycleInference(cycle_type=None, cycle_confidence=None)

    tail_mean = float(np.mean(tail_flow))
    tail_fraction = tail_mean / peak_flow

    if tail_fraction > PREMATURE_CYCLE_FLOW_FRACTION:
        # Confidence scales with how far above the threshold
        excess = (tail_fraction - PREMATURE_CYCLE_FLOW_FRACTION) / (
            1.0 - PREMATURE_CYCLE_FLOW_FRACTION
        )
        confidence = MAX_CYCLE_CONFIDENCE * float(np.clip(excess, 0.0, 1.0))
        return CycleInference(
            cycle_type="premature",
            cycle_confidence=round(confidence, 3),
        )

    return CycleInference(cycle_type="normal", cycle_confidence=0.45)
