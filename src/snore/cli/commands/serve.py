"""serve command — start the SNORE API server and web UI."""

from __future__ import annotations

import os

import click
import uvicorn


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to listen on")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--db", default=None, help="Path to SQLite database file")
def serve(host: str, port: int, reload: bool, db: str | None) -> None:
    """Start the SNORE API server and web UI.

    Serves the REST API at /api/v1/. If the web UI has been built
    (ui/dist/ exists), it is served at all other paths.
    """
    if db:
        # uvicorn spawns the app via factory; env var is the only cross-process
        # channel available at this point
        os.environ["SNORE_DB_PATH"] = db

    uvicorn.run(
        "snore.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )
