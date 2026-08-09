"""Unit tests for the mcp CLI command: stdio-only transport."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner


class TestMcpCliStdioPath:
    """stdio is the only transport; make_server() + run(transport='stdio') are called."""

    def test_default_invocation_runs_stdio(self) -> None:
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

    def test_db_and_profile_flags_forwarded(self) -> None:
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

    def test_transport_option_is_gone(self) -> None:
        """--transport is no longer accepted; the CLI must reject it."""
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        result = runner.invoke(mcp, ["--transport", "http"])
        assert result.exit_code != 0

    def test_host_option_is_gone(self) -> None:
        """--host is no longer accepted; the CLI must reject it."""
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        result = runner.invoke(mcp, ["--host", "0.0.0.0"])
        assert result.exit_code != 0

    def test_port_option_is_gone(self) -> None:
        """--port is no longer accepted; the CLI must reject it."""
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        result = runner.invoke(mcp, ["--port", "9999"])
        assert result.exit_code != 0


class TestMcpCliProfileValidation:
    """Unknown profile names produce a clear error."""

    def test_unknown_profile_rejected(self) -> None:
        from snore.cli.commands.mcp import mcp

        runner = CliRunner()
        result = runner.invoke(mcp, ["--profile", "unknown-profile"])
        assert result.exit_code != 0
        assert "Unknown profile" in result.output

    def test_valid_profiles_accepted(self) -> None:
        from snore.cli.commands.mcp import mcp

        for valid_profile in ("neutral", "uars", "osa", "csa"):
            runner = CliRunner()
            mock_server = MagicMock()
            with patch("snore.mcp.server.make_server", return_value=mock_server):
                result = runner.invoke(mcp, ["--profile", valid_profile])
            assert result.exit_code == 0, (
                f"Profile {valid_profile!r} failed: {result.output}"
            )
