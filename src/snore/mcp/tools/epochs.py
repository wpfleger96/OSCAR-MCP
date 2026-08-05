"""compare_epochs tool — BreathService.compare_epochs() adapter.

No domain computation in this module: validate input → call service → map types.
Date range parsing and metric-string validation happen here; statistics,
RX-homogeneity checks, and cross-epoch identity checks all live in the service.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from snore.mcp.errors import ValidationError
from snore.mcp.schemas import (
    CompareEpochsResponse,
    EpochDistribution,
    EpochRxViolationRow,
    EpochSpec,
    EpochStats,
)
from snore.mcp.tools._service_errors import (
    MAPPED_SERVICE_ERRORS,
    raise_mapped_service_error,
)
from snore.mcp.validation import parse_date_range


def _map_distribution(d: Any) -> EpochDistribution:
    """Map a DistributionStats DTO to the MCP EpochDistribution schema (field-for-field)."""
    return EpochDistribution(
        median=d.median,
        iqr=d.iqr,
        p95=d.p95,
        n_breaths=d.n_breaths,
        n_nights=d.n_nights,
    )


async def compare_epochs(
    db_session: AsyncSession,
    profile_id: int,
    epochs: list[EpochSpec],
    metrics: list[str] | None = None,
) -> CompareEpochsResponse:
    """Compare breath-feature distributions across therapy settings epochs.

    Validates per-epoch date ranges and metric strings, then delegates to
    ``BreathService.compare_epochs()``.  Maps the typed result DTO to the
    ``CompareEpochsResponse`` schema: dates to isoformat strings, NullReason/enum
    values to plain strings, and flow_class_distribution int keys to string keys.

    Service ``ValueError`` ("All epochs in a comparison must target the same
    device_id") propagates to the MCP error boundary unchanged — it is a caller
    error that belongs on the wire as a ToolError.
    """
    from snore.services.breath_service import (  # noqa: PLC0415
        BreathService,
        DistributionMetric,
        EpochRequest,
    )

    # Parse and validate per-epoch date ranges.  parse_date_range raises
    # ValidationError with the exact epoch index in the parameter name so the
    # LLM can identify which epoch is malformed.
    epoch_requests: list[EpochRequest] = []
    for i, spec in enumerate(epochs):
        start_date, end_date = parse_date_range(
            spec.date_start,
            spec.date_end,
            start_param=f"epochs[{i}].date_start",
            end_param=f"epochs[{i}].date_end",
        )
        epoch_requests.append(
            EpochRequest(
                label=spec.label,
                date_start=start_date,
                date_end=end_date,
                device_id=spec.device_id,
            )
        )

    # Validate metric strings and map to DistributionMetric enum values.
    parsed_metrics: list[DistributionMetric] | None = None
    if metrics is not None:
        valid_names = {m.value for m in DistributionMetric}
        for m in metrics:
            if m not in valid_names:
                raise ValidationError(
                    f"Unknown metric {m!r}. Valid metrics: "
                    "mid_insp_flattening, flatness_index, tidal_volume_ml, ie_ratio"
                )
        parsed_metrics = [DistributionMetric(m) for m in metrics]

    # Delegate to service.  DeviceAmbiguityError propagates from the service
    # (multi-device profile, no device_id given); DeviceNotOwnedError for an
    # explicit foreign device is handled INSIDE the service and returned as
    # not_available epochs — no exception reaches here in that case.
    try:
        result = await BreathService(db_session, profile_id).compare_epochs(
            epoch_requests, metrics=parsed_metrics
        )
    except MAPPED_SERVICE_ERRORS as exc:
        raise_mapped_service_error(exc)

    # Map CompareEpochsResult → CompareEpochsResponse.
    # Enum values → str; dates → isoformat; flow_class int keys → str keys.
    epoch_stats: list[EpochStats] = [
        EpochStats(
            label=s.label,
            date_start=s.date_start.isoformat(),
            date_end=s.date_end.isoformat(),
            nights_with_data=s.nights_with_data,
            nights_missing_analysis=s.nights_missing_analysis,
            algorithm_identity=s.algorithm_identity.model_dump(mode="json")
            if s.algorithm_identity is not None
            else None,
            null_reason=str(s.null_reason) if s.null_reason is not None else None,
            primary_mode=s.primary_mode,
            mid_insp_flattening=_map_distribution(s.mid_insp_flattening),
            flatness_index=_map_distribution(s.flatness_index),
            flow_class_distribution={
                str(k): v for k, v in s.flow_class_distribution.items()
            },
            tidal_volume_ml=_map_distribution(s.tidal_volume_ml),
            ie_ratio=_map_distribution(s.ie_ratio),
            rera_proxy_count=s.rera_proxy_count,
            rera_reason=str(s.rera_reason) if s.rera_reason is not None else None,
            rx_settings=s.rx_settings,
        )
        for s in result.epochs
    ]

    return CompareEpochsResponse(
        epochs=epoch_stats,
        null_reason=str(result.null_reason) if result.null_reason is not None else None,
        rx_violations=[
            EpochRxViolationRow(
                epoch_label=v.epoch_label,
                changed_keys=v.changed_keys,
                change_dates=[d.isoformat() for d in v.change_dates],
            )
            for v in result.rx_violations
        ],
    )
