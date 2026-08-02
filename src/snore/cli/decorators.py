"""Shared CLI decorators and utilities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def init_db(db: str | None) -> None:
    """Initialize the database, resolving the path if provided.

    Bridges the sync Click command boundary to the async ``init_database``
    coroutine by running it in a new event loop.
    """
    import asyncio  # noqa: PLC0415

    from snore.database.session import init_database  # noqa: PLC0415

    asyncio.run(init_database(str(Path(db).expanduser()) if db else None))


@asynccontextmanager
async def db_session(db: str | None) -> AsyncIterator[AsyncSession]:
    """Async CLI bridge: initialise the database and yield an AsyncSession.

    Each CLI command wraps its body in ``asyncio.run`` and uses this context
    manager to obtain a session.  The session is committed on clean exit and
    rolled back on exception.
    """
    from snore.database.session import init_database, session_scope  # noqa: PLC0415

    await init_database(str(Path(db).expanduser()) if db else None)
    async with session_scope() as session:
        yield session


def db_option(f: Any) -> Any:
    """Shared --db option for commands that access the database."""
    return click.option(
        "--db",
        default=None,
        type=click.Path(),
        help="Path to SQLite database file",
    )(f)


def device_option(f: Any) -> Any:
    """Shared --device/-d option for filtering by device serial number."""
    return click.option("--device", "-d", help="Device serial number")(f)


def session_id_date_options(f: Any) -> Any:
    """Shared --session-id/--date options for selecting a session."""
    f = click.option(
        "--date",
        type=click.DateTime(formats=["%Y-%m-%d"]),
        help="Session date (YYYY-MM-DD)",
    )(f)
    f = click.option("--session-id", type=int, help="Session ID")(f)
    return f


def date_range_options(f: Any) -> Any:
    """Shared --from/--to date range options."""
    f = click.option(
        "--to",
        "date_to",
        default=None,
        type=click.DateTime(formats=["%Y-%m-%d"]),
        help="End date (YYYY-MM-DD)",
    )(f)
    f = click.option(
        "--from",
        "date_from",
        default=None,
        type=click.DateTime(formats=["%Y-%m-%d"]),
        help="Start date (YYYY-MM-DD)",
    )(f)
    return f


def date_range_options_required(f: Any) -> Any:
    """Shared --from/--to date range options (required, not optional)."""
    f = click.option(
        "--to",
        "date_to",
        required=True,
        type=click.DateTime(formats=["%Y-%m-%d"]),
        help="End date (YYYY-MM-DD)",
    )(f)
    f = click.option(
        "--from",
        "date_from",
        required=True,
        type=click.DateTime(formats=["%Y-%m-%d"]),
        help="Start date (YYYY-MM-DD)",
    )(f)
    return f


def parse_id_list(raw: str) -> list[int]:
    """Parse a comma-separated string of IDs into a list of ints.

    Raises click.BadParameter on invalid input.
    """
    try:
        return [int(sid.strip()) for sid in raw.split(",")]
    except ValueError as e:
        raise click.BadParameter(
            f"Invalid ID list: {raw!r}. Expected comma-separated integers."
        ) from e
