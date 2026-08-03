from collections.abc import AsyncGenerator, Callable
from datetime import date, datetime, time
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from snore.auth.actor import ActorContext, AuthMode
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


async def get_actor(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ActorContext:
    """Return the actor for this request.

    In local mode: AuthMiddleware auto-provisions and sets request.state.actor.
    In multiuser mode: AuthMiddleware validates the session cookie and sets it.

    Raises 401 if no actor is present (unauthenticated in multiuser or
    middleware provisioning failed in local mode).
    """
    actor: ActorContext | None = getattr(request.state, "actor", None)
    if actor is not None:
        return actor

    # Fallback for test environments where middleware may not run (e.g. unit
    # tests that create a TestClient without lifespan and don't set state.actor).
    # In production multiuser mode the middleware always runs; this path is only
    # reached in test setups that bypass middleware.
    from snore.api.config import get_config  # noqa: PLC0415

    cfg = get_config()
    if cfg.is_multiuser:
        from fastapi import HTTPException  # noqa: PLC0415

        raise HTTPException(status_code=401, detail="Authentication required")

    from snore.auth.factory import ActorContextFactory  # noqa: PLC0415

    factory = ActorContextFactory(db)
    return await factory.make_local(mode=AuthMode.LOCAL)


ActorDep = Annotated[ActorContext, Depends(get_actor)]


def service_dep[T](cls: Callable[[AsyncSession, int], T]) -> Callable[..., T]:
    """Return a FastAPI dependency that constructs ``cls(db, profile_id)``."""

    async def _dep(
        db: Annotated[AsyncSession, Depends(get_db)],
        actor: ActorDep,
    ) -> T:
        return cls(db, actor.profile_id)

    return _dep  # type: ignore[return-value]


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
