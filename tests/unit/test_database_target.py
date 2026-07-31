"""Tests for DatabaseTarget — three-state resolver (§2).

Three states:
- Unrecognised dialect: parse raises ValueError.
- Recognised target: parse succeeds; resolution capability-gates PostgreSQL.
- Driver installed / operation supported: SQLite resolution returns a valid URL.

Also covers the full precedence chain, driver-qualified input stripping, and
the ``serve --db`` env-collision scenario.
"""

from __future__ import annotations

import pytest

from snore.database.target import DatabaseTarget


class TestDatabaseTargetParsing:
    """Parsing is dialect-complete: sqlite and postgresql recognised; unknown dialects error."""

    def test_parse_unrecognised_dialect_raises_error(self):
        """Unrecognised dialect raises ValueError at parse time."""
        with pytest.raises(ValueError, match="Unrecognised database dialect"):
            DatabaseTarget.from_url("mysql://localhost/db")

    def test_parse_sqlite_url_succeeds(self):
        """sqlite:/// URL is recognised and parses successfully."""
        target = DatabaseTarget.from_url("sqlite:///path/to/db.sqlite")
        assert target.dialect == "sqlite"
        assert target.is_sqlite

    def test_parse_postgresql_url_succeeds(self):
        """postgresql:// URL is recognised at parse time."""
        target = DatabaseTarget.from_url("postgresql://localhost/snore")
        assert target.dialect == "postgresql"
        assert not target.is_sqlite

    def test_parse_bare_path_treated_as_sqlite(self):
        """A bare file path without a scheme is treated as sqlite."""
        target = DatabaseTarget.from_url("/tmp/test.db")
        assert target.dialect == "sqlite"

    def test_parse_driver_qualified_sqlite_strips_driver(self):
        """sqlite+pysqlite:// is stripped to sqlite and the URL is normalised."""
        target = DatabaseTarget.from_url("sqlite+pysqlite:///path/to/db.db")
        assert target.dialect == "sqlite"

    def test_parse_driver_qualified_postgresql_strips_driver(self):
        """postgresql+psycopg:// is stripped to postgresql at parse time."""
        target = DatabaseTarget.from_url("postgresql+psycopg://localhost/snore")
        assert target.dialect == "postgresql"


class TestDatabaseTargetResolution:
    """Resolution is capability-gated: sqlite resolves; postgresql is blocked."""

    def test_sqlite_sync_url_resolves_successfully(self):
        """sqlite target produces a valid sync SQLAlchemy URL."""
        target = DatabaseTarget.from_url("sqlite:///test.db")
        url = target.resolve_sync_url()
        assert url.startswith("sqlite+pysqlite:///")
        assert "test.db" in url

    def test_sqlite_migration_url_resolves_successfully(self):
        """sqlite target produces a valid migration URL."""
        target = DatabaseTarget.from_url("sqlite:///test.db")
        url = target.resolve_migration_url()
        assert url.startswith("sqlite+pysqlite:///")

    def test_postgresql_sync_url_capability_gated(self):
        """postgresql target raises a sanitised RuntimeError — no credentials in message."""
        target = DatabaseTarget.from_url("postgresql://user:secret@localhost/db")
        with pytest.raises(RuntimeError) as exc_info:
            target.resolve_sync_url()
        error_text = str(exc_info.value)
        assert "PostgreSQL support requires a driver" in error_text
        # Credentials must not appear in the error message.
        assert "secret" not in error_text
        assert "user" not in error_text

    def test_postgresql_migration_url_capability_gated(self):
        """postgresql migration URL also raises a sanitised RuntimeError."""
        target = DatabaseTarget.from_url("postgresql://localhost/snore")
        with pytest.raises(RuntimeError, match="PostgreSQL support requires a driver"):
            target.resolve_migration_url()


class TestDatabaseTargetPrecedenceChain:
    """Precedence: --db > SNORE_DATABASE_URL > SNORE_DB_PATH > default."""

    def test_db_flag_wins_over_env_vars(self, monkeypatch, tmp_path):
        """--db flag wins over both env vars; warnings are logged for ignored inputs."""
        monkeypatch.setenv("SNORE_DATABASE_URL", "sqlite:///env_url.db")
        monkeypatch.setenv("SNORE_DB_PATH", "/some/path.db")

        db_path = str(tmp_path / "flag.db")
        target = DatabaseTarget.from_env_and_flags(db_flag=db_path, warn_ignored=False)
        assert "flag.db" in target.location

    def test_database_url_wins_over_db_path(self, monkeypatch):
        """SNORE_DATABASE_URL wins over SNORE_DB_PATH."""
        monkeypatch.setenv("SNORE_DATABASE_URL", "sqlite:///url_wins.db")
        monkeypatch.setenv("SNORE_DB_PATH", "/ignored.db")
        monkeypatch.delenv("SNORE_DATABASE_URL", raising=False)
        monkeypatch.setenv("SNORE_DATABASE_URL", "sqlite:///url_wins.db")

        target = DatabaseTarget.from_env_and_flags(warn_ignored=False)
        assert "url_wins.db" in target.raw_url

    def test_db_path_used_when_no_url(self, monkeypatch):
        """SNORE_DB_PATH is used when SNORE_DATABASE_URL is not set."""
        monkeypatch.delenv("SNORE_DATABASE_URL", raising=False)
        monkeypatch.setenv("SNORE_DB_PATH", "/my/path.db")

        target = DatabaseTarget.from_env_and_flags(warn_ignored=False)
        assert "path.db" in target.location

    def test_default_path_used_when_no_inputs(self, monkeypatch):
        """Default SQLite path is used when no flags or env vars are set."""
        monkeypatch.delenv("SNORE_DATABASE_URL", raising=False)
        monkeypatch.delenv("SNORE_DB_PATH", raising=False)

        target = DatabaseTarget.from_env_and_flags(warn_ignored=False)
        assert target.dialect == "sqlite"

    def test_serve_db_flag_with_both_env_vars_preloads_child_with_canonical_url(
        self, monkeypatch
    ):
        """serve --db scenario: --db wins over both env vars; canonical URL is exported.

        Simulates a parent process that has SNORE_DATABASE_URL and SNORE_DB_PATH
        inherited from the environment. The --db flag must override both.
        """
        monkeypatch.setenv("SNORE_DATABASE_URL", "sqlite:///inherited_url.db")
        monkeypatch.setenv("SNORE_DB_PATH", "/inherited/path.db")

        db_flag_path = "/explicit/db.db"
        target = DatabaseTarget.from_env_and_flags(
            db_flag=db_flag_path, warn_ignored=True
        )
        canonical_url = target.resolve_sync_url()

        # The resolved URL points to the --db flag value, not the env vars.
        assert "db.db" in canonical_url
        assert "inherited" not in canonical_url
