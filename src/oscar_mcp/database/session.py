"""Database session management for OSCAR-MCP."""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from oscar_mcp.constants import DEFAULT_DATABASE_PATH

# Base class for all ORM models
Base = declarative_base()

# Global engine and session factory
_engine = None
_SessionFactory = None


def init_database(database_path: str = None) -> None:
    """
    Initialize the database connection.

    Args:
        database_path: Path to the SQLite database file.
                      Defaults to DEFAULT_DATABASE_PATH.
    """
    global _engine, _SessionFactory

    if database_path is None:
        database_path = DEFAULT_DATABASE_PATH

    # Create directory if it doesn't exist
    db_dir = os.path.dirname(database_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

    # Create engine with SQLite
    database_url = f"sqlite:///{database_path}"
    _engine = create_engine(
        database_url,
        echo=False,  # Set to True for SQL debugging
        connect_args={"check_same_thread": False},  # Allow multi-threading
    )

    # Create session factory
    _SessionFactory = sessionmaker(bind=_engine)

    # Create all tables
    Base.metadata.create_all(_engine)


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
def session_scope() -> Generator[Session, None, None]:
    """
    Provide a transactional scope for database operations.

    Usage:
        with session_scope() as session:
            session.add(obj)
            # Automatically commits on success, rolls back on error

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


def get_engine():
    """Get the database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _engine
