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
# Skip migrations when schema is at head
# ---------------------------------------------------------------------------


class TestSkipMigrationsAtHead:
    """_apply_migrations_sync skips Alembic machinery when the DB is already at head."""

    def test_skips_alembic_when_schema_is_at_head(self, tmp_path):
        """Alembic upgrade/stamp are NOT called when DB is already at head.

        The read-only sqlite3.connect check compares the stored ``alembic_version``
        against the current script head(s).  On match, the function returns before
        creating the ephemeral sync engine, which is the write operation we want to
        skip when ``snore mcp`` starts against a live server's database.
        """
        from unittest.mock import patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "already_at_head.db")
        sync_url = f"sqlite:///{db_path}"

        # Arrange: run migrations once so the DB exists at the current head.
        _apply_migrations_sync(sync_url)

        # Act: run again and capture any Alembic calls.
        with patch("snore.database.session.alembic_command") as mock_alembic:
            _apply_migrations_sync(sync_url)

        # Assert: neither upgrade nor stamp should have been called because the
        # schema was already current.
        mock_alembic.upgrade.assert_not_called()
        mock_alembic.stamp.assert_not_called()

    def test_falls_through_when_db_file_missing(self, tmp_path):
        """Missing DB file → read-only open fails → migrations run normally.

        The OperationalError from sqlite3.connect(mode=ro) on a non-existent file
        is caught and the code falls through to the full migration path, which
        creates the database from scratch.
        """
        import os

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "nonexistent.db")
        sync_url = f"sqlite:///{db_path}"

        # Act: call on a path that does not exist yet.
        # The fast-path read-only open raises OperationalError and is caught,
        # so the normal migration path runs and creates the file.
        _apply_migrations_sync(sync_url)

        assert os.path.exists(db_path), "DB file must be created when it did not exist"

    def test_falls_through_when_alembic_version_table_missing(self, tmp_path):
        """No alembic_version table → fast-path exception → migrations run normally.

        If the DB exists but ``alembic_version`` was never stamped (e.g. a raw
        schema created without Alembic), the fast path gets an OperationalError
        when querying the table.  The exception is caught and the normal upgrade
        path runs instead.
        """
        import sqlite3

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "no_alembic_table.db")
        sync_url = f"sqlite:///{db_path}"

        # Arrange: create the file without alembic_version.
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        # Act: call _apply_migrations_sync on a DB with no alembic_version table.
        # The fast-path SELECT raises OperationalError → falls through to normal path.
        _apply_migrations_sync(sync_url)

        # Assert: after migration, alembic_version must exist at head.
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        conn.close()

        assert row is not None, "alembic_version must be stamped after fallthrough"

    def test_falls_through_when_version_does_not_match_head(self, tmp_path):
        """Outdated version_num → fast-path skips; upgrade runs normally.

        If the DB has an ``alembic_version`` row but it does not match the current
        head(s) (e.g. a pre-migration schema), the fast path detects the mismatch
        and falls through to ``alembic_command.upgrade``.
        """
        import sqlite3

        from unittest.mock import patch

        from snore.database.session import _apply_migrations_sync

        db_path = str(tmp_path / "old_version.db")
        sync_url = f"sqlite:///{db_path}"

        # Arrange: create the DB at head, then overwrite version with a fake old one.
        _apply_migrations_sync(sync_url)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE alembic_version SET version_num='deadbeef0000000000000000000000000'"
        )
        conn.commit()
        conn.close()

        # Act: run with a fake old version — fast path should NOT skip because
        # the stored version does not match the current Alembic head(s).
        with patch("snore.database.session.alembic_command") as mock_alembic:
            _apply_migrations_sync(sync_url)

        # Assert: upgrade was called (not skipped) because version didn't match head.
        mock_alembic.upgrade.assert_called_once()
