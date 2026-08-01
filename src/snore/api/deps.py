from collections.abc import AsyncGenerator, Callable
from datetime import date, datetime, time
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from snore.database.session import get_session


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency that provides a committed/rolled-back AsyncSession."""
    session = get_session()
    try:
        async with session.begin():
            yield session
    except Exception:
        raise
    finally:
        await session.close()


def service_dep[T](cls: Callable[[AsyncSession], T]) -> Callable[..., T]:
    """Return a FastAPI dependency that constructs ``cls(db)``."""

    async def _dep(db: Annotated[AsyncSession, Depends(get_db)]) -> T:
        return cls(db)

    return _dep  # type: ignore[return-value]  # FastAPI resolves async deps; Callable[..., T] matches


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

    @property
    def start_datetime(self) -> datetime | None:
        """from_date as an inclusive datetime lower bound (midnight)."""
        return datetime.combine(self.from_date, time.min) if self.from_date else None

    @property
    def end_datetime(self) -> datetime | None:
        """to_date as an inclusive datetime upper bound (end of day)."""
        return datetime.combine(self.to_date, time.max) if self.to_date else None
