"""
Validation module for comparing SNORE's programmatic detection with machine events,
and for signal-level FL and breath-trends validation against device waveforms.
"""

from snore.validation.apple_cross_report import (
    AppleCrossAggregate,
    AppleCrossNightRecord,
    AppleCrossValidationReport,
    PairCorrelation,
    correlate_night_pairs,
    export_apple_cross_report_csv,
    export_apple_cross_report_json,
)
from snore.validation.apple_cross_validator import AppleCrossValidator
from snore.validation.batch import BatchValidator
from snore.validation.breath_trends_report import (
    BreathTrendsAggregateMetrics,
    BreathTrendsSessionValidation,
    BreathTrendsValidationReport,
    ChannelAggregateMetrics,
    ChannelComparison,
    export_breath_trends_report_csv,
    export_breath_trends_report_json,
)
from snore.validation.breath_trends_validator import BreathTrendsValidator
from snore.validation.fl_report import (
    FlAggregateMetrics,
    FlSessionValidation,
    FlValidationReport,
    export_fl_report_csv,
    export_fl_report_json,
)
from snore.validation.fl_validator import FlowLimitationValidator
from snore.validation.report import (
    CrossParserSameDay,
    IntegrityReport,
    OverlappingSessionPair,
    ValidationReport,
    export_report_csv,
    export_report_json,
)
from snore.validation.rera_report import (
    ReraAggregateMetrics,
    ReraSessionValidation,
    ReraValidationReport,
    export_rera_report_csv,
    export_rera_report_json,
)
from snore.validation.rera_validator import ReraValidator
from snore.validation.stats import mean_or_none, spearman_or_none
from snore.validation.sweep import (
    DEFAULT_GRIDS,
    DEFAULT_KNOBS,
    NOT_SWEEPABLE_NOTICE,
    TARGETS,
    SweepData,
    SweepResult,
    SweepRow,
    evaluate_grid,
    export_sweep_csv,
    load_sweep_data,
)

__all__ = [
    "AppleCrossValidator",
    "AppleCrossValidationReport",
    "AppleCrossNightRecord",
    "AppleCrossAggregate",
    "PairCorrelation",
    "correlate_night_pairs",
    "export_apple_cross_report_json",
    "export_apple_cross_report_csv",
    "BatchValidator",
    "CrossParserSameDay",
    "IntegrityReport",
    "OverlappingSessionPair",
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
    "BreathTrendsValidator",
    "BreathTrendsValidationReport",
    "BreathTrendsSessionValidation",
    "BreathTrendsAggregateMetrics",
    "ChannelComparison",
    "ChannelAggregateMetrics",
    "export_breath_trends_report_json",
    "export_breath_trends_report_csv",
    "ReraValidator",
    "ReraValidationReport",
    "ReraSessionValidation",
    "ReraAggregateMetrics",
    "export_rera_report_json",
    "export_rera_report_csv",
    "load_sweep_data",
    "evaluate_grid",
    "export_sweep_csv",
    "SweepData",
    "SweepResult",
    "SweepRow",
    "DEFAULT_GRIDS",
    "DEFAULT_KNOBS",
    "TARGETS",
    "NOT_SWEEPABLE_NOTICE",
]
