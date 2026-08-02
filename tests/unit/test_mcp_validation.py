"""Unit tests for MCP input validation helpers."""

from __future__ import annotations

import pytest

from snore.mcp.errors import ValidationError
from snore.mcp.validation import parse_date, parse_date_range


class TestParseDate:
    def test_valid_iso_date_returns_date_object(self) -> None:
        result = parse_date("2024-08-01", "start")
        from datetime import date

        assert result == date(2024, 8, 1)

    def test_missing_leading_zero_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            parse_date("2024-8-1", "start")

    def test_non_date_string_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            parse_date("not-a-date", "start")

    def test_empty_string_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            parse_date("", "start")

    def test_impossible_date_raises_validation_error(self) -> None:
        # February 30 is not a real date
        with pytest.raises(ValidationError):
            parse_date("2024-02-30", "start")

    def test_param_name_appears_in_error_message(self) -> None:
        with pytest.raises(ValidationError, match="my_param"):
            parse_date("bad", "my_param")


class TestParseDateRange:
    def test_valid_range_returns_tuple(self) -> None:
        from datetime import date

        start, end = parse_date_range("2024-01-01", "2024-01-31")
        assert start == date(2024, 1, 1)
        assert end == date(2024, 1, 31)

    def test_single_day_range_is_valid(self) -> None:
        from datetime import date

        start, end = parse_date_range("2024-06-15", "2024-06-15")
        assert start == end == date(2024, 6, 15)

    def test_start_after_end_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="must not be after"):
            parse_date_range("2024-02-01", "2024-01-01")

    def test_invalid_start_propagates_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="start"):
            parse_date_range("bad", "2024-01-01")

    def test_invalid_end_propagates_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="end"):
            parse_date_range("2024-01-01", "bad")
