"""OSCAR channel ID to SNORE unified model mappings."""

from snore.constants import (
    CPAP_CLEAR_AIRWAY,
    CPAP_CSR,
    CPAP_FLOW_LIMIT,
    CPAP_FLOW_RATE,
    CPAP_HYPOPNEA,
    CPAP_LEAK,
    CPAP_MASK_PRESSURE,
    CPAP_MINUTE_VENT,
    CPAP_OBSTRUCTIVE,
    CPAP_PERIODIC_BREATHING,
    CPAP_RERA,
    CPAP_RESPRATE,
    CPAP_TIDAL_VOLUME,
    OXI_PULSE,
    OXI_SPO2,
)
from snore.models.unified import RespiratoryEventType, WaveformType

__all__ = [
    "OSCAR_EVENT_TYPE_MAP",
    "OSCAR_WAVEFORM_TYPE_MAP",
    "OSCAR_WAVEFORM_UNITS",
    "OSCAR_EVENT_CHANNEL_IDS",
    "OSCAR_WAVEFORM_CHANNEL_IDS",
]

OSCAR_EVENT_TYPE_MAP: dict[int, RespiratoryEventType] = {
    CPAP_OBSTRUCTIVE: RespiratoryEventType.OBSTRUCTIVE_APNEA,
    CPAP_HYPOPNEA: RespiratoryEventType.HYPOPNEA,
    CPAP_CLEAR_AIRWAY: RespiratoryEventType.CENTRAL_APNEA,
    CPAP_RERA: RespiratoryEventType.RERA,
    CPAP_FLOW_LIMIT: RespiratoryEventType.FLOW_LIMITATION,
    CPAP_CSR: RespiratoryEventType.PERIODIC_BREATHING,
    CPAP_PERIODIC_BREATHING: RespiratoryEventType.PERIODIC_BREATHING,
}

OSCAR_WAVEFORM_TYPE_MAP: dict[int, WaveformType] = {
    CPAP_FLOW_RATE: WaveformType.FLOW_RATE,
    CPAP_MASK_PRESSURE: WaveformType.MASK_PRESSURE,
    CPAP_LEAK: WaveformType.LEAK_RATE,
    CPAP_RESPRATE: WaveformType.RESPIRATORY_RATE,
    CPAP_TIDAL_VOLUME: WaveformType.TIDAL_VOLUME,
    CPAP_MINUTE_VENT: WaveformType.MINUTE_VENTILATION,
    OXI_SPO2: WaveformType.SPO2,
    OXI_PULSE: WaveformType.PULSE,
}

OSCAR_WAVEFORM_UNITS: dict[int, str] = {
    CPAP_FLOW_RATE: "L/min",
    CPAP_MASK_PRESSURE: "cmH₂O",
    CPAP_LEAK: "L/min",
    CPAP_RESPRATE: "bpm",
    CPAP_TIDAL_VOLUME: "mL",
    CPAP_MINUTE_VENT: "L/min",
    OXI_SPO2: "%",
    OXI_PULSE: "bpm",
}

OSCAR_EVENT_CHANNEL_IDS: set[int] = set(OSCAR_EVENT_TYPE_MAP.keys())

OSCAR_WAVEFORM_CHANNEL_IDS: set[int] = set(OSCAR_WAVEFORM_TYPE_MAP.keys())
