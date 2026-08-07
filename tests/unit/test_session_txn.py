"""Unit tests for session.py and txn.py DB-layer improvements.

Covers:
- PRAGMA journal_size_limit=67108864 is applied on every new connection.
- session_scope(immediate=True) emits BEGIN IMMEDIATE; the default emits BEGIN.
- run_txn uses BEGIN IMMEDIATE (via session_scope(immediate=True)).
- _apply_migrations_sync skips Alembic when the schema is already at head.
- _apply_migrations_sync falls through to normal migrations on any check failure.
- is_sqlite_contention is public; _is_sqlite_contention is a backward-compat alias.
- Module-level constants (MAX_ATTEMPTS, BASE_DELAY_SECONDS, MAX_DELAY_SECONDS) remain
  importable at module scope.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Public API / constants
# ---------------------------------------------------------------------------


class TestPublicExports:
    """is_sqlite_contention and the retry constants are importable by external callers."""

    def test_is_sqlite_contention_detects_sqlite_busy(self):
        """is_sqlite_contention returns True for SQLITE_BUSY messages."""
        from snore.database.txn import is_sqlite_contention

        assert is_sqlite_contention(RuntimeError("SQLITE_BUSY: database is locked"))
        assert is_sqlite_contention(
            RuntimeError("(sqlite3.OperationalError) database is locked")
        )
        assert is_sqlite_contention(RuntimeError("SQLITE_LOCKED"))

    def test_is_sqlite_contention_returns_false_for_non_contention(self):
        """is_sqlite_contention returns False for unrelated errors."""
        from snore.database.txn import is_sqlite_contention

        assert not is_sqlite_contention(ValueError("unique constraint violated"))
        assert not is_sqlite_contention(RuntimeError("some other error"))

    def test_private_alias_is_same_function(self):
        """_is_sqlite_contention is the exact same object as is_sqlite_contention."""
        from snore.database.txn import _is_sqlite_contention, is_sqlite_contention

        # The alias must be the same function object so existing patches against
        # snore.database.txn._is_sqlite_contention continue to intercept calls.
        assert _is_sqlite_contention is is_sqlite_contention

    def test_retry_constants_are_public(self):
        """MAX_ATTEMPTS, BASE_DELAY_SECONDS, MAX_DELAY_SECONDS exist at module scope."""
        from snore.database import txn

        assert txn.MAX_ATTEMPTS == 5
        assert isinstance(txn.BASE_DELAY_SECONDS, float)
        assert isinstance(txn.MAX_DELAY_SECONDS, float)
        assert txn.BASE_DELAY_SECONDS > 0
        assert txn.MAX_DELAY_SECONDS >= txn.BASE_DELAY_SECONDS

    def test_backoff_delay_is_public_and_bounded(self):
        """backoff_delay is importable and respects the max-delay cap."""
        from snore.database.txn import (
            MAX_DELAY_SECONDS,
            backoff_delay,
        )

        # For any attempt, the returned delay must not exceed the cap.
        for attempt in range(1, 20):
            delay = backoff_delay(attempt)
            assert delay <= MAX_DELAY_SECONDS, (
                f"backoff_delay({attempt}) = {delay} exceeds MAX_DELAY_SECONDS={MAX_DELAY_SECONDS}"
            )
            assert delay >= 0


# ---------------------------------------------------------------------------
# PRAGMA journal_size_limit
# ---------------------------------------------------------------------------


class TestJournalSizeLimit:
    """journal_size_limit=67108864 is applied to every new DBAPI connection."""

    async def test_journal_size_limit_set_on_new_connection(self, temp_db):
        """PRAGMA journal_size_limit reads back as 67108864 (64 MB) after init."""
        from sqlalchemy import text

        from snore.database.session import (
            cleanup_database,
            init_database,
            session_scope,
        )

        # Arrange: initialize DB so the engine+pragmas are registered.
        await init_database(str(temp_db))

        try:
            # Act: query the pragma on a live pooled connection.
            async with session_scope() as session:
                result = (
                    await session.execute(text("PRAGMA journal_size_limit"))
                ).scalar()
        finally:
            await cleanup_database()

        # Assert: value matches the 64 MB limit set in _register_sqlite_pragmas.
        assert result == 67108864, (
            f"Expected journal_size_limit=67108864 (64 MB), got {result}"
        )


# ---------------------------------------------------------------------------
# session_scope(immediate=True) — BEGIN IMMEDIATE
# ---------------------------------------------------------------------------


class TestSessionScopeImmediate:
    """session_scope() emits the correct BEGIN variant based on the immediate flag."""

    async def test_immediate_true_emits_begin_immediate(self, temp_db):
        """BEGIN IMMEDIATE is sent to the DBAPI when immediate=True.

        The ``begin`` event listener reads the ``sqlite_txn_mode`` execution option
        set by ``session_scope(immediate=True)`` via ``session.connection()``.
        ``before_cursor_execute`` fires for all raw cursor executions — including
        ``exec_driver_sql`` calls — so we can assert the exact statement without
        mocking SQLite internals.
        """
        from sqlalchemy import event

        from snore.database.session import (
            cleanup_database,
            get_engine,
            init_database,
            session_scope,
        )

        # Arrange.
        await init_database(str(temp_db))
        engine = get_engine()
        captured: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def capture_sql(conn, cursor, statement, params, context, executemany):
            captured.append(statement)

        try:
            # Act: open a session with immediate=True.
            # session.connection() forces connection checkout so the "begin" event
            # fires — and with it, our emit_begin listener — before any SQL.
            async with session_scope(immediate=True):
                pass
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_sql)
            await cleanup_database()

        # Assert: at least one BEGIN IMMEDIATE was sent.
        assert any("BEGIN IMMEDIATE" in s for s in captured), (
            f"Expected BEGIN IMMEDIATE in DBAPI statements; got: {captured}"
        )

    async def test_immediate_false_emits_plain_begin(self, temp_db):
        """Plain BEGIN (not IMMEDIATE) is sent to the DBAPI by default.

        The default ``session_scope()`` should not acquire the write lock upfront —
        read-heavy and short-write callers use plain BEGIN so they don't serialise
        unnecessarily against concurrent writers.
        """
        from sqlalchemy import event, text

        from snore.database.session import (
            cleanup_database,
            get_engine,
            init_database,
            session_scope,
        )

        # Arrange.
        await init_database(str(temp_db))
        engine = get_engine()
        captured: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def capture_sql(conn, cursor, statement, params, context, executemany):
            captured.append(statement)

        try:
            # Act: open a session with the default (immediate=False).
            # Execute a SELECT to force connection checkout — without it the DBAPI
            # connection is acquired lazily (only at commit/rollback) and BEGIN may
            # not be emitted until after the listener is removed.
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_sql)
            await cleanup_database()

        # Assert: a plain BEGIN was sent but not BEGIN IMMEDIATE.
        begin_stmts = [s for s in captured if "BEGIN" in s.upper()]
        assert begin_stmts, f"Expected at least one BEGIN statement; got: {captured}"
        assert not any("IMMEDIATE" in s for s in begin_stmts), (
            f"Unexpected IMMEDIATE in default mode statements: {begin_stmts}"
        )


# ---------------------------------------------------------------------------
# run_txn uses BEGIN IMMEDIATE
# ---------------------------------------------------------------------------


class TestRunTxnUsesImmediate:
    """run_txn delegates to session_scope(immediate=True), so it emits BEGIN IMMEDIATE."""

    async def test_run_txn_emits_begin_immediate(self, temp_db):
        """run_txn sends BEGIN IMMEDIATE to the DBAPI for each attempt.

        run_txn is only used for write units of work (import chunks, invite
        redemptions).  Using BEGIN IMMEDIATE means write-lock contention queues
        on busy_timeout rather than failing instantly on a WAL snapshot-upgrade
        conflict.  We verify the actual DBAPI statement rather than the call
        signature so the test is resilient to refactoring inside run_txn.
        """
        from sqlalchemy import event, text

        from snore.database.session import cleanup_database, get_engine, init_database
        from snore.database.txn import run_txn

        # Arrange.
        await init_database(str(temp_db))
        engine = get_engine()
        captured: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def capture_sql(conn, cursor, statement, params, context, executemany):
            captured.append(statement)

        try:
            # Act: run a unit of work that executes SQL so the DBAPI connection is
            # checked out and the begin event fires while the listener is active.
            async def noop(db):
                await db.execute(text("SELECT 1"))
                return True

            result = await run_txn(noop)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_sql)
            await cleanup_database()

        assert result is True
        assert any("BEGIN IMMEDIATE" in s for s in captured), (
            f"Expected BEGIN IMMEDIATE from run_txn; got: {captured}"
        )


# ---------------------------------------------------------------------------
# Empty migration chain — schema via create_all
# ---------------------------------------------------------------------------


class TestEmptyChainSchemaCreation:
    """_apply_migrations_sync uses create_all when versions/ contains no migration files.

    Each test mocks ScriptDirectory.from_config to return [] heads, so the
    empty-chain path is exercised regardless of what files exist on disk.
    """

    def test_alembic_commands_never_invoked(self, tmp_path):
        """Alembic upgrade/stamp are NOT called in zero-migration mode.

        With get_heads() returning [], the function takes the create_all
        early-exit path before any Alembic command is reached.
        """
        import os

        from unittest.mock import MagicMock, patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "empty_chain.db")
        sync_url = f"sqlite:///{db_path}"

        mock_script = MagicMock()
        mock_script.get_heads.return_value = []

        with (
            patch(
                "alembic.script.ScriptDirectory.from_config", return_value=mock_script
            ),
            patch("snore.database.session.alembic_command") as mock_alembic,
        ):
            _apply_migrations_sync(sync_url)

        mock_alembic.upgrade.assert_not_called()
        mock_alembic.stamp.assert_not_called()
        assert os.path.exists(db_path), "DB file must be created by create_all"

    def test_fresh_db_gets_schema_no_alembic_version(self, tmp_path):
        """Fresh/missing DB file → create_all creates schema; alembic_version absent.

        Zero-migration mode never writes alembic_version, so a brand-new
        database must have application tables but no alembic_version table.
        """
        import sqlite3

        from unittest.mock import MagicMock, patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "fresh.db")
        sync_url = f"sqlite:///{db_path}"

        mock_script = MagicMock()
        mock_script.get_heads.return_value = []

        with patch(
            "alembic.script.ScriptDirectory.from_config", return_value=mock_script
        ):
            _apply_migrations_sync(sync_url)

        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        assert "sessions" in tables, "sessions table must exist after create_all"
        assert "statistics" in tables, "statistics table must exist after create_all"
        assert "alembic_version" not in tables, (
            "alembic_version must NOT be created in zero-migration mode"
        )

    def test_existing_db_idempotent_no_alembic_version(self, tmp_path):
        """Existing DB with schema → second create_all(checkfirst=True) is a no-op.

        Running _apply_migrations_sync twice on the same database must not
        raise, must leave tables intact, and must still not write alembic_version.
        """
        import sqlite3

        from unittest.mock import MagicMock, patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "existing.db")
        sync_url = f"sqlite:///{db_path}"

        mock_script = MagicMock()
        mock_script.get_heads.return_value = []

        with patch(
            "alembic.script.ScriptDirectory.from_config", return_value=mock_script
        ):
            _apply_migrations_sync(sync_url)
            _apply_migrations_sync(sync_url)  # second call must not raise

        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        assert "sessions" in tables
        assert "alembic_version" not in tables

    def test_stale_alembic_version_row_does_not_cause_failure(self, tmp_path):
        """DB with a stale alembic_version row → _apply_migrations_sync succeeds.

        Zero-migration mode ignores alembic_version entirely, so a leftover
        stamp from a pre-flatten database must not cause any exception.
        """
        import sqlite3

        from unittest.mock import MagicMock, patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "stale_stamp.db")
        sync_url = f"sqlite:///{db_path}"

        mock_script = MagicMock()
        mock_script.get_heads.return_value = []

        # Arrange: create schema via first call, then manually add a stale stamp.
        with patch(
            "alembic.script.ScriptDirectory.from_config", return_value=mock_script
        ):
            _apply_migrations_sync(sync_url)

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('deadbeef0000')")
        conn.commit()
        conn.close()

        # Act: second call must succeed and must not invoke alembic upgrade.
        # The stale stamp is read but heads=[] so we enter empty-chain mode.
        with (
            patch(
                "alembic.script.ScriptDirectory.from_config", return_value=mock_script
            ),
            patch("snore.database.session.alembic_command") as mock_alembic,
        ):
            _apply_migrations_sync(sync_url)

        mock_alembic.upgrade.assert_not_called()
        mock_alembic.stamp.assert_not_called()


# ---------------------------------------------------------------------------
# Non-empty migration chain paths
# ---------------------------------------------------------------------------


class TestNonEmptyChainPaths:
    """_apply_migrations_sync with a non-empty migration chain (mocked ScriptDirectory).

    ScriptDirectory.from_config is patched so tests are independent of the
    actual contents of versions/ on disk.
    """

    @staticmethod
    def _fake_heads(heads: list[str]) -> Any:
        """Return a context manager that patches ScriptDirectory.from_config."""
        from unittest.mock import MagicMock, patch

        mock_script = MagicMock()
        mock_script.get_heads.return_value = heads
        return patch(
            "alembic.script.ScriptDirectory.from_config", return_value=mock_script
        )

    def test_at_head_skips_all_alembic(self, tmp_path):
        """DB stamped at the current head → neither stamp nor upgrade is called."""
        import sqlite3

        from unittest.mock import patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "at_head.db")
        sync_url = f"sqlite:///{db_path}"

        # Pre-create DB with only an alembic_version table at the fake head.
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('fakehead0001')")
        conn.commit()
        conn.close()

        with (
            self._fake_heads(["fakehead0001"]),
            patch("snore.database.session.alembic_command") as mock_alembic,
        ):
            _apply_migrations_sync(sync_url)

        mock_alembic.stamp.assert_not_called()
        mock_alembic.upgrade.assert_not_called()

    def test_stale_version_triggers_upgrade(self, tmp_path):
        """DB with a stale stamp → upgrade called with 'head'; stamp not called."""
        import sqlite3

        from unittest.mock import ANY, patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "stale.db")
        sync_url = f"sqlite:///{db_path}"

        # DB has a sessions table and alembic_version row at an old revision.
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('oldrev0000')")
        conn.commit()
        conn.close()

        with (
            self._fake_heads(["fakehead0001"]),
            patch("snore.database.session.alembic_command") as mock_alembic,
        ):
            _apply_migrations_sync(sync_url)

        mock_alembic.upgrade.assert_called_once_with(ANY, "head")
        mock_alembic.stamp.assert_not_called()

    def test_fresh_db_created_and_stamped(self, tmp_path):
        """Nonexistent DB → application tables created and stamp called with 'head'."""
        import sqlite3

        from unittest.mock import ANY, patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "fresh_nonexistent.db")
        sync_url = f"sqlite:///{db_path}"
        # Do not create the file — it must not exist before the call.

        with (
            self._fake_heads(["fakehead0001"]),
            patch("snore.database.session.alembic_command") as mock_alembic,
        ):
            _apply_migrations_sync(sync_url)

        mock_alembic.stamp.assert_called_once_with(ANY, "head")
        mock_alembic.upgrade.assert_not_called()

        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "sessions" in tables, "sessions table must exist after create_all"

    def test_unstamped_existing_db_raises_actionable_error(self, tmp_path):
        """DB with sessions but no alembic_version → RuntimeError with clear message."""
        import sqlite3

        from unittest.mock import patch

        import pytest

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "unstamped.db")
        sync_url = f"sqlite:///{db_path}"

        # DB has application tables but was never stamped by Alembic.
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        with (
            self._fake_heads(["fakehead0001"]),
            patch("snore.database.session.alembic_command") as mock_alembic,
        ):
            with pytest.raises(RuntimeError, match="zero-migration mode"):
                _apply_migrations_sync(sync_url)

        mock_alembic.upgrade.assert_not_called()
