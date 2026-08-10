"""
Validation module for comparing SNORE's programmatic detection with machine events,
and for signal-level FL validation against the device's FLG waveform.
"""

from snore.validation.batch import BatchValidator
from snore.validation.fl_report import (
    FlAggregateMetrics,
    FlSessionValidation,
    FlValidationReport,
    export_fl_report_csv,
    export_fl_report_json,
)
from snore.validation.fl_validator import FlowLimitationValidator
from snore.validation.report import (
    ValidationReport,
    export_report_csv,
    export_report_json,
)
from snore.validation.stats import mean_or_none, spearman_or_none

__all__ = [
    "BatchValidator",
    "ValidationReport",
    "export_report_csv",
    "export_report_json",
    "FlowLimitationValidator",
    "FlValidationReport",
    "FlSessionValidation",
    "FlAggregateMetrics",
    "export_fl_report_json",
    "export_fl_report_csv",
    "spearman_or_none",
    "mean_or_none",
]
