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

    async def test_exc_with_response_status_code_emits_http_status_message(
        self,
    ) -> None:
        from unittest.mock import MagicMock

        from fastmcp.exceptions import ToolError

        mock_response = MagicMock()
        mock_response.status_code = 503

        exc = RuntimeError("secret response body")
        exc.response = mock_response

        @tool_error_boundary
        async def _bad() -> str:
            raise exc

        with pytest.raises(ToolError, match="HTTP 503 from upstream service"):
            await _bad()

    async def test_exc_with_response_status_does_not_include_response_body(
        self,
    ) -> None:
        from unittest.mock import MagicMock

        from fastmcp.exceptions import ToolError

        mock_response = MagicMock()
        mock_response.status_code = 401

        exc = RuntimeError("secret response body text")
        exc.response = mock_response

        @tool_error_boundary
        async def _bad() -> str:
            raise exc

        with pytest.raises(ToolError) as exc_info:
            await _bad()
        assert "secret response body text" not in str(exc_info.value)


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

    def test_non_ascii_payload_under_byte_limit_passes(self) -> None:
        # "Ā" (U+0100) is escaped by json.dumps to "Ā" — 6 ASCII bytes per char.
        # 80,000 chars × 6 bytes + JSON overhead ≈ 480,012 bytes; under 500,000.
        payload = {"data": "Ā" * 80_000}
        _check_response_size(payload, "test_tool")  # must not raise

    def test_non_ascii_payload_over_byte_limit_fails(self) -> None:
        from fastmcp.exceptions import ToolError

        # 90,000 "Ā" chars × 6 JSON bytes + overhead ≈ 540,012 bytes → over limit.
        payload = {"data": "Ā" * 90_000}
        with pytest.raises(ToolError, match="exceeds"):
            _check_response_size(payload, "test_tool")

    def test_measurement_failure_logs_warning_and_does_not_raise(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from unittest.mock import patch

        with patch("snore.mcp.server.json.dumps", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.WARNING, logger="snore.mcp.server"):
                _check_response_size({"key": "value"}, "my_tool")  # must not raise

        assert any("measurement failed" in r.message for r in caplog.records)


class TestBuildInstructions:
    def test_contains_snore_version(self) -> None:
        profile = get_profile("neutral")
        instructions = _build_instructions(profile)
        assert "snore" in instructions.lower()

    def test_contains_profile_display_name(self) -> None:
        profile = get_profile("uars")
        instructions = _build_instructions(profile)
        assert "UARS" in instructions

    def test_does_not_reference_docs_tools(self) -> None:
        profile = get_profile("neutral")
        instructions = _build_instructions(profile)
        assert "docs://tools" not in instructions

    def test_instructions_mention_schemas_resource(self) -> None:
        profile = get_profile("neutral")
        instructions = _build_instructions(profile)
        assert "docs://schemas" in instructions

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


class TestStage2ToolsRegistered:
    """The three Stage-2 tools appear in make_server() tool listing."""

    async def test_seven_tools_registered(self) -> None:
        """make_server() registers exactly seven tools (four Stage-1 + three Stage-2)."""
        from collections.abc import AsyncIterator
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock, patch

        import fastmcp

        from snore.mcp.server import SNORERuntime, make_server

        @asynccontextmanager
        async def _fake_lifespan(
            app: MagicMock, db_flag: str | None = None, profile_name: str = "neutral"
        ) -> AsyncIterator[SNORERuntime]:
            session = MagicMock()
            yield SNORERuntime(
                scope_provider=lambda: _noop_scope(session),
                profile_id=1,
            )

        @asynccontextmanager
        async def _noop_scope(s: MagicMock) -> AsyncIterator[MagicMock]:
            yield s

        mcp = make_server()
        with patch("snore.mcp.server._lifespan", _fake_lifespan):
            async with fastmcp.Client(mcp) as client:
                tools = await client.list_tools()

        tool_names = {t.name for t in tools}
        assert "get_breath_table" in tool_names
        assert "find_windows" in tool_names
        assert "compare_epochs" in tool_names
        # Stage-1 tools still present
        assert "get_data_overview" in tool_names
        assert "get_events" in tool_names
        assert len(tool_names) == 7

    async def test_compare_epochs_schema_has_epochs_parameter(self) -> None:
        """compare_epochs tool schema includes an 'epochs' parameter."""
        from collections.abc import AsyncIterator
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock, patch

        import fastmcp

        from snore.mcp.server import SNORERuntime, make_server

        @asynccontextmanager
        async def _fake_lifespan(
            app: MagicMock, db_flag: str | None = None, profile_name: str = "neutral"
        ) -> AsyncIterator[SNORERuntime]:
            session = MagicMock()
            yield SNORERuntime(
                scope_provider=lambda: _noop_scope(session),
                profile_id=1,
            )

        @asynccontextmanager
        async def _noop_scope(s: MagicMock) -> AsyncIterator[MagicMock]:
            yield s

        mcp = make_server()
        with patch("snore.mcp.server._lifespan", _fake_lifespan):
            async with fastmcp.Client(mcp) as client:
                tools = await client.list_tools()

        ce_tool = next(t for t in tools if t.name == "compare_epochs")
        # epochs parameter must appear in the input schema
        schema = ce_tool.inputSchema
        assert "epochs" in (schema.get("properties") or {}), (
            f"compare_epochs inputSchema missing 'epochs': {schema}"
        )


class TestLifespanStartupFailure:
    """_lifespan calls cleanup_database and re-raises when startup fails."""

    async def test_cleanup_called_and_error_reraised_on_profile_not_found(
        self,
    ) -> None:
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock, patch

        from snore.mcp.server import _lifespan

        # A mock DB session where .scalars().first() returns None → profile not found.
        mock_db = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        @asynccontextmanager
        async def _mock_session_scope():
            yield mock_db

        cleanup_mock = AsyncMock()
        mock_target = MagicMock()
        mock_target.resolve_async_url.return_value = "sqlite+aiosqlite:///:memory:"

        with (
            patch("snore.mcp.server.DatabaseTarget") as mock_target_cls,
            patch("snore.mcp.server.init_database_from_url", new_callable=AsyncMock),
            patch("snore.mcp.server.cleanup_database", cleanup_mock),
            patch("snore.mcp.server.session_scope", _mock_session_scope),
            patch("snore.parsers.register_all.ensure_registered_parsers"),
        ):
            mock_target_cls.from_env_and_flags.return_value = mock_target

            with pytest.raises(RuntimeError, match="No profile found"):
                async with _lifespan(None):
                    pass  # pragma: no cover

        cleanup_mock.assert_awaited_once()
