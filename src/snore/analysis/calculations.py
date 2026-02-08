"""Statistical calculations for OSCAR therapy data."""

from datetime import date, timedelta
from statistics import median
from typing import Literal

from snore.database import models
from snore.models.statistics import PeriodStatistics


def calculate_average_ahi(days: list[models.Day]) -> float | None:
    """
    Calculate average AHI across multiple days.

    Args:
        days: List of Day records

    Returns:
        Average AHI or None if no data
    """
    ahi_values = [day.ahi for day in days if day.ahi is not None]
    if not ahi_values:
        return None

    return sum(ahi_values) / len(ahi_values)


def calculate_total_hours(days: list[models.Day]) -> float:
    """
    Calculate total therapy hours across multiple days.

    Args:
        days: List of Day records

    Returns:
        Total hours
    """
    return sum(day.total_therapy_hours or 0 for day in days)


def calculate_average_hours_per_day(days: list[models.Day]) -> float:
    """
    Calculate average therapy hours per day.

    Only includes days with actual usage.

    Args:
        days: List of Day records

    Returns:
        Average hours per day
    """
    days_with_usage = [
        day for day in days if day.total_therapy_hours and day.total_therapy_hours > 0
    ]

    if not days_with_usage:
        return 0.0

    total_hours = sum(day.total_therapy_hours for day in days_with_usage)
    return total_hours / len(days_with_usage)


def assess_therapy_effectiveness(avg_ahi: float | None) -> str:
    """
    Assess therapy effectiveness based on AHI.

    Args:
        avg_ahi: Average AHI

    Returns:
        Assessment string: excellent, good, fair, poor
    """
    if avg_ahi is None:
        return "unknown"

    if avg_ahi < 5:
        return "excellent"
    elif avg_ahi < 10:
        return "good"
    elif avg_ahi < 15:
        return "fair"
    else:
        return "poor"


def get_date_range(days: list[models.Day]) -> tuple[date, date] | None:
    """
    Get date range from list of days.

    Args:
        days: List of Day records

    Returns:
        Tuple of (start_date, end_date) or None if no days
    """
    if not days:
        return None

    dates = [day.date for day in days]
    return min(dates), max(dates)


def calculate_median_ahi(days: list[models.Day]) -> float | None:
    """
    Calculate median AHI across multiple days.

    Args:
        days: List of Day records

    Returns:
        Median AHI or None if no data
    """
    ahi_values = [day.ahi for day in days if day.ahi is not None]
    if not ahi_values:
        return None

    return median(ahi_values)


def _get_period_boundaries(
    period_type: Literal["week", "month", "6month", "year"],
    start_date: date,
    end_date: date,
) -> list[tuple[date, date]]:
    """
    Generate period boundaries for bucketing days.

    Args:
        period_type: One of 'week', 'month', '6month', 'year'
        start_date: Start of date range
        end_date: End of date range

    Returns:
        List of (period_start, period_end) tuples
    """
    periods = []
    current = start_date

    if period_type == "week":
        current = current - timedelta(days=current.weekday())

        while current <= end_date:
            period_end = current + timedelta(days=6)
            periods.append((current, period_end))
            current = period_end + timedelta(days=1)

    elif period_type == "month":
        current = current.replace(day=1)

        while current <= end_date:
            if current.month == 12:
                next_month = current.replace(year=current.year + 1, month=1)
            else:
                next_month = current.replace(month=current.month + 1)

            period_end = next_month - timedelta(days=1)
            periods.append((current, period_end))
            current = next_month

    elif period_type == "6month":
        if current.month <= 6:
            current = current.replace(month=1, day=1)
        else:
            current = current.replace(month=7, day=1)

        while current <= end_date:
            if current.month == 1:
                period_end = current.replace(month=6, day=30)
            else:
                period_end = current.replace(month=12, day=31)

            periods.append((current, period_end))

            if current.month == 1:
                current = current.replace(month=7)
            else:
                current = current.replace(year=current.year + 1, month=1)

    elif period_type == "year":
        current = current.replace(month=1, day=1)

        while current <= end_date:
            period_end = current.replace(month=12, day=31)
            periods.append((current, period_end))
            current = current.replace(year=current.year + 1)
    else:
        raise ValueError(f"Invalid period_type: {period_type}")

    return periods


def _field_avg(days: list[models.Day], field: str) -> float | None:
    """Extract field average helper for period statistics."""
    values = [getattr(day, field) for day in days if getattr(day, field) is not None]
    return sum(values) / len(values) if values else None


def calculate_period_statistics(
    day_records: list[models.Day],
    period_type: Literal["week", "month", "6month", "year"],
) -> list[PeriodStatistics]:
    """
    Calculate statistics grouped by time periods.

    Args:
        day_records: List of Day records
        period_type: One of 'week', 'month', '6month', 'year'

    Returns:
        List of PeriodStatistics for each period
    """
    if not day_records:
        return []

    dates = [day.date for day in day_records]
    start_date = min(dates)
    end_date = max(dates)

    periods = _get_period_boundaries(period_type, start_date, end_date)
    results = []

    for period_start, period_end in periods:
        days_in_period = [
            day for day in day_records if period_start <= day.date <= period_end
        ]

        if not days_in_period:
            continue

        days_used = len(days_in_period)
        days_in_period_count = (period_end - period_start).days + 1

        avg_hours_per_day = calculate_average_hours_per_day(days_in_period)
        avg_ahi = calculate_average_ahi(days_in_period)
        median_ahi = calculate_median_ahi(days_in_period)

        avg_pressure = _field_avg(days_in_period, "pressure_median")
        avg_leak = _field_avg(days_in_period, "leak_median")
        avg_spo2 = _field_avg(days_in_period, "spo2_mean")

        spo2_mins = [day.spo2_min for day in days_in_period if day.spo2_min is not None]
        min_spo2 = min(spo2_mins) if spo2_mins else None

        results.append(
            PeriodStatistics(
                period_type=period_type,
                period_start=period_start,
                period_end=period_end,
                days_used=days_used,
                days_in_period=days_in_period_count,
                avg_hours_per_day=avg_hours_per_day if avg_hours_per_day > 0 else None,
                avg_ahi=avg_ahi,
                median_ahi=median_ahi,
                avg_pressure=avg_pressure,
                avg_leak=avg_leak,
                avg_spo2=avg_spo2,
                min_spo2=min_spo2,
            )
        )

    return results


def calculate_trends(
    period_stats: list[PeriodStatistics],
) -> dict[str, list[tuple[date, float | None]]]:
    """
    Extract trend data from period statistics.

    Args:
        period_stats: List of PeriodStatistics

    Returns:
        Dictionary mapping metric names to lists of (date, value) tuples.
        Metrics include: ahi, usage, spo2, leak
    """
    trends: dict[str, list[tuple[date, float | None]]] = {
        "ahi": [],
        "usage": [],
        "spo2": [],
        "leak": [],
    }

    for stat in period_stats:
        trends["ahi"].append((stat.period_start, stat.avg_ahi))
        trends["usage"].append((stat.period_start, stat.avg_hours_per_day))
        trends["spo2"].append((stat.period_start, stat.avg_spo2))
        trends["leak"].append((stat.period_start, stat.avg_leak))

    return trends
