"""serve command — start the SNORE API server and web UI."""

from __future__ import annotations

import logging
import os

import click
import uvicorn

logger = logging.getLogger(__name__)


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to listen on")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--db", default=None, help="Path to SQLite database file")
def serve(host: str, port: int, reload: bool, db: str | None) -> None:
    """Start the SNORE API server and web UI.

    Serves the REST API at /api/v1/. If the web UI has been built
    (ui/dist/ exists), it is served at all other paths.

    Database resolution uses the precedence chain:
        --db > SNORE_DATABASE_URL > SNORE_DB_PATH > default SQLite path

    The parent process resolves the target and exports the canonical URL as
    SNORE_DATABASE_URL so the uvicorn child process opens the same database.
    SNORE_DB_PATH is removed from the environment to avoid lower-precedence
    leakage.
    """
    from snore.database.target import DatabaseTarget

    target = DatabaseTarget.from_env_and_flags(db_flag=db, warn_ignored=True)
    canonical_url = target.resolve_sync_url()

    # Export the canonical URL for the child process; clear the lower-precedence
    # path variable so it cannot override what the child receives.
    os.environ["SNORE_DATABASE_URL"] = canonical_url
    os.environ.pop("SNORE_DB_PATH", None)

    # Export the actual bind host so the app lifespan validates the same
    # address that uvicorn will actually bind.  This is the single source of
    # truth: the lifespan reads SNORE_BIND_HOST, not --host.
    os.environ["SNORE_BIND_HOST"] = host

    # Validate config — including local-mode + non-loopback bind refusal —
    # before the socket is created.  Fail fast with a clear error message.
    from snore.api.config import ConfigError, load_config  # noqa: PLC0415

    try:
        load_config()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    logger.debug(
        "serve: resolved database target %r → %r", target.raw_url, canonical_url
    )

    uvicorn.run(
        "snore.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["src"] if reload else None,
    )
