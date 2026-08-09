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
    help=(
        "Transport mode: stdio (default, for Claude Desktop/Code) or http "
        "(DEPRECATED — 'snore serve' now embeds the HTTP MCP endpoint at /mcp; "
        "standalone http transport will be removed in a future release)."
    ),
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

    HTTP transport (DEPRECATED): Start the FastMCP server over streamable-HTTP
    with Google OAuth, suitable for Claude iOS (remote MCP) integration.
    'snore serve' now embeds this endpoint at /mcp when SNORE_MCP_BASE_URL is
    set; the standalone http transport will be removed in a future release.
    Note: every authenticated request performs a live Google tokeninfo call;
    outages or rate-limits will affect all requests.  Requires three
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
        base_url = os.environ.get("SNORE_MCP_BASE_URL", "").strip()
        google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        missing = [
            name
            for name, val in [
                ("SNORE_MCP_BASE_URL", base_url),
                ("GOOGLE_CLIENT_ID", google_client_id),
                ("GOOGLE_CLIENT_SECRET", google_client_secret),
            ]
            if not val
        ]
        if missing:
            raise click.UsageError(
                f"HTTP transport requires environment variables: {', '.join(missing)}"
            )

        from snore.mcp.auth import make_auth_provider  # noqa: PLC0415

        try:
            auth = make_auth_provider(
                base_url=base_url,
                google_client_id=google_client_id,
                google_client_secret=google_client_secret,
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        server = make_server(db_flag=db, profile_name=profile, auth=auth)
        server.run(transport="http", host=host, port=port)
    else:
        if host != "127.0.0.1" or port != 8321:
            click.echo(
                "Warning: --host/--port are ignored with stdio transport", err=True
            )
        server = make_server(db_flag=db, profile_name=profile)
        server.run(transport="stdio")
