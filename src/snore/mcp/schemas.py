"""Pydantic response schemas for SNORE MCP tools.

All date/time fields use ISO 8601 strings with explicit UTC offset.
All measurement fields carry their unit as a sibling ``_unit`` field or are
documented in the tool docstring.  Absent data is ``null`` with a companion
``_reason`` field (e.g. ``rera_index: null, rera_index_reason: "analysis_not_run"``).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class DeviceCapabilities(BaseModel):
    """Capabilities declared by the device/dataset for a queried range (G2)."""

    model_config = ConfigDict(populate_by_name=True)

    manufacturer: str
    model: str
    serial_number: str
    has_flow_waveform: bool
    has_pressure_waveform: bool
    has_leak_waveform: bool
    has_spo2: bool
    has_events: bool
    has_analysis: bool
    notes: list[str] = []


class DeviceInfo(BaseModel):
    """Summary of a single device."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    manufacturer: str
    model: str
    serial_number: str
    first_session_date: date | None = None
    last_session_date: date | None = None
    session_count: int = 0
    therapy_modes: list[str] = []


class DataOverviewResponse(BaseModel):
    """Response from get_data_overview."""

    model_config = ConfigDict(populate_by_name=True)

    devices: list[DeviceInfo]
    date_range_start: date | None = None
    date_range_end: date | None = None
    total_sessions: int = 0
    available_waveform_channels: list[str] = []
    available_event_types: list[str] = []
    analysis_run: bool = False
    analysis_session_count: int = 0


class SettingsEpoch(BaseModel):
    """A contiguous period of stable therapy settings."""

    model_config = ConfigDict(populate_by_name=True)

    start_date: date
    end_date: date
    nights: int
    settings: dict[str, str | None]
    changed_keys: list[str] = []
    device_id: int
    device_capabilities: DeviceCapabilities | None = None


class SettingsTimelineResponse(BaseModel):
    """Response from get_settings_timeline."""

    model_config = ConfigDict(populate_by_name=True)

    epochs: list[SettingsEpoch]
    total_epochs: int


class NightlyRow(BaseModel):
    """Per-night summary row returned by get_nightly_summary."""

    model_config = ConfigDict(populate_by_name=True)

    date: date
    usage_hours: float | None = None
    session_count: int = 0

    # AHI components (events/hr) — null + reason when absent
    ahi: float | None = None
    oai: float | None = None
    cai: float | None = None
    hi: float | None = None

    # Analysis-derived indices — null when analysis has not been run
    rera_index: float | None = None
    rera_index_reason: str | None = None
    rdi: float | None = None
    rdi_reason: str | None = None

    # Pressure percentiles (cmH₂O)
    pressure_median_cmh2o: float | None = None
    pressure_95th_cmh2o: float | None = None
    epap_median_cmh2o: float | None = None

    # Leak (L/min)
    leak_median_lpm: float | None = None
    leak_95th_lpm: float | None = None
    leak_above_24_pct: float | None = None

    # Resp physiology
    rr_mean_bpm: float | None = None
    tv_mean_ml: float | None = None
    mv_mean_lpm: float | None = None

    # SpO₂ (%)
    spo2_mean_pct: float | None = None

    device_id: int | None = None


class ComplianceFields(BaseModel):
    """Compliance summary appended to range-mode nightly summary."""

    model_config = ConfigDict(populate_by_name=True)

    threshold_hours: float
    days_compliant: int
    days_total: int
    compliance_pct: float


class NightlySummaryResponse(BaseModel):
    """Response from get_nightly_summary."""

    model_config = ConfigDict(populate_by_name=True)

    nights: list[NightlyRow]
    total_nights: int
    page: int
    page_size: int
    # Compliance block only present in range mode
    compliance: ComplianceFields | None = None
    device_capabilities: DeviceCapabilities | None = None


class EventContext(BaseModel):
    """Per-event contextual snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    pressure_at_event_cmh2o: float | None = None
    leak_at_event_lpm: float | None = None
    mv_prior_120s_lpm: float | None = None
    minutes_since_session_start: float | None = None


class EventRow(BaseModel):
    """A single respiratory event with inline context."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    event_type: str
    start_time_iso: str
    duration_seconds: float | None = None
    spo2_drop_pct: float | None = None
    peak_flow_limitation: float | None = None
    context: EventContext | None = None


class EventsResponse(BaseModel):
    """Response from get_events."""

    model_config = ConfigDict(populate_by_name=True)

    date: str
    session_id: int
    events: list[EventRow]
    total_events: int
    device_capabilities: DeviceCapabilities | None = None


class CapabilityEntry(BaseModel):
    """One entry in the capabilities resource."""

    model_config = ConfigDict(populate_by_name=True)

    channel: str
    description: str
    unit: str | None = None
    present_in_dataset: bool
    sample_rate_hz: float | None = None


# Mapping used for docs://schemas/{type} — maps schema name to model class
SCHEMA_MODEL_MAP: dict[str, type[BaseModel]] = {
    "device_capabilities": DeviceCapabilities,
    "device_info": DeviceInfo,
    "data_overview": DataOverviewResponse,
    "settings_epoch": SettingsEpoch,
    "settings_timeline": SettingsTimelineResponse,
    "nightly_row": NightlyRow,
    "compliance_fields": ComplianceFields,
    "nightly_summary": NightlySummaryResponse,
    "event_context": EventContext,
    "event_row": EventRow,
    "events_response": EventsResponse,
    "capability_entry": CapabilityEntry,
}


def model_to_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the JSON schema for a Pydantic model."""
    return model.model_json_schema()
