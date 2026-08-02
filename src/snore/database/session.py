"""Database session management for SNORE.

Async SQLite transaction recipe (§4, PR-2)
-------------------------------------------
The async engine uses ``aiosqlite`` as the DBAPI.

**PRAGMA setup (``"connect"`` event):**
The ``"connect"`` event fires on every new DBAPI connection.  aiosqlite wraps
the raw ``sqlite3.Connection``; we access it via
``dbapi_conn.driver_connection._conn``.  We set ``isolation_level = None``
(autocommit) *permanently* on the raw connection so PRAGMAs like
``journal_mode=WAL`` can run outside a transaction.  The connection stays in
autocommit mode — SQLAlchemy's logical transaction control takes over via the
``"begin"`` event (see below).

**Transaction control (``"begin"`` event):**
With aiosqlite in autocommit mode, SQLAlchemy's logical ``BEGIN`` never emits
an actual ``BEGIN`` statement to SQLite.  That means a released savepoint
(``RELEASE SAVEPOINT sp``) escapes the outer rollback — the row is committed to
the file before the outer ``ROLLBACK`` fires.

To fix this, we attach a ``"begin"`` event listener to
``async_engine.sync_engine`` that executes an explicit ``BEGIN`` statement
whenever SQLAlchemy starts a new logical transaction.  This restores the
expected two-layer semantics: SQLite's outer ``BEGIN`` contains all
``SAVEPOINT`` / ``RELEASE`` / ``ROLLBACK TO`` operations so that an outer
``ROLLBACK`` always undoes any released savepoints.

VACUUM uses a separate sync AUTOCOMMIT connection and is unaffected.

**Migrations:**
Alembic stays fully synchronous and uses the pysqlite URL.  The async
``init_database`` / ``init_database_from_url`` entry points run
``_apply_migrations_sync`` via ``asyncio.to_thread`` so they never block the
event loop.

expire_on_commit=False is set on all sessions so that ORM attributes remain
accessible after a commit without triggering implicit I/O — required for
async contexts where lazy loads raise MissingGreenlet.
"""

import asyncio
import logging
import os

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from snore.constants import DEFAULT_DATABASE_PATH
from snore.database.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_AsyncSessionFactory: async_sessionmaker[AsyncSession] | None = None
_db_path: str | None = None

# Once-task coordination: concurrent callers await the SAME in-flight
# initialization task via asyncio.shield().  Published engine/factory only
# after migration success; disposed and cleared atomically on cancellation or
# failure so a retry re-runs migrations from a clean state.
# Protected by a stable asyncio.Lock (never replaced so no two lock domains
# can overlap).
_init_lock: asyncio.Lock | None = None  # Created lazily inside an event loop.
_init_task: asyncio.Task[None] | None = None  # The in-flight once-task.


def _get_init_lock() -> asyncio.Lock:
    """Return the module-level asyncio.Lock, creating it on first call.

    Must be called from within a running event loop.  The lock is never
    replaced once created so all callers always share the same domain.
    """
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


def _build_alembic_config(database_url: str) -> AlembicConfig:
    migrations_dir = str(Path(__file__).parent / "migrations")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", migrations_dir)
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _apply_migrations_sync(sync_url: str) -> None:
    """Run Alembic migrations synchronously.

    Called from within ``asyncio.to_thread`` so it never blocks the event loop.
    Uses the sync pysqlite URL for Alembic — identical to PR-1.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415

    engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    table_names = set(inspect(engine).get_table_names())
    alembic_cfg = _build_alembic_config(sync_url)

    if "sessions" not in table_names:
        Base.metadata.create_all(engine)
        alembic_command.stamp(alembic_cfg, "head")
        logger.info("Fresh database created and stamped at head")
    else:
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied (upgrade to head)")

    engine.dispose()


def _register_sqlite_pragmas_on_async_engine(async_engine: AsyncEngine) -> None:
    """Attach the integrated SQLite connection recipe to *async_engine*.

    Two event listeners are registered on ``async_engine.sync_engine``:

    **"connect" — PRAGMA setup:**
    Fires on every new DBAPI connection.  For aiosqlite connections,
    ``dbapi_conn.driver_connection._conn`` is the raw ``sqlite3.Connection``.
    We set ``isolation_level = None`` (autocommit) *permanently* so that
    PRAGMAs like ``journal_mode=WAL`` run outside a transaction and so that
    SQLAlchemy's logical transaction control is the sole source of truth.
    The connection is left in autocommit mode after the event returns.

    **"begin" — explicit BEGIN:**
    With the DBAPI in autocommit mode, SQLAlchemy's logical transaction does
    not emit a real ``BEGIN`` to SQLite.  Without an explicit ``BEGIN``, a
    released savepoint (``RELEASE SAVEPOINT sp``) writes directly to the
    database file and cannot be undone by a later outer rollback.

    The ``"begin"`` listener executes ``BEGIN`` via a raw cursor whenever
    SQLAlchemy opens a new logical transaction.  This re-establishes the
    two-layer semantics: SQLite's outer ``BEGIN`` contains all
    ``SAVEPOINT`` / ``RELEASE`` / ``ROLLBACK TO`` operations, so an outer
    rollback undoes every released savepoint within it.
    """
    from sqlalchemy import event  # noqa: PLC0415

    @event.listens_for(async_engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_conn: Any, connection_record: Any) -> None:
        # aiosqlite wraps the raw connection; unwrap to access isolation_level.
        raw_conn = getattr(
            getattr(dbapi_conn, "driver_connection", None), "_conn", None
        )
        if raw_conn is None:
            # Fallback: try direct raw connection (sync pysqlite path).
            raw_conn = dbapi_conn

        # Set autocommit permanently — the "begin" listener emits explicit BEGIN.
        raw_conn.isolation_level = None

        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()

    @event.listens_for(async_engine.sync_engine, "begin")
    def emit_begin(conn: Any) -> None:
        # With DBAPI in autocommit mode, emit an explicit BEGIN so that SQLite's
        # outer transaction wraps all SAVEPOINTs and outer ROLLBACK works correctly.
        conn.exec_driver_sql("BEGIN")


async def _do_init(sync_url: str, async_url: str, db_path: str | None) -> None:
    """Build the engine, run migrations, then publish globals atomically.

    Called exactly once per initialization lifecycle.  On failure or
    cancellation, disposes any partially-built engine and clears all globals
    (including ``_init_task``) so a retry starts fresh.

    ``CancelledError`` is caught as ``BaseException`` so a cancelled driver
    tears down cleanly and lets the next caller retry rather than hanging.
    """
    global _engine, _AsyncSessionFactory, _db_path

    engine: AsyncEngine | None = None
    try:
        if db_path and db_path != ":memory:":
            db_dir = os.path.dirname(db_path)
            if db_dir:
                try:
                    os.makedirs(db_dir, exist_ok=True)
                except PermissionError as e:
                    raise PermissionError(
                        f"Cannot create database directory {db_dir}: {e}"
                    ) from e

        engine = create_async_engine(
            async_url,
            echo=False,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        from sqlalchemy.engine import make_url as _make_url  # noqa: PLC0415

        dialect = _make_url(async_url).get_backend_name()
        if dialect == "sqlite":
            _register_sqlite_pragmas_on_async_engine(engine)

        # Run migrations off the event loop — never blocks uvicorn.
        await asyncio.to_thread(_apply_migrations_sync, sync_url)

        # Publish only after successful migration.
        _db_path = db_path
        _engine = engine
        _AsyncSessionFactory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    except BaseException:
        # Atomic teardown on any failure including CancelledError.
        # Use synchronous disposal only — awaiting inside an except-BaseException
        # block after catching CancelledError can re-raise CancelledError at the
        # next await in Python 3.11+ cancellation semantics.
        # _init_task does NOT need explicit clearing here: _init_with_once_task
        # checks ``_init_task.done()`` under the lock, which returns True once
        # this task is cancelled or failed — so retries always create a fresh task.
        if engine is not None:
            engine.sync_engine.dispose()
        _engine = None
        _AsyncSessionFactory = None
        _db_path = None
        raise


async def _init_with_once_task(
    sync_url: str, async_url: str, db_path: str | None
) -> None:
    """Coordinate initialization via a shared once-task.

    The first caller creates an ``asyncio.Task`` that runs ``_do_init`` and
    stores it in ``_init_task``.  Every subsequent concurrent caller awaits the
    SAME task through ``asyncio.shield()`` so cancelling a waiter never cancels
    the shared work.

    Cancellation semantics (cancel-cleans-up-and-retries-fresh):
    - Cancelling a **waiter** raises ``CancelledError`` in the waiter only; the
      shared task keeps running and eventually publishes globals for any other
      concurrent caller.
    - Cancelling the **driver** (the task itself) causes ``_do_init`` to catch
      ``BaseException``, tear down, and clear ``_init_task`` atomically, so
      the next ``init_database`` call creates a fresh task and retries.
    """
    global _init_task

    lock = _get_init_lock()
    async with lock:
        if _engine is not None and _AsyncSessionFactory is not None:
            # Already initialized — fast path.
            return

        if _init_task is None or _init_task.done():
            # No task in flight (first call or previous task finished/failed).
            _init_task = asyncio.create_task(_do_init(sync_url, async_url, db_path))

        task = _init_task

    # Await the shared task through shield() so cancelling this coroutine
    # never cancels the shared work.
    await asyncio.shield(task)


async def init_database(database_path: str | None = None) -> None:
    """Initialize the database connection using a once-future state machine.

    Concurrent callers await the SAME in-flight initialization — no caller
    returns before migrations complete.  On failure, globals are cleared
    atomically so a retry re-runs migrations from a clean state.

    Migrations run via ``asyncio.to_thread`` so this coroutine never blocks
    the event loop.  Safe to ``await`` directly inside a FastAPI lifespan or
    any async CLI bridge.

    Args:
        database_path: Path to the SQLite database file.
                      Defaults to DEFAULT_DATABASE_PATH.

    Raises:
        PermissionError: If directory cannot be created
        ValueError: If database path is invalid
    """
    from snore.database.target import DatabaseTarget  # noqa: PLC0415

    if _engine is not None and _AsyncSessionFactory is not None:
        return  # Already initialized — ultra-fast path before taking the lock.

    if database_path is None:
        database_path = DEFAULT_DATABASE_PATH

    if not database_path or not isinstance(database_path, str):
        raise ValueError(f"Invalid database path: {database_path}")

    target = DatabaseTarget.from_url(database_path)
    sync_url = target.resolve_sync_url()
    async_url = target.resolve_async_url()
    db_path = target.sqlite_path if target.is_sqlite else None

    await _init_with_once_task(sync_url, async_url, db_path)


async def init_database_from_url(database_url: str) -> None:
    """Initialise the database from a fully-formed SQLAlchemy URL.

    Concurrent callers await the SAME in-flight initialization — no caller
    returns before migrations complete.  Migrations run via
    ``asyncio.to_thread`` so this coroutine never blocks the event loop.

    Used by ``serve`` after the parent has resolved the canonical URL from the
    ``DatabaseTarget`` precedence chain and exported it as ``SNORE_DATABASE_URL``.

    Args:
        database_url: A fully-formed SQLAlchemy URL string.
    """
    from snore.database.target import DatabaseTarget  # noqa: PLC0415

    if _engine is not None and _AsyncSessionFactory is not None:
        return  # Already initialized — ultra-fast path.

    if not database_url:
        raise ValueError(f"Invalid database URL: {database_url!r}")

    target = DatabaseTarget.from_url(database_url)
    sync_url = target.resolve_sync_url()
    async_url = target.resolve_async_url()
    db_path = target.sqlite_path if target.is_sqlite else None

    await _init_with_once_task(sync_url, async_url, db_path)


def get_session() -> AsyncSession:
    """Return a new (unstarted) async database session.

    Raises:
        RuntimeError: If database has not been initialized.
    """
    if _AsyncSessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _AsyncSessionFactory()


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession]:
    """Provide a transactional scope for async database operations.

    Usage::

        async with session_scope() as session:
            session.add(obj)

    Commits on success; rolls back on any exception.

    Yields:
        An async database session.
    """
    session = get_session()
    try:
        async with session.begin():
            yield session
    except Exception:
        # begin() rolls back automatically on __aexit__ with an exception.
        raise
    finally:
        await session.close()


def get_engine() -> AsyncEngine:
    """Get the database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _engine


def get_db_path() -> str:
    """Get the path to the initialized database."""
    if _db_path is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db_path


async def cleanup_database() -> None:
    """Clean up database connections and reset global state.

    Atomically detaches and awaits any in-flight initialization task before
    disposing published state — no engine/factory can be published after
    cleanup returns.  The stable lock is never replaced.

    This function should be called during test teardown or application shutdown.
    """
    global _engine, _AsyncSessionFactory, _db_path, _init_task

    lock = _get_init_lock()
    async with lock:
        task = _init_task
        _init_task = None

    # Cancel and await the task outside the lock so _do_init's own lock
    # acquisition (in its BaseException handler) cannot deadlock.
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async with lock:
        if _engine is not None:
            await _engine.dispose()
            _engine = None
        _AsyncSessionFactory = None
        _db_path = None
