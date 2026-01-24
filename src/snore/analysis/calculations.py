"""Statistical calculations for OSCAR therapy data."""

from datetime import date

from snore.database import models


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
