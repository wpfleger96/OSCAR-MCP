"""Realistic error paths: bad input must fail cleanly, not crash.

Users mistype paths, query empty databases, and forget required filters. Each
of these should produce a clear message and a sensible exit code (not a
traceback), and these guarantees are easy to break during refactors of the CLI
argument/validation layer.
"""

from __future__ import annotations


def test_import_nonexistent_path_is_usage_error(snore, fresh_db_path):
    result = snore("import", "/nonexistent/snore/path", db=fresh_db_path)
    assert result.returncode == 2  # Click usage error for a bad PATH argument
    assert "does not exist" in (result.stdout + result.stderr)


def test_session_show_missing_id_reports_not_found(snore, empty_db):
    result = snore("session", "show", "999", db=empty_db)
    assert result.returncode == 1
    assert "not found" in (result.stdout + result.stderr).lower()


def test_session_delete_without_filter_errors(snore, imported_db):
    result = snore("session", "delete", db=imported_db)
    assert result.returncode == 1
    assert "at least one filter" in (result.stdout + result.stderr).lower()


def test_stats_on_empty_database_is_graceful(snore, empty_db):
    result = snore("stats", db=empty_db)
    assert result.returncode == 0
    assert "No therapy data found" in result.stdout


def test_session_list_on_empty_database_is_graceful(snore, empty_db):
    result = snore("session", "list", db=empty_db)
    assert result.returncode == 0
    # No traceback leaked to stderr.
    assert "Traceback" not in result.stderr
