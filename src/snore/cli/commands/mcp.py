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
@click.option(
    "--transport",
    default="stdio",
    show_default=True,
    help="Transport mode (stdio now; http in a future release)",
)
def mcp(db: str | None, profile: str, transport: str) -> None:
    """Launch the SNORE MCP server.

    Starts the FastMCP server using the stdio transport (default), suitable
    for Claude Desktop / Claude Code integration.

    Database resolution uses the same precedence chain as 'snore serve':
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

    if transport != "stdio":
        raise click.BadParameter(
            f"Transport {transport!r} is not yet supported. Only 'stdio' is available.",
            param_hint="--transport",
        )

    from snore.mcp.server import make_server

    logger.debug("snore mcp: profile=%s db=%r", profile, db)
    server = make_server(db_flag=db, profile_name=profile)
    server.run(transport="stdio")
