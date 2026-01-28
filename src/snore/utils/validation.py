"""Validation utilities for SNORE."""

from datetime import date, datetime


def validate_date_format(date_str: str) -> date:
    """
    Validate and parse date string in YYYY-MM-DD format.

    Args:
        date_str: Date string

    Returns:
        Parsed date object

    Raises:
        ValueError: If date format is invalid
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"Invalid date format: '{date_str}'. Expected format: YYYY-MM-DD (e.g., 2024-01-15)"
        ) from None


def validate_date_range(start_date: date, end_date: date) -> bool:
    """
    Validate that date range is logical.

    Args:
        start_date: Start date
        end_date: End date

    Returns:
        True if valid

    Raises:
        ValueError: If date range is invalid
    """
    if start_date > end_date:
        raise ValueError(
            f"Invalid date range: start_date ({start_date}) "
            f"must be before or equal to end_date ({end_date})"
        )
    return True
