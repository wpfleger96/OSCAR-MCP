"""Input validation helpers for MCP tools."""

from __future__ import annotations

import re

from datetime import date

from snore.mcp.errors import ValidationError

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value: str, param_name: str) -> date:
    """Parse an ISO 8601 date string (YYYY-MM-DD).

    Args:
        value: The date string to parse.
        param_name: Parameter name used in error messages.

    Returns:
        Parsed date object.

    Raises:
        ValidationError: If the string is not a valid YYYY-MM-DD date.
    """
    if not _ISO_DATE_RE.match(value):
        raise ValidationError(
            f"Invalid date value {value!r} for parameter '{param_name}'. "
            "Expected YYYY-MM-DD format, e.g. '2025-08-01'."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            f"Invalid date value {value!r} for parameter '{param_name}': {exc}"
        ) from exc


def parse_date_range(
    start: str,
    end: str,
    *,
    start_param: str = "start",
    end_param: str = "end",
) -> tuple[date, date]:
    """Parse and validate a start/end date range.

    Args:
        start: Start date string (YYYY-MM-DD).
        end: End date string (YYYY-MM-DD).
        start_param: Name of the start parameter for error messages.
        end_param: Name of the end parameter for error messages.

    Returns:
        Tuple of (start_date, end_date).

    Raises:
        ValidationError: If either date is invalid or start > end.
    """
    start_date = parse_date(start, start_param)
    end_date = parse_date(end, end_param)
    if start_date > end_date:
        raise ValidationError(
            f"'{start_param}' ({start}) must not be after '{end_param}' ({end})."
        )
    return start_date, end_date
