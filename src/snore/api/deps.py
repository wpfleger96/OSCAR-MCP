from collections.abc import Generator
from datetime import date
from typing import Annotated

from fastapi import Query
from sqlalchemy.orm import Session

from snore.database.session import get_session


def get_db() -> Generator[Session]:
    """FastAPI dependency that mirrors session_scope() commit/rollback behavior."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class PaginationParams:
    def __init__(
        self,
        limit: int = Query(default=20, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


class DateRangeParams:
    def __init__(
        self,
        from_date: Annotated[date | None, Query()] = None,
        to_date: Annotated[date | None, Query()] = None,
    ):
        self.from_date = from_date
        self.to_date = to_date
