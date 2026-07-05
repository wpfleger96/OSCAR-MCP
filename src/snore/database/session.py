"""Database session management for SNORE."""

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


def _build_alembic_config(database_path: str) -> AlembicConfig:
    migrations_dir = str(Path(__file__).parent / "migrations")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", migrations_dir)
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return cfg


def _apply_migrations(engine: Engine, database_path: str) -> None:
    insp = inspect(engine)
    table_names = set(insp.get_table_names())
    alembic_cfg = _build_alembic_config(database_path)

    if "alembic_version" in table_names:
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied (upgrade to head)")
    elif "sessions" not in table_names:
        Base.metadata.create_all(engine)
        alembic_command.stamp(alembic_cfg, "head")
        logger.info("Fresh database created and stamped at head")
    else:
        columns = {col["name"] for col in insp.get_columns("statistics")}
        stamp_rev = "a3f8e9c12b45" if "ipap_median" in columns else "102cf96663ea"
        alembic_command.stamp(alembic_cfg, stamp_rev)
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Legacy database stamped at %s and upgraded to head", stamp_rev)


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
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_conn: Any, connection_record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()

        _SessionFactory = sessionmaker(bind=_engine)

        _apply_migrations(_engine, database_path)


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
