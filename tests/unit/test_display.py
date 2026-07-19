"""Unit tests for cli/display helpers."""

from io import StringIO
from unittest.mock import patch

import pytest

from rich.console import Console

from snore.cli.display import (
    SEP_NARROW,
    SEP_WIDE,
    _indent_prefix,
    print_dry_run_complete,
    print_dry_run_header,
    print_header,
    print_kv,
    print_raw,
    print_success,
    print_warning,
)


@pytest.fixture()
def capture_stdout():
    buf = StringIO()
    # force_terminal=True so Rich renders markup and icons (tests check plain-text substrings)
    test_console = Console(file=buf, force_terminal=True, width=120)
    with patch("snore.cli.display.console", test_console):
        yield buf


@pytest.fixture()
def capture_stderr():
    buf = StringIO()
    # force_terminal=True so Rich renders markup and icons (tests check plain-text substrings)
    test_console = Console(file=buf, stderr=True, force_terminal=True, width=120)
    with patch("snore.cli.display.err_console", test_console):
        yield buf


class TestConsoleRouting:
    def test_print_success_routes_to_stdout(self, capture_stdout):
        print_success("done")
        assert "done" in capture_stdout.getvalue()
        assert "✓" in capture_stdout.getvalue()

    def test_print_warning_routes_to_stderr(self, capture_stderr):
        print_warning("watch out")
        assert "watch out" in capture_stderr.getvalue()
        assert "⚠" in capture_stderr.getvalue()


class TestIndentation:
    def test_indent_prefix_one(self):
        assert _indent_prefix(1) == "  "

    def test_print_success_with_indent(self, capture_stdout):
        print_success("msg", indent=2)
        output = capture_stdout.getvalue()
        assert output.startswith("    ")


class TestPrintRaw:
    def test_brackets_not_parsed_as_markup(self, capture_stdout):
        print_raw("[disabled] session")
        output = capture_stdout.getvalue()
        assert "[disabled]" in output

    def test_rich_markup_not_applied(self, capture_stdout):
        print_raw("[bold]not bold[/bold]")
        output = capture_stdout.getvalue()
        assert "[bold]" in output
        assert "[/bold]" in output


class TestSeparators:
    def test_header_contains_title_and_separator(self, capture_stdout):
        print_header("My Title")
        output = capture_stdout.getvalue()
        assert "My Title" in output
        assert "=" * SEP_NARROW in output

    def test_header_wide_uses_wide_separator(self, capture_stdout):
        print_header("Wide Title", wide=True)
        output = capture_stdout.getvalue()
        assert "Wide Title" in output
        assert "=" * SEP_WIDE in output


class TestKeyValue:
    def test_kv_contains_key_and_value(self, capture_stdout):
        print_kv("Name", "Alice")
        output = capture_stdout.getvalue()
        assert "Name" in output
        assert "Alice" in output


class TestDryRun:
    def test_dry_run_header_contains_mode_label(self, capture_stdout):
        print_dry_run_header()
        output = capture_stdout.getvalue()
        assert "DRY RUN MODE" in output

    def test_dry_run_complete_custom_verb(self, capture_stdout):
        print_dry_run_complete("import")
        output = capture_stdout.getvalue()
        assert "Dry run complete" in output
        assert "import" in output
