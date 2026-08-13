"""Type registry and per-record parsing logic for Apple Health data.

To add a future quantity metric: add one entry to ``QUANTITY_TYPES`` (HK
identifier → expected unit, or ``None`` to accept whatever arrives) and one
entry to ``HAE_METRIC_NAME_MAP``.  No other changes required.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from xml.etree.ElementTree import Element

from snore.parsers.apple_health.models import RawHealthRecord, apply_noon_split

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"

QUANTITY_TYPES: dict[str, str | None] = {
    "HKQuantityTypeIdentifierOxygenSaturation": "%",
    "HKQuantityTypeIdentifierRespiratoryRate": "count/min",
    # Unit is unconfirmed against real export data; accept whatever arrives.
    "HKQuantityTypeIdentifierAppleSleepingBreathingDisturbances": None,
}

# Maps all 7 XML sleep value strings to canonical stage names.
# Legacy pre-watchOS-9 "Asleep" treated as AsleepUnspecified per spec.
XML_SLEEP_VALUE_MAP: dict[str, str] = {
    "HKCategoryValueSleepAnalysisInBed": "InBed",
    "HKCategoryValueSleepAnalysisAsleep": "AsleepUnspecified",  # legacy alias
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "AsleepUnspecified",
    "HKCategoryValueSleepAnalysisAwake": "Awake",
    "HKCategoryValueSleepAnalysisAsleepCore": "AsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep": "AsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM": "AsleepREM",
}

# HAE uses human-readable English labels, not HK identifiers.
HAE_SLEEP_VALUE_MAP: dict[str, str] = {
    "In Bed": "InBed",
    "Asleep": "AsleepUnspecified",  # uncategorised; HAE pre-watchOS-9 label
    "Unspecified": "AsleepUnspecified",
    "Core": "AsleepCore",
    "Deep": "AsleepDeep",
    "REM": "AsleepREM",
    "Awake": "Awake",
}

# HAE metric name → HK identifier.
# Keys are snake_case (the form community integrations actually send); use
# _normalize_metric_name() before lookup so display strings like "Sleep Analysis"
# also resolve.  Unknown names must be reported under their original spelling.
HAE_METRIC_NAME_MAP: dict[str, str] = {
    "sleep_analysis": SLEEP_TYPE,
    "blood_oxygen_saturation": "HKQuantityTypeIdentifierOxygenSaturation",
    "respiratory_rate": "HKQuantityTypeIdentifierRespiratoryRate",
    "apple_sleeping_breathing_disturbances": (
        "HKQuantityTypeIdentifierAppleSleepingBreathingDisturbances"
    ),
}


def _normalize_metric_name(name: str) -> str:
    """Normalize an HAE metric name to snake_case for ``HAE_METRIC_NAME_MAP`` lookup.

    Accepts both ``"sleep_analysis"`` (REST payload form) and
    ``"Sleep Analysis"`` (display-string form) — both map to ``"sleep_analysis"``.
    """
    return name.strip().lower().replace(" ", "_").replace("-", "_")


_HANDLED_TYPES: frozenset[str] = frozenset({SLEEP_TYPE} | set(QUANTITY_TYPES))


def is_handled_type(hk_type: str) -> bool:
    """Return ``True`` if this HK type identifier is processed by v1 of the parser."""
    return hk_type in _HANDLED_TYPES


def _parse_hk_timestamp(s: str) -> tuple[datetime, int]:
    """Parse ``'YYYY-MM-DD HH:MM:SS ±HHMM'`` → ``(naive wall-clock, utc_offset_seconds)``."""
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")
    offset_secs = int(dt.utcoffset().total_seconds())  # type: ignore[union-attr]
    return dt.replace(tzinfo=None), offset_secs


def parse_xml_record(elem: Element) -> RawHealthRecord | None:
    """Parse a ``<Record>`` Element into a ``RawHealthRecord``.

    Returns ``None`` for unhandled types or unparseable records.  The caller is
    responsible for counting skips and calling ``elem.clear()``.
    """
    hk_type = elem.get("type", "")
    if not is_handled_type(hk_type):
        return None

    try:
        start_time, utc_offset = _parse_hk_timestamp(elem.get("startDate", ""))
        end_time, _ = _parse_hk_timestamp(elem.get("endDate", ""))
    except ValueError:
        return None

    raw_value = elem.get("value", "")
    value_text: str | None = None
    value_num: float | None = None

    if hk_type == SLEEP_TYPE:
        value_text = XML_SLEEP_VALUE_MAP.get(raw_value)
        if value_text is None:
            return None  # unknown sleep value string — skip
    else:
        try:
            value_num = round(float(raw_value), 4)
        except ValueError:
            return None
        # Unit is stored as-is even if it mismatches the expected unit —
        # preserving data fidelity over silent coercion.

    return RawHealthRecord(
        record_type=hk_type,
        source_name=elem.get("sourceName", ""),
        source_version=elem.get("sourceVersion") or None,
        device_info=elem.get("device") or None,
        start_time=start_time,
        end_time=end_time,
        value_text=value_text,
        value_num=value_num,
        unit=elem.get("unit") or None,
        utc_offset_seconds=utc_offset,
        night_date=apply_noon_split(start_time),
        ingest_channel="export_xml",
    )


def parse_hae_metric(metric: dict[str, object]) -> Iterator[RawHealthRecord]:
    """Yield ``RawHealthRecord`` objects from one HAE metric dict.

    Skips malformed points silently; never raises.  The caller should only pass
    metrics whose ``name`` is present in ``HAE_METRIC_NAME_MAP``.
    """
    name = str(metric.get("name", ""))
    hk_type = HAE_METRIC_NAME_MAP.get(_normalize_metric_name(name))
    if hk_type is None:
        return

    is_sleep = hk_type == SLEEP_TYPE
    default_source = "Health Auto Export"
    units = str(metric.get("units")) if metric.get("units") is not None else None
    data = metric.get("data")
    if not isinstance(data, list):
        return

    for point in data:
        if not isinstance(point, dict):
            continue
        try:
            source_name = str(point.get("source") or default_source)

            if is_sleep:
                start_time, utc_offset = _parse_hk_timestamp(str(point["startDate"]))
                end_time, _ = _parse_hk_timestamp(str(point["endDate"]))
                raw_val = str(point.get("value", ""))
                value_text = HAE_SLEEP_VALUE_MAP.get(raw_val)
                if value_text is None:
                    continue  # unknown sleep stage — skip
                value_num: float | None = None
                point_unit = units
            else:
                raw_date = str(point["date"])
                start_time, utc_offset = _parse_hk_timestamp(raw_date)
                end_time = start_time  # point sample: start == end
                if "qty" in point:
                    qty = float(point["qty"])
                elif "Avg" in point:
                    # Aggregated shape (Min/Avg/Max); use Avg.
                    qty = float(point["Avg"])
                else:
                    continue  # no numeric value — skip
                value_num = round(qty, 4)
                value_text = None
                point_unit = units
        except (KeyError, ValueError, TypeError):
            continue  # malformed point — skip

        yield RawHealthRecord(
            record_type=hk_type,
            source_name=source_name,
            source_version=None,
            device_info=None,
            start_time=start_time,
            end_time=end_time,
            value_text=value_text,
            value_num=value_num,
            unit=point_unit,
            utc_offset_seconds=utc_offset,
            night_date=apply_noon_split(start_time),
            ingest_channel="hae_json",
        )
