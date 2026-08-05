"""Unit tests for the mcp CLI command: transport switching, env validation, click.Choice."""

from __future__ import annotations

import sys

from unittest.mock import MagicMock, patch

import pytest

from click.testing import CliRunner


def _make_mock_auth_module() -> MagicMock:
    """Return a fake snore.mcp.auth module with make_auth_provider configured."""
    mock_auth_mod = MagicMock()
    mock_auth_mod.make_auth_provider.return_value = MagicMock(name="auth_provider")
    return mock_auth_mod


class TestMcpCliTransportChoice:
    """click.Choice rejects transports other than stdio and http."""

    def test_invalid_transport_rejected(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "websocket"])
        assert result.exit_code != 0
        assert "Invalid value for '--transport'" in result.output

    def test_stdio_accepted_as_choice(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        mock_server = MagicMock()
        with patch("snore.mcp.server.make_server", return_value=mock_server):
            result = runner.invoke(mcp, ["--transport", "stdio"])
        assert "Invalid value for '--transport'" not in result.output

    def test_http_accepted_as_choice_fails_on_missing_env_not_choice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """http is valid in click.Choice — failure comes from missing env vars, not the Choice."""
        from snore.cli.commands.mcp import mcp

        monkeypatch.delenv("SNORE_MCP_BASE_URL", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "http"])
        assert "Invalid value for '--transport'" not in result.output
        assert "HTTP transport requires environment variables" in result.output


class TestMcpCliStdioPath:
    """stdio transport calls make_server() with no auth and run(transport='stdio')."""

    def test_stdio_calls_make_server_and_run(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        mock_server = MagicMock()
        with patch(
            "snore.mcp.server.make_server", return_value=mock_server
        ) as mock_make:
            result = runner.invoke(mcp, [])

        assert result.exit_code == 0, result.output
        mock_make.assert_called_once_with(db_flag=None, profile_name="neutral")
        mock_server.run.assert_called_once_with(transport="stdio")

    def test_stdio_with_db_and_profile_flags(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        mock_server = MagicMock()
        with patch(
            "snore.mcp.server.make_server", return_value=mock_server
        ) as mock_make:
            result = runner.invoke(mcp, ["--db", "/tmp/test.db", "--profile", "osa"])

        assert result.exit_code == 0, result.output
        mock_make.assert_called_once_with(db_flag="/tmp/test.db", profile_name="osa")
        mock_server.run.assert_called_once_with(transport="stdio")

    def test_stdio_with_custom_host_emits_warning(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        mock_server = MagicMock()
        with patch("snore.mcp.server.make_server", return_value=mock_server):
            result = runner.invoke(mcp, ["--host", "0.0.0.0"])

        assert result.exit_code == 0, result.output
        assert "ignored" in result.output

    def test_stdio_with_custom_port_emits_warning(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        mock_server = MagicMock()
        with patch("snore.mcp.server.make_server", return_value=mock_server):
            result = runner.invoke(mcp, ["--port", "9999"])

        assert result.exit_code == 0, result.output
        assert "ignored" in result.output

    def test_stdio_default_host_and_port_no_warning(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        mock_server = MagicMock()
        with patch("snore.mcp.server.make_server", return_value=mock_server):
            result = runner.invoke(mcp, [])

        assert result.exit_code == 0, result.output
        assert "ignored" not in result.output


class TestMcpCliHttpMissingEnvVars:
    """http transport without env vars -> UsageError naming the missing vars."""

    def test_all_missing_lists_all_three(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from snore.cli.commands.mcp import mcp

        monkeypatch.delenv("SNORE_MCP_BASE_URL", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "http"])
        assert result.exit_code != 0
        assert "SNORE_MCP_BASE_URL" in result.output
        assert "GOOGLE_CLIENT_ID" in result.output
        assert "GOOGLE_CLIENT_SECRET" in result.output

    def test_partial_missing_lists_only_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from snore.cli.commands.mcp import mcp

        monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "http"])
        assert result.exit_code != 0
        assert "GOOGLE_CLIENT_ID" in result.output
        assert "GOOGLE_CLIENT_SECRET" in result.output
        # SNORE_MCP_BASE_URL was provided — must not appear as missing
        assert "SNORE_MCP_BASE_URL" not in result.output

    def test_empty_string_env_var_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from snore.cli.commands.mcp import mcp

        monkeypatch.setenv("SNORE_MCP_BASE_URL", "")
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "http"])
        assert result.exit_code != 0
        # Empty string counts as missing
        assert "SNORE_MCP_BASE_URL" in result.output

    def test_whitespace_only_env_var_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from snore.cli.commands.mcp import mcp

        monkeypatch.setenv("SNORE_MCP_BASE_URL", "   ")
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "http"])
        assert result.exit_code != 0
        # Whitespace-only counts as missing
        assert "SNORE_MCP_BASE_URL" in result.output


class TestMcpCliHttpPath:
    """http transport with all env vars -> make_auth_provider + make_server + run(http, host, port)."""

    def _invoke_http(
        self,
        monkeypatch: pytest.MonkeyPatch,
        extra_args: list[str] | None = None,
    ) -> tuple[MagicMock, MagicMock, CliRunner]:
        """Set required env vars, patch auth + server, invoke CLI, return mocks + runner result."""
        monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")

        mock_auth_mod = _make_mock_auth_module()
        mock_server = MagicMock()

        from snore.cli.commands.mcp import mcp

        with patch.dict(sys.modules, {"snore.mcp.auth": mock_auth_mod}):
            with patch("snore.mcp.server.make_server", return_value=mock_server):
                runner = CliRunner()
                args = ["--transport", "http"] + (extra_args or [])
                result = runner.invoke(mcp, args)

        return mock_auth_mod, mock_server, result  # type: ignore[return-value]

    def test_http_calls_make_auth_provider_with_correct_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_auth_mod, _, result = self._invoke_http(monkeypatch)
        assert result.exit_code == 0, result.output
        mock_auth_mod.make_auth_provider.assert_called_once_with(
            base_url="https://mcp.example.com",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )

    def test_http_default_host_and_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, mock_server, result = self._invoke_http(monkeypatch)
        assert result.exit_code == 0, result.output
        mock_server.run.assert_called_once_with(
            transport="http", host="127.0.0.1", port=8321
        )

    def test_http_custom_host_and_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, mock_server, result = self._invoke_http(
            monkeypatch, extra_args=["--host", "0.0.0.0", "--port", "9000"]
        )
        assert result.exit_code == 0, result.output
        mock_server.run.assert_called_once_with(
            transport="http", host="0.0.0.0", port=9000
        )

    def test_http_make_server_called_with_auth_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """make_server() must receive the constructed auth provider via the auth= kwarg."""
        monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")

        mock_auth_mod = _make_mock_auth_module()
        mock_server = MagicMock()

        from snore.cli.commands.mcp import mcp

        with patch.dict(sys.modules, {"snore.mcp.auth": mock_auth_mod}):
            with patch(
                "snore.mcp.server.make_server", return_value=mock_server
            ) as mock_make_server:
                runner = CliRunner()
                result = runner.invoke(mcp, ["--transport", "http"])

        assert result.exit_code == 0, result.output
        mock_make_server.assert_called_once_with(
            db_flag=None,
            profile_name="neutral",
            auth=mock_auth_mod.make_auth_provider.return_value,
        )

    def test_auth_provider_construction_failure_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """make_auth_provider ValueError surfaces as a clean UsageError (no traceback)."""
        monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")

        mock_auth_mod = MagicMock()
        mock_auth_mod.make_auth_provider.side_effect = ValueError("bad credentials")

        from snore.cli.commands.mcp import mcp

        with patch.dict(sys.modules, {"snore.mcp.auth": mock_auth_mod}):
            runner = CliRunner()
            result = runner.invoke(mcp, ["--transport", "http"])

        # Server must not start when auth construction fails.
        assert result.exit_code != 0
        # The ValueError text must appear in output (not a raw traceback).
        assert "bad credentials" in result.output
