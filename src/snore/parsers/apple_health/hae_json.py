"""Parser for Health Auto Export (HAE) JSON payloads.

HAE pushes JSON to a REST endpoint.  The payload is HTTP-bounded so we
return a list rather than an iterator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from snore.parsers.apple_health.models import RawHealthRecord
from snore.parsers.apple_health.type_handlers import (
    HAE_METRIC_NAME_MAP,
    _normalize_metric_name,
    parse_hae_metric,
)


@dataclass
class HAEParseResult:
    """Result of parsing one HAE JSON payload."""

    records: list[RawHealthRecord] = field(default_factory=list)
    unknown_metrics: dict[str, int] = field(default_factory=dict)
    """Metric name → point count for metrics not in ``HAE_METRIC_NAME_MAP``."""
    skipped_points: int = 0
    """Malformed points within known metrics that could not be parsed."""


def parse_payload(payload: dict[str, object]) -> HAEParseResult:
    """Parse a raw HAE JSON payload into health records.

    Tolerant of missing ``data`` / ``metrics`` keys — returns an empty result
    rather than raising.

    Args:
        payload: The decoded JSON object, e.g. ``{"data": {"metrics": [...]}}``.
    """
    result = HAEParseResult()

    data = payload.get("data")
    if not isinstance(data, dict):
        return result

    metrics = data.get("metrics")
    if not isinstance(metrics, list):
        return result

    for metric in metrics:
        if not isinstance(metric, dict):
            continue

        name = str(metric.get("name", ""))
        normalized = _normalize_metric_name(name)

        if normalized not in HAE_METRIC_NAME_MAP:
            # Unknown — count under the original un-normalized name so callers
            # can report exactly what arrived in the payload.
            point_data = metric.get("data")
            count = len(point_data) if isinstance(point_data, list) else 0
            if name:
                result.unknown_metrics[name] = (
                    result.unknown_metrics.get(name, 0) + count
                )
            continue

        point_data = metric.get("data")
        total_points = len(point_data) if isinstance(point_data, list) else 0
        parsed = list(parse_hae_metric(metric))
        result.records.extend(parsed)
        result.skipped_points += total_points - len(parsed)

    return result
