"""Single-source registry for the session/day metric set.

The 74-field metric vocabulary is declared in several places (unified parser
model, ORM ``Statistics``, services schema, export lists, day aggregation).
This leaf module (stdlib-only) is the canonical declaration; schema-alignment
tests assert the other declarations stay in sync with it.
"""

from dataclasses import dataclass
from enum import StrEnum


class DayAgg(StrEnum):
    """How a session statistic rolls up into its Day-table column."""

    SUM = "sum"
    MIN = "min"
    MAX = "max"
    USAGE_WEIGHTED_MEAN = "usage_weighted_mean"


@dataclass(frozen=True)
class MetricSpec:
    """One metric column: its name, display unit, and day-level aggregation.

    ``day_agg`` is None for metrics that have no mirrored Day-table column.
    """

    name: str
    unit: str | None
    day_agg: DayAgg | None


def _m(name: str, unit: str | None = None) -> MetricSpec:
    """Session-level metric with no Day-table column."""
    return MetricSpec(name=name, unit=unit, day_agg=None)


# All models.Statistics metric columns (every column except session_id),
# ordered as declared in the ORM.  Units follow the field descriptions in
# parsers.unified.SessionStatistics, applied family-wide.
SESSION_METRICS: tuple[MetricSpec, ...] = (
    # Event counts
    _m("obstructive_apneas"),
    _m("central_apneas"),
    _m("mixed_apneas"),
    _m("hypopneas"),
    _m("reras"),
    _m("flow_limitations"),
    # Indices
    _m("ahi", "events/h"),
    _m("oai", "events/h"),
    _m("cai", "events/h"),
    _m("hi", "events/h"),
    _m("rei", "events/h"),
    # Pressure
    _m("pressure_min", "cmH2O"),
    _m("pressure_max", "cmH2O"),
    _m("pressure_median", "cmH2O"),
    _m("pressure_mean", "cmH2O"),
    _m("pressure_95th", "cmH2O"),
    # EPAP
    _m("epap_min", "cmH2O"),
    _m("epap_max", "cmH2O"),
    _m("epap_median", "cmH2O"),
    _m("epap_mean", "cmH2O"),
    _m("epap_95th", "cmH2O"),
    # IPAP
    _m("ipap_median", "cmH2O"),
    _m("ipap_95th", "cmH2O"),
    _m("ipap_max", "cmH2O"),
    # Leak
    _m("leak_min", "L/min"),
    _m("leak_max", "L/min"),
    _m("leak_median", "L/min"),
    _m("leak_mean", "L/min"),
    _m("leak_95th", "L/min"),
    _m("leak_percentile_70", "L/min"),
    # Respiratory rate
    _m("respiratory_rate_min", "breaths/min"),
    _m("respiratory_rate_max", "breaths/min"),
    _m("respiratory_rate_mean", "breaths/min"),
    # Tidal volume
    _m("tidal_volume_min", "mL"),
    _m("tidal_volume_max", "mL"),
    _m("tidal_volume_mean", "mL"),
    # Minute ventilation
    _m("minute_ventilation_min", "L/min"),
    _m("minute_ventilation_max", "L/min"),
    _m("minute_ventilation_mean", "L/min"),
    # SpO2 / pulse oximetry
    _m("spo2_min", "%"),
    _m("spo2_max", "%"),
    _m("spo2_mean", "%"),
    _m("spo2_median", "%"),
    _m("spo2_95th", "%"),
    _m("spo2_time_below_90", "seconds"),
    _m("pulse_min", "bpm"),
    _m("pulse_max", "bpm"),
    _m("pulse_mean", "bpm"),
    # Usage
    _m("usage_hours", "hours"),
    # STR daily summary extras
    _m("uai", "events/h"),
    _m("ai", "events/h"),
    _m("rin", "events/h"),
    _m("csr_pct", "%"),
    _m("spont_cyc_pct", "%"),
    _m("respiratory_rate_95th", "breaths/min"),
    _m("tidal_volume_95th", "mL"),
    _m("minute_ventilation_95th", "L/min"),
    _m("ie_ratio_median"),
    _m("ie_ratio_95th"),
    _m("ie_ratio_max"),
    _m("ti_median", "seconds"),
    _m("ti_95th", "seconds"),
    _m("ti_max", "seconds"),
    _m("flow_5th", "L/min"),
    _m("flow_95th", "L/min"),
    _m("blow_press_5th", "cmH2O"),
    _m("blow_press_95th", "cmH2O"),
    _m("blow_flow_median", "L/min"),
    _m("amb_humidity_median", "%"),
    _m("hum_temp_median"),
    _m("htube_temp_median"),
    _m("htube_pow_median"),
    _m("hum_pow_median"),
    _m("mask_events"),
)


def _day(name: str, unit: str | None, day_agg: DayAgg) -> MetricSpec:
    return MetricSpec(name=name, unit=unit, day_agg=day_agg)


# Day-table stat columns mirroring Statistics fields, with their day-level
# aggregation.  Counts, indices (ahi/oai/cai/hi), hours, and bookkeeping
# columns are aggregated separately and intentionally excluded.
DAY_METRIC_STAT_COLUMNS: tuple[MetricSpec, ...] = (
    _day("pressure_min", "cmH2O", DayAgg.MIN),
    _day("pressure_max", "cmH2O", DayAgg.MAX),
    _day("pressure_median", "cmH2O", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("pressure_mean", "cmH2O", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("pressure_95th", "cmH2O", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("epap_min", "cmH2O", DayAgg.MIN),
    _day("epap_max", "cmH2O", DayAgg.MAX),
    _day("epap_median", "cmH2O", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("epap_mean", "cmH2O", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("epap_95th", "cmH2O", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("leak_min", "L/min", DayAgg.MIN),
    _day("leak_max", "L/min", DayAgg.MAX),
    _day("leak_median", "L/min", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("leak_mean", "L/min", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("leak_95th", "L/min", DayAgg.USAGE_WEIGHTED_MEAN),
    _day("spo2_min", "%", DayAgg.MIN),
    _day("spo2_max", "%", DayAgg.MAX),
    _day("spo2_mean", "%", DayAgg.USAGE_WEIGHTED_MEAN),
)


# Exact CSV/JSON export stat-key subset, in exact output order.  Shared by the
# CSV header, the CSV row builder, the export SELECT, and the statistics-dict
# assembly in export_service.
EXPORT_STAT_KEYS: tuple[str, ...] = (
    "ahi",
    "oai",
    "cai",
    "hi",
    "obstructive_apneas",
    "central_apneas",
    "hypopneas",
    "reras",
    "pressure_mean",
    "pressure_95th",
    "epap_mean",
    "leak_mean",
    "leak_95th",
    "spo2_mean",
    "usage_hours",
)
