"""Database session management for SNORE.

Async SQLite connection recipe (§4, PR-2)
------------------------------------------
The async engine uses ``aiosqlite`` as the DBAPI.  aiosqlite wraps the raw
``sqlite3.Connection`` in an ``AsyncAdapt_aiosqlite_connection`` adapter.
The PRAGMA recipe on the ``"connect"`` event accesses the raw connection via
``dbapi_conn.driver_connection._conn`` and sets ``isolation_level = None``
(autocommit) before applying PRAGMAs, then restores the prior isolation level.

VACUUM uses a separate sync AUTOCOMMIT connection and is unaffected.

Alembic stays fully synchronous.  Migrations run via
``asyncio.get_event_loop().run_in_executor`` / ``asyncio.to_thread`` patterns
in test helpers, or directly in CLI commands that bridge with ``asyncio.run``.

expire_on_commit=False is set on all sessions so that ORM attributes remain
accessible after a commit without triggering implicit I/O — required for
async contexts where lazy loads raise MissingGreenlet.
"""

import logging
import os
import threading

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
_init_lock = threading.Lock()


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

    The recipe attaches to ``async_engine.sync_engine`` (the underlying pysqlite
    engine) so the autocommit toggle works on the real DBAPI connection.

    For ``aiosqlite``, ``dbapi_conn`` is an ``AsyncAdapt_aiosqlite_connection``
    wrapper.  The underlying ``sqlite3.Connection`` is at
    ``dbapi_conn.driver_connection._conn``.  We set ``isolation_level = None``
    (autocommit) on the raw connection before applying PRAGMAs, then restore it.

    Steps:
    1. Access the raw ``sqlite3.Connection`` via ``driver_connection._conn``.
    2. Set ``isolation_level = None`` (autocommit) so PRAGMAs like
       ``journal_mode=WAL`` can run outside a transaction.
    3. Apply PRAGMAs.
    4. Restore the original ``isolation_level``.
    """
    from sqlalchemy import event  # noqa: PLC0415

    @event.listens_for(async_engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_conn: Any, connection_record: Any) -> None:
        # aiosqlite wraps the raw connection; unwrap to access isolation_level.
        raw_conn = getattr(
            getattr(dbapi_conn, "driver_connection", None), "_conn", None
        )
        if raw_conn is None:
            # Fallback: try direct autocommit attribute (sync pysqlite path).
            raw_conn = dbapi_conn

        prior_isolation = getattr(raw_conn, "isolation_level", "")
        try:
            raw_conn.isolation_level = None  # autocommit
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
                cursor.execute("PRAGMA temp_store=MEMORY")
            finally:
                cursor.close()
        finally:
            raw_conn.isolation_level = prior_isolation


def _init_engine(database_url: str, async_url: str, db_path: str | None) -> None:
    """Build the async engine and session factory (called under _init_lock)."""
    global _engine, _AsyncSessionFactory, _db_path

    if db_path and db_path != ":memory:":
        db_dir = os.path.dirname(db_path)
        if db_dir:
            try:
                os.makedirs(db_dir, exist_ok=True)
            except PermissionError as e:
                raise PermissionError(
                    f"Cannot create database directory {db_dir}: {e}"
                ) from e

    _db_path = db_path

    engine = create_async_engine(
        async_url,
        echo=False,
        connect_args={
            "check_same_thread": False,
        },
        pool_pre_ping=True,
    )

    # Attach the SQLite PRAGMA recipe via sync_engine listener.
    from sqlalchemy.engine import make_url as _make_url  # noqa: PLC0415

    dialect = _make_url(async_url).get_backend_name()
    if dialect == "sqlite":
        _register_sqlite_pragmas_on_async_engine(engine)

    _engine = engine
    _AsyncSessionFactory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Alembic migrations always use the sync pysqlite URL.
    _apply_migrations_sync(database_url)


def init_database(database_path: str | None = None) -> None:
    """Initialize the database connection in a thread-safe manner.

    Args:
        database_path: Path to the SQLite database file.
                      Defaults to DEFAULT_DATABASE_PATH.

    Raises:
        PermissionError: If directory cannot be created
        ValueError: If database path is invalid
    """
    from snore.database.target import DatabaseTarget  # noqa: PLC0415

    global _engine, _AsyncSessionFactory

    with _init_lock:
        if _engine is not None and _AsyncSessionFactory is not None:
            return

        if database_path is None:
            database_path = DEFAULT_DATABASE_PATH

        if not database_path or not isinstance(database_path, str):
            raise ValueError(f"Invalid database path: {database_path}")

        target = DatabaseTarget.from_url(database_path)
        sync_url = target.resolve_sync_url()
        async_url = target.resolve_async_url()
        db_path = target.sqlite_path if target.is_sqlite else None

        _init_engine(sync_url, async_url, db_path)


def init_database_from_url(database_url: str) -> None:
    """Initialise the database from a fully-formed SQLAlchemy URL.

    Used by ``serve`` after the parent has resolved the canonical URL from the
    ``DatabaseTarget`` precedence chain and exported it as ``SNORE_DATABASE_URL``.

    Args:
        database_url: A fully-formed SQLAlchemy URL string.
    """
    from snore.database.target import DatabaseTarget  # noqa: PLC0415

    global _engine, _AsyncSessionFactory

    with _init_lock:
        if _engine is not None and _AsyncSessionFactory is not None:
            return

        if not database_url:
            raise ValueError(f"Invalid database URL: {database_url!r}")

        target = DatabaseTarget.from_url(database_url)
        sync_url = target.resolve_sync_url()
        async_url = target.resolve_async_url()
        db_path = target.sqlite_path if target.is_sqlite else None

        _init_engine(sync_url, async_url, db_path)


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


def get_sync_session_for_alembic() -> Any:
    """Return a synchronous session for Alembic operations only.

    This is intentionally sync — Alembic's migration runner is synchronous.
    Do NOT use this for application logic.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    if _db_path is None:
        raise RuntimeError("Database not initialized.")
    url = f"sqlite:///{_db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine)
    return factory()


async def cleanup_database() -> None:
    """Clean up database connections and reset global state.

    This function should be called during test cleanup to prevent resource warnings.
    """
    global _engine, _AsyncSessionFactory, _db_path

    with _init_lock:
        if _engine is not None:
            await _engine.dispose()
            _engine = None
        _AsyncSessionFactory = None
        _db_path = None
