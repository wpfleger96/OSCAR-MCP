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
    print_error,
    print_footer,
    print_header,
    print_info,
    print_kv,
    print_raw,
    print_separator,
    print_skip,
    print_subsection,
    print_success,
    print_tip,
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

    def test_print_error_routes_to_stderr(self, capture_stderr):
        print_error("broke")
        assert "broke" in capture_stderr.getvalue()
        assert "✗" in capture_stderr.getvalue()

    def test_print_skip_routes_to_stdout(self, capture_stdout):
        print_skip("skipped it")
        assert "skipped it" in capture_stdout.getvalue()
        assert "⊙" in capture_stdout.getvalue()

    def test_print_info_routes_to_stdout(self, capture_stdout):
        print_info("just info")
        assert "just info" in capture_stdout.getvalue()

    def test_print_tip_routes_to_stdout(self, capture_stdout):
        print_tip("helpful hint")
        output = capture_stdout.getvalue()
        assert "Tip:" in output
        assert "helpful hint" in output


class TestIndentation:
    def test_indent_prefix_zero(self):
        assert _indent_prefix(0) == ""

    def test_indent_prefix_one(self):
        assert _indent_prefix(1) == "  "

    def test_indent_prefix_three(self):
        assert _indent_prefix(3) == "      "

    def test_print_success_with_indent(self, capture_stdout):
        print_success("msg", indent=2)
        output = capture_stdout.getvalue()
        assert output.startswith("    ")

    def test_print_warning_with_indent(self, capture_stderr):
        print_warning("msg", indent=1)
        output = capture_stderr.getvalue()
        assert output.startswith("  ")

    def test_print_info_with_indent(self, capture_stdout):
        print_info("msg", indent=2)
        output = capture_stdout.getvalue()
        assert output.startswith("    ")

    def test_print_raw_with_indent(self, capture_stdout):
        print_raw("msg", indent=1)
        output = capture_stdout.getvalue()
        assert output.startswith("  ")


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
    def test_footer_narrow_width(self, capture_stdout):
        print_footer()
        output = capture_stdout.getvalue().strip()
        assert output == "=" * SEP_NARROW

    def test_footer_wide_width(self, capture_stdout):
        print_footer(wide=True)
        output = capture_stdout.getvalue().strip()
        assert output == "=" * SEP_WIDE

    def test_separator_narrow_width(self, capture_stdout):
        print_separator()
        output = capture_stdout.getvalue().strip()
        assert output == "-" * SEP_NARROW

    def test_separator_wide_width(self, capture_stdout):
        print_separator(wide=True)
        output = capture_stdout.getvalue().strip()
        assert output == "-" * SEP_WIDE

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

    def test_header_with_icon(self, capture_stdout):
        print_header("Stats", "ICON")
        output = capture_stdout.getvalue()
        assert "ICON" in output
        assert "Stats" in output

    def test_header_without_icon_no_leading_space(self, capture_stdout):
        print_header("Plain")
        output = capture_stdout.getvalue()
        lines = output.strip().split("\n")
        title_line = lines[0].strip()
        assert title_line == "Plain"


class TestSubsection:
    def test_subsection_prints_title(self, capture_stdout):
        print_subsection("Details")
        output = capture_stdout.getvalue()
        assert "Details" in output


class TestKeyValue:
    def test_kv_contains_key_and_value(self, capture_stdout):
        print_kv("Name", "Alice")
        output = capture_stdout.getvalue()
        assert "Name" in output
        assert "Alice" in output

    def test_kv_default_indent(self, capture_stdout):
        print_kv("Key", "Val")
        output = capture_stdout.getvalue()
        assert output.startswith("  ")

    def test_kv_custom_indent(self, capture_stdout):
        print_kv("Key", "Val", indent=3)
        output = capture_stdout.getvalue()
        assert output.startswith("      ")


class TestDryRun:
    def test_dry_run_header_contains_mode_label(self, capture_stdout):
        print_dry_run_header()
        output = capture_stdout.getvalue()
        assert "DRY RUN MODE" in output

    def test_dry_run_complete_default_verb(self, capture_stdout):
        print_dry_run_complete()
        output = capture_stdout.getvalue()
        assert "Dry run complete" in output

    def test_dry_run_complete_custom_verb(self, capture_stdout):
        print_dry_run_complete("import")
        output = capture_stdout.getvalue()
        assert "Dry run complete" in output
        assert "import" in output
