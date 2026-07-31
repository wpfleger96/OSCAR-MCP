"""Database session management for SNORE.

SQLite connection recipe (§4)
------------------------------
The sync engine uses Python ≥3.13 modern transaction control
(``connect_args={"autocommit": False}``).  A plain ``cursor.execute("PRAGMA
journal_mode=WAL")`` inside the connect listener fails with
``OperationalError: cannot change into wal mode from within a transaction``
because Python 3.13 modern-control mode opens an implicit transaction before
the listener fires.

The fix: the connect listener temporarily toggles ``dbapi_conn.autocommit =
True`` before running PRAGMAs, then restores ``False``.  Both PRAGMAs and
modern transaction control are applied atomically on each new connection.

This was probe-verified on Python 3.13.9 in the repository environment.
"""

import logging
import os
import threading

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from snore.constants import DEFAULT_DATABASE_PATH
from snore.database.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionFactory = None
_db_path: str | None = None
_init_lock = threading.Lock()


def _build_alembic_config(database_url: str) -> AlembicConfig:
    migrations_dir = str(Path(__file__).parent / "migrations")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", migrations_dir)
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _apply_migrations(engine: Engine, database_url: str) -> None:
    table_names = set(inspect(engine).get_table_names())
    alembic_cfg = _build_alembic_config(database_url)

    if "sessions" not in table_names:
        Base.metadata.create_all(engine)
        alembic_command.stamp(alembic_cfg, "head")
        logger.info("Fresh database created and stamped at head")
    else:
        # Pre-squash or unstamped DBs fail loudly here (unknown revision / table
        # already exists); pre-alpha contract is: delete the DB file and re-import.
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied (upgrade to head)")


def _register_sqlite_pragmas(engine: Engine) -> None:
    """Attach the integrated SQLite connection recipe to *engine*.

    The connect listener must:
    1. Toggle ``autocommit = True`` before running PRAGMAs (Python 3.13 modern
       transaction control opens an implicit transaction before the listener fires;
       ``journal_mode=WAL`` raises OperationalError inside that transaction).
    2. Apply PRAGMAs while autocommit is True.
    3. Restore ``autocommit = False`` so normal SQLAlchemy transactions use
       modern control.

    VACUUM uses a separate AUTOCOMMIT connection and is not affected by this recipe.
    """

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_conn: Any, connection_record: Any) -> None:
        # Step 1: exit implicit transaction so WAL pragma can execute.
        dbapi_conn.autocommit = True
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()
        # Step 2: restore modern transaction control.
        dbapi_conn.autocommit = False


def init_database(database_path: str | None = None) -> None:
    """
    Initialize the database connection in a thread-safe manner.

    Args:
        database_path: Path to the SQLite database file.
                      Defaults to DEFAULT_DATABASE_PATH.

    Raises:
        PermissionError: If directory cannot be created
        ValueError: If database path is invalid
    """
    global _engine, _SessionFactory, _db_path

    with _init_lock:
        if _engine is not None and _SessionFactory is not None:
            return

        if database_path is None:
            database_path = DEFAULT_DATABASE_PATH

        if not database_path or not isinstance(database_path, str):
            raise ValueError(f"Invalid database path: {database_path}")

        _db_path = database_path

        db_dir = os.path.dirname(database_path)
        if db_dir:
            try:
                os.makedirs(db_dir, exist_ok=True)
            except PermissionError as e:
                raise PermissionError(
                    f"Cannot create database directory {db_dir}: {e}"
                ) from e

        database_url = f"sqlite:///{database_path}"

        _engine = create_engine(
            database_url,
            echo=False,
            connect_args={
                "check_same_thread": False,
                "autocommit": False,  # Python ≥3.13 modern transaction control
            },
            pool_pre_ping=True,
        )

        _register_sqlite_pragmas(_engine)

        _SessionFactory = sessionmaker(bind=_engine)

        _apply_migrations(_engine, database_url)


def init_database_from_url(database_url: str) -> None:
    """Initialise the database from a fully-formed SQLAlchemy URL.

    Used by ``serve`` after the parent has resolved the canonical URL from the
    ``DatabaseTarget`` precedence chain and exported it as ``SNORE_DATABASE_URL``.

    For SQLite URLs the path component is extracted and used for directory
    creation and Alembic config.  Non-SQLite URLs (e.g. PostgreSQL) are
    forwarded once their drivers are installed (hosted milestone).

    Args:
        database_url: A fully-formed SQLAlchemy URL string.
    """
    global _engine, _SessionFactory, _db_path

    with _init_lock:
        if _engine is not None and _SessionFactory is not None:
            return

        if not database_url:
            raise ValueError(f"Invalid database URL: {database_url!r}")

        # Extract file path from sqlite:///... URL for directory creation.
        if database_url.startswith("sqlite"):
            # sqlite:////abs/path or sqlite:///rel/path
            raw_path = database_url.split("sqlite+pysqlite://", 1)[-1]
            if raw_path.startswith("/"):
                pass  # absolute
            raw_path = database_url.split("sqlite", 1)[1].split("///", 1)[-1]
            # Normalise: strip leading slash for relative, keep for absolute.
            if database_url.count("/") >= 4:
                # Four or more slashes → absolute path (sqlite:////abs/…)
                _db_path = "/" + raw_path.lstrip("/")
            else:
                _db_path = raw_path

            db_dir = os.path.dirname(_db_path)
            if db_dir:
                try:
                    os.makedirs(db_dir, exist_ok=True)
                except PermissionError as e:
                    raise PermissionError(
                        f"Cannot create database directory {db_dir}: {e}"
                    ) from e
        else:
            _db_path = None  # non-SQLite; no local path

        _engine = create_engine(
            database_url,
            echo=False,
            connect_args={
                "check_same_thread": False,
                "autocommit": False,
            },
            pool_pre_ping=True,
        )

        _register_sqlite_pragmas(_engine)

        _SessionFactory = sessionmaker(bind=_engine)

        _apply_migrations(_engine, database_url)


def get_session() -> Session:
    """
    Get a new database session.

    Returns:
        A new SQLAlchemy session.

    Raises:
        RuntimeError: If database has not been initialized.
    """
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    return _SessionFactory()


@contextmanager
def session_scope() -> Generator[Session]:
    """
    Provide a transactional scope for database operations.

    Usage:
        with session_scope() as session:
            session.add(obj)

    Yields:
        A database session.
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine() -> Engine:
    """Get the database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _engine


def get_db_path() -> str:
    """Get the path to the initialized database."""
    if _db_path is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db_path


def cleanup_database() -> None:
    """
    Clean up database connections and reset global state.

    This function should be called during test cleanup to prevent resource warnings.
    It properly disposes of the SQLAlchemy engine and resets global variables.
    """
    global _engine, _SessionFactory, _db_path

    with _init_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
        _SessionFactory = None
        _db_path = None
