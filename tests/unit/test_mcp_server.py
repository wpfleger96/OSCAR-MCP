"""Unit tests for MCP server wiring: error boundary, size guard, profile building."""

from __future__ import annotations

import pytest

from snore.mcp.profiles import get_profile
from snore.mcp.server import (
    RESPONSE_SIZE_LIMIT,
    _build_instructions,
    _check_response_size,
    tool_error_boundary,
)


class TestToolErrorBoundary:
    async def test_passes_through_on_success(self) -> None:
        @tool_error_boundary
        async def _ok() -> str:
            return "ok"

        result = await _ok()
        assert result == "ok"

    async def test_converts_validation_error_to_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        from snore.mcp.errors import ValidationError

        @tool_error_boundary
        async def _bad() -> str:
            raise ValidationError("bad input")

        with pytest.raises(ToolError, match="bad input"):
            await _bad()

    async def test_converts_value_error_to_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @tool_error_boundary
        async def _bad() -> str:
            raise ValueError("invalid value")

        with pytest.raises(ToolError, match="invalid value"):
            await _bad()

    async def test_passes_through_tool_error_unchanged(self) -> None:
        from fastmcp.exceptions import ToolError

        @tool_error_boundary
        async def _bad() -> str:
            raise ToolError("already a tool error")

        with pytest.raises(ToolError, match="already a tool error"):
            await _bad()

    async def test_converts_generic_exception_to_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        @tool_error_boundary
        async def _bad() -> str:
            raise RuntimeError("unexpected")

        with pytest.raises(ToolError, match="unexpected"):
            await _bad()


class TestCheckResponseSize:
    def test_small_response_passes(self) -> None:
        # Should not raise
        _check_response_size({"key": "value"}, "test_tool")

    def test_oversized_response_raises_tool_error(self) -> None:
        from fastmcp.exceptions import ToolError

        # Build a payload that exceeds RESPONSE_SIZE_LIMIT
        huge = {"data": "x" * (RESPONSE_SIZE_LIMIT + 1)}
        with pytest.raises(ToolError, match="exceeds"):
            _check_response_size(huge, "test_tool")

    def test_tool_name_appears_in_error_message(self) -> None:
        from fastmcp.exceptions import ToolError

        huge = {"data": "x" * (RESPONSE_SIZE_LIMIT + 1)}
        with pytest.raises(ToolError, match="my_tool"):
            _check_response_size(huge, "my_tool")

    def test_narrow_your_query_hint_included(self) -> None:
        from fastmcp.exceptions import ToolError

        huge = {"data": "x" * (RESPONSE_SIZE_LIMIT + 1)}
        with pytest.raises(ToolError, match="Narrow your query"):
            _check_response_size(huge, "test_tool")


class TestBuildInstructions:
    def test_contains_snore_version(self) -> None:
        profile = get_profile("neutral")
        instructions = _build_instructions(profile)
        assert "snore" in instructions.lower()

    def test_contains_profile_display_name(self) -> None:
        profile = get_profile("uars")
        instructions = _build_instructions(profile)
        assert "UARS" in instructions

    def test_contains_required_reading_directive(self) -> None:
        profile = get_profile("neutral")
        instructions = _build_instructions(profile)
        assert "REQUIRED READING" in instructions

    def test_contains_docs_tools_reference(self) -> None:
        profile = get_profile("neutral")
        instructions = _build_instructions(profile)
        assert "docs://tools" in instructions

    def test_all_profiles_produce_non_empty_instructions(self) -> None:
        from snore.mcp.profiles import VALID_PROFILES

        for name in VALID_PROFILES:
            profile = get_profile(name)
            instructions = _build_instructions(profile)
            assert len(instructions) > 100


class TestValidatePageArgs:
    """validate_page_args rejects out-of-range pagination values."""

    def test_valid_args_return_capped_page_size(self) -> None:
        from snore.mcp.validation import validate_page_args

        assert validate_page_args(1, 30) == 30

    def test_page_size_is_capped_at_90(self) -> None:
        from snore.mcp.validation import validate_page_args

        assert validate_page_args(1, 200) == 90

    def test_page_zero_raises(self) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.validation import validate_page_args

        with pytest.raises(ValidationError, match="page must be >= 1"):
            validate_page_args(0, 30)

    def test_page_negative_raises(self) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.validation import validate_page_args

        with pytest.raises(ValidationError, match="page must be >= 1"):
            validate_page_args(-5, 30)

    def test_page_size_zero_raises(self) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.validation import validate_page_args

        with pytest.raises(ValidationError, match="page_size must be >= 1"):
            validate_page_args(1, 0)

    def test_page_size_negative_raises(self) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.validation import validate_page_args

        with pytest.raises(ValidationError, match="page_size must be >= 1"):
            validate_page_args(1, -10)


class TestValidateComplianceThreshold:
    """validate_compliance_threshold rejects negative thresholds."""

    def test_zero_is_valid(self) -> None:
        from snore.mcp.validation import validate_compliance_threshold

        validate_compliance_threshold(0.0)  # must not raise

    def test_positive_value_is_valid(self) -> None:
        from snore.mcp.validation import validate_compliance_threshold

        validate_compliance_threshold(4.0)  # must not raise

    def test_negative_raises(self) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.validation import validate_compliance_threshold

        with pytest.raises(
            ValidationError, match="compliance_threshold_hours must be >= 0"
        ):
            validate_compliance_threshold(-1.0)


class TestValidateMinDuration:
    """validate_min_duration rejects negative durations."""

    def test_none_is_valid(self) -> None:
        from snore.mcp.validation import validate_min_duration

        validate_min_duration(None)  # must not raise

    def test_zero_is_valid(self) -> None:
        from snore.mcp.validation import validate_min_duration

        validate_min_duration(0.0)  # must not raise

    def test_positive_is_valid(self) -> None:
        from snore.mcp.validation import validate_min_duration

        validate_min_duration(5.0)  # must not raise

    def test_negative_raises(self) -> None:
        from snore.mcp.errors import ValidationError
        from snore.mcp.validation import validate_min_duration

        with pytest.raises(ValidationError, match="min_duration must be >= 0"):
            validate_min_duration(-0.1)
