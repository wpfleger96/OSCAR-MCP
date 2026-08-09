"""Analysis pipeline type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from snore.analysis.modes.types import ModeResult


class AnalysisEvent(BaseModel):
    """
    Respiratory event structure for analysis processing.

    Note: This is distinct from models.unified.RespiratoryEvent which is the
    canonical storage format. AnalysisEvent uses float timestamps for performance
    and includes analysis-specific metadata (source, confidence).
    """

    event_type: str = Field(description="Event type")
    start_time: float = Field(description="Session offset (seconds from session start)")
    duration: float = Field(ge=0, description="Event duration (seconds)")
    source: str = Field(description="Event source (machine/programmatic)")
    confidence: float | None = Field(
        default=None, ge=0, le=1, description="Detection confidence"
    )
    flow_reduction: float | None = Field(
        default=None, ge=0, le=1, description="Flow reduction (0-1)"
    )
    has_desaturation: bool | None = Field(
        default=None, description="Has SpO2 desaturation"
    )
    baseline_flow: float | None = Field(
        default=None, description="Baseline flow (L/min)"
    )


class AnalysisResult(BaseModel):
    """Results from session analysis."""

    session_id: int = Field(description="Database session ID")
    session_duration_hours: float = Field(ge=0, description="Session duration (hours)")
    total_breaths: int = Field(ge=0, description="Total breaths segmented")
    machine_events: list[AnalysisEvent] = Field(description="Machine-flagged events")
    mode_results: dict[str, ModeResult] = Field(description="Results by detection mode")
    flow_analysis: dict[str, Any] | None = Field(
        default=None, description="Flow limitation analysis"
    )
    csr_detection: dict[str, Any] | None = Field(
        default=None, description="Cheyne-Stokes Respiration detection (summary)"
    )
    periodic_breathing: dict[str, Any] | None = Field(
        default=None, description="Periodic breathing detection (summary)"
    )
    csr_episodes: list[dict[str, Any]] | None = Field(
        default=None, description="Time-localized CSR episodes"
    )
    periodic_breathing_episodes: list[dict[str, Any]] | None = Field(
        default=None, description="Time-localized periodic breathing episodes"
    )
    pulse_change_count: int | None = Field(
        default=None, description="Total pulse change events detected"
    )
    pulse_change_index: float | None = Field(
        default=None, description="Pulse changes per hour"
    )
    timestamp_start: float = Field(default=0.0, description="Session start timestamp")
    timestamp_end: float = Field(default=0.0, description="Session end timestamp")


# ---------------------------------------------------------------------------
# Per-breath compute envelope
# ---------------------------------------------------------------------------


@dataclass
class ComputedBreath:
    """Per-breath derived fields produced during compute_analysis.

    This is the private compute-layer representation — it NEVER enters the
    public AnalysisResult DTO or programmatic_result_json.  It is persisted
    as a models.Breath row via AnalysisComputation.
    """

    breath_number: int
    start_offset_s: float
    end_offset_s: float

    # Timing
    inspiration_time_s: float | None
    expiration_time_s: float | None
    total_time_s: float | None
    i_e_ratio: float | None
    duty_cycle: float | None

    # Amplitude
    peak_flow_lpm: float | None  # peak inspiratory flow L/min
    peak_exp_flow_lpm: float | None  # peak expiratory flow L/min
    tidal_volume_ml: float | None
    respiratory_rate_rolling: float | None

    # Flow shape
    flatness_index: float | None  # time-above-80%-peak
    mid_insp_flattening: float | None  # mid-insp flow ÷ peak (new)

    # Flow classification
    flow_class: int | None
    flow_confidence: float | None

    # Recovery flag (from primary mode's RERA detector)
    is_recovery_breath: bool | None

    # Trigger/cycle (experimental)
    inferred_trigger_type: str | None
    trigger_confidence: float | None
    inferred_cycle_type: str | None
    cycle_confidence: float | None
    trigger_cycle_applicable: bool | None
    trigger_cycle_reason: str | None

    # Quality flags
    leak_valid: bool | None
    leak_valid_reason: str | None
    ramp_active: bool | None
    ramp_active_reason: str | None
    mask_off: bool | None
    mask_off_reason: str | None


@dataclass
class AnalysisComputation:
    """Private compute envelope returned by compute_analysis().

    Contains both the public AnalysisResult summary (stored in
    programmatic_result_json) and the per-breath list (stored as models.Breath
    children of the AnalysisResult row).  Breaths NEVER enter the public DTO.
    """

    summary: AnalysisResult
    breaths: list[ComputedBreath] = field(default_factory=list)
    primary_mode: str = "aasm"
