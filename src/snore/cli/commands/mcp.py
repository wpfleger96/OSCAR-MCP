"""mcp command — launch the SNORE MCP server over stdio."""

from __future__ import annotations

import logging

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
def mcp(db: str | None, profile: str) -> None:
    """Launch the SNORE MCP server over stdio.

    Starts the FastMCP server using stdio transport, suitable for Claude
    Desktop / Claude Code integration.  No authentication is required; the
    server accesses the local database directly.

    For remote (Claude iOS) access with Google OAuth, run 'snore serve' with
    SNORE_PUBLIC_BASE_URL, GOOGLE_CLIENT_ID, and GOOGLE_CLIENT_SECRET set.  The
    FastMCP streamable-HTTP server is embedded into the FastAPI app and served
    at {SNORE_PUBLIC_BASE_URL}/mcp in multiuser mode.

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

    logger.debug("snore mcp: profile=%s db=%r transport=stdio", profile, db)

    server = make_server(db_flag=db, profile_name=profile)
    server.run(transport="stdio")
