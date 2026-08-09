"""Embedded MCP server: mount the FastMCP streamable-HTTP app inside FastAPI.

``snore serve`` hosts the MCP endpoint at ``/mcp`` in the same uvicorn process
as the REST API when ``AppConfig.is_mcp_enabled`` is true (multiuser mode +
``SNORE_MCP_BASE_URL`` + Google OAuth credentials).  ``build_mcp_app`` returns
the FastMCP sub-app; ``app.py`` mounts it as the final catch-all and chains its
lifespan (which owns the StreamableHTTPSessionManager task group) into the
FastAPI lifespan.

``is_mcp_path`` is the single classifier for MCP-owned request paths, consumed
by the CSRF middleware exemption and the SPA 404 fallback.  FastMCP serves the
MCP endpoint at ``/mcp`` and, when auth is configured, OAuth protocol routes at
the sub-app root: ``/.well-known/*`` metadata plus the fixed set in
``_MCP_ROOT_PATHS``.
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider
    from fastmcp.server.http import StarletteWithLifespan

    from snore.api.config import AppConfig

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"

# Root-level OAuth protocol routes registered by FastMCP's auth provider
# (GoogleProvider / OAuthProxy).  /revoke is optional but reserved.
_MCP_ROOT_PATHS = frozenset(
    {"/authorize", "/token", "/register", "/revoke", "/consent", "/auth/callback"}
)


def is_mcp_path(path: str) -> bool:
    """True when ``path`` belongs to the embedded MCP sub-app.

    Covers the MCP endpoint (``/mcp`` and anything under it), OAuth discovery
    metadata (``/.well-known/*``), and the root-level OAuth protocol routes.
    """
    return (
        path == MCP_PATH
        or path.startswith(f"{MCP_PATH}/")
        or path.startswith("/.well-known/")
        or path in _MCP_ROOT_PATHS
    )


def build_mcp_app(cfg: AppConfig) -> StarletteWithLifespan | None:
    """Build the FastMCP streamable-HTTP sub-app, or None when MCP is disabled.

    The sub-app carries its own middleware stack (bearer-token auth) and
    lifespan (StreamableHTTPSessionManager task group), so the whole app must
    be mounted and its lifespan chained — see ``app.py``.
    """
    if not cfg.is_mcp_enabled:
        if cfg.mcp_base_url:
            # SNORE_MCP_BASE_URL is set but a co-requirement is missing —
            # explain why /mcp will not be served.
            if not cfg.is_multiuser:
                logger.info(
                    "SNORE_MCP_BASE_URL is set but auth mode is local"
                    " — /mcp is disabled in local mode (use 'snore mcp' for stdio)"
                )
            else:
                logger.warning(
                    "SNORE_MCP_BASE_URL is set but Google OAuth is not configured"
                    " (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET absent)"
                    " — /mcp will not be served"
                )
        return None

    from snore.mcp.server import make_server  # noqa: PLC0415

    auth = _make_mcp_auth_provider(cfg)
    # manage_database=False: the FastAPI lifespan owns the module-global engine.
    server = make_server(auth=auth, manage_database=False)
    logger.info("Embedded MCP server enabled at %s%s", cfg.mcp_base_url, MCP_PATH)
    return server.http_app(path=MCP_PATH, host_origin_protection=False)


def _make_mcp_auth_provider(cfg: AppConfig) -> AuthProvider:
    """Construct the Google OAuth provider for the embedded MCP app.

    Module-level seam: tests monkeypatch this to substitute a JWTVerifier so
    no network calls to Google are made.
    """
    from snore.mcp.auth import make_auth_provider  # noqa: PLC0415

    return make_auth_provider(
        base_url=cfg.mcp_base_url,
        google_client_id=cfg.google_client_id,
        google_client_secret=cfg.google_client_secret,
    )
