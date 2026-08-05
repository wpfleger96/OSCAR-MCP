"""mcp command — launch the SNORE MCP server over stdio or HTTP with OAuth."""

from __future__ import annotations

import logging
import os

import click

logger = logging.getLogger(__name__)


@click.command()
@click.option("--db", default=None, help="Path to SQLite database file")
@click.option(
    "--profile",
    default="neutral",
    show_default=True,
    help="Clinical profile: neutral, uars, osa, csa",
)
@click.option(
    "--transport",
    default="stdio",
    show_default=True,
    type=click.Choice(["stdio", "http"]),
    help="Transport mode: stdio (default, for Claude Desktop/Code) or http (OAuth, for Claude iOS remote).",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind when --transport=http.",
)
@click.option(
    "--port",
    default=8321,
    show_default=True,
    type=int,
    help="Port to bind when --transport=http.",
)
def mcp(db: str | None, profile: str, transport: str, host: str, port: int) -> None:
    """Launch the SNORE MCP server.

    STDIO transport (default): Start the FastMCP server using stdio, suitable
    for Claude Desktop / Claude Code integration.

    HTTP transport: Start the FastMCP server over streamable-HTTP with Google
    OAuth, suitable for Claude iOS (remote MCP) integration.  Requires three
    environment variables:

    \b
        SNORE_MCP_BASE_URL     — public base URL of this server (e.g.
                                  https://mcp.example.com), used to build the
                                  OAuth redirect URI and server metadata.
        GOOGLE_CLIENT_ID       — OAuth 2.0 client ID from Google Cloud Console.
        GOOGLE_CLIENT_SECRET   — OAuth 2.0 client secret.

    The Google OAuth app must register {SNORE_MCP_BASE_URL}/auth/callback as an
    authorised redirect URI.  Users must already have a SNORE account with a
    linked Google identity (set up via the web UI before connecting).

    Database resolution uses the same precedence chain as 'snore serve':

    \b
        --db > SNORE_DATABASE_URL > SNORE_DB_PATH > default SQLite path

    Clinical profiles shape the INSTRUCTIONS resource and priority hints only;
    they do not change the data returned by any tool (G1).  Available profiles:
    neutral (default), uars, osa, csa.
    """
    from snore.mcp.profiles import VALID_PROFILES

    if profile not in VALID_PROFILES:
        raise click.BadParameter(
            f"Unknown profile {profile!r}. Choose from: {sorted(VALID_PROFILES)}",
            param_hint="--profile",
        )

    from snore.mcp.server import make_server

    logger.debug("snore mcp: profile=%s db=%r transport=%s", profile, db, transport)

    if transport == "http":
        missing = [
            name
            for name, var in [
                ("SNORE_MCP_BASE_URL", os.environ.get("SNORE_MCP_BASE_URL")),
                ("GOOGLE_CLIENT_ID", os.environ.get("GOOGLE_CLIENT_ID")),
                ("GOOGLE_CLIENT_SECRET", os.environ.get("GOOGLE_CLIENT_SECRET")),
            ]
            if not var
        ]
        if missing:
            raise click.UsageError(
                f"HTTP transport requires environment variables: {', '.join(missing)}"
            )

        from snore.mcp.auth import make_auth_provider  # noqa: PLC0415

        auth = make_auth_provider(
            base_url=os.environ["SNORE_MCP_BASE_URL"],
            google_client_id=os.environ["GOOGLE_CLIENT_ID"],
            google_client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        )
        server = make_server(db_flag=db, profile_name=profile, auth=auth)
        server.run(transport="http", host=host, port=port)
    else:
        server = make_server(db_flag=db, profile_name=profile)
        server.run(transport="stdio")
