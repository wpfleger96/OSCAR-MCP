"""Shared CLI decorators and utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click


def init_db(db: str | None) -> None:
    """Initialize the database, resolving the path if provided."""
    from snore.database.session import init_database

    init_database(str(Path(db)) if db else None)


def db_option(f: Any) -> Any:
    """Shared --db option for commands that access the database."""
    return click.option(
        "--db",
        default=None,
        type=click.Path(),
        help="Path to SQLite database file",
    )(f)


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
