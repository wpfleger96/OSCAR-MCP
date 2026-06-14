"""
Event annotation labels shared across parsers and display code.

Maps device/file annotation text (long names, CamelCase variants, and
abbreviations) to the unified RespiratoryEventType, plus the set of
non-event annotations that should be ignored during event parsing.
"""

from snore.parsers.unified import RespiratoryEventType

__all__ = ["EVENT_TYPE_MAP", "FILTERED_ANNOTATIONS"]

EVENT_TYPE_MAP: dict[str, RespiratoryEventType] = {
    "Obstructive Apnea": RespiratoryEventType.OBSTRUCTIVE_APNEA,
    "ObstructiveApnea": RespiratoryEventType.OBSTRUCTIVE_APNEA,
    "Obstructive apnea": RespiratoryEventType.OBSTRUCTIVE_APNEA,
    "OA": RespiratoryEventType.OBSTRUCTIVE_APNEA,
    "Central Apnea": RespiratoryEventType.CENTRAL_APNEA,
    "CentralApnea": RespiratoryEventType.CENTRAL_APNEA,
    "Central apnea": RespiratoryEventType.CENTRAL_APNEA,
    "CA": RespiratoryEventType.CENTRAL_APNEA,
    "Clear Airway": RespiratoryEventType.CLEAR_AIRWAY,  # (same as Central Apnea in some ResMed devices)
    "ClearAirway": RespiratoryEventType.CLEAR_AIRWAY,
    "Apnea": RespiratoryEventType.UNCLASSIFIED_APNEA,
    "UA": RespiratoryEventType.UNCLASSIFIED_APNEA,
    "Hypopnea": RespiratoryEventType.HYPOPNEA,
    "H": RespiratoryEventType.HYPOPNEA,
    "RERA": RespiratoryEventType.RERA,  # (Respiratory Effort Related Arousal)
    "RE": RespiratoryEventType.RERA,
    "Arousal": RespiratoryEventType.RERA,  # OSCAR uses "Arousal" for RERA
    "Flow Limitation": RespiratoryEventType.FLOW_LIMITATION,
    "FlowLimitation": RespiratoryEventType.FLOW_LIMITATION,
    "FL": RespiratoryEventType.FLOW_LIMITATION,
    "Periodic Breathing": RespiratoryEventType.PERIODIC_BREATHING,
    "PeriodicBreathing": RespiratoryEventType.PERIODIC_BREATHING,
    "PB": RespiratoryEventType.PERIODIC_BREATHING,
    "Large Leak": RespiratoryEventType.LARGE_LEAK,
    "LargeLeak": RespiratoryEventType.LARGE_LEAK,
    "LL": RespiratoryEventType.LARGE_LEAK,
    "Vibratory Snore": RespiratoryEventType.VIBRATORY_SNORE,
    "VibratorySnore": RespiratoryEventType.VIBRATORY_SNORE,
    "VS": RespiratoryEventType.VIBRATORY_SNORE,
}

FILTERED_ANNOTATIONS: set[str] = {
    "Recording starts",
    "SpO2 Desaturation",  # handled separately if needed
}
