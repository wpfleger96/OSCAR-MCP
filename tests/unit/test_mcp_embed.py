"""Unit tests for the embedded MCP wiring: path classifier, app builder, and
the make_server/manage_database seam."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

import snore.api.mcp_embed as mcp_embed
import snore.mcp.server as mcp_server

from snore.api.config import AppConfig, parse_origin
from snore.api.mcp_embed import build_mcp_app, is_mcp_path
from snore.auth.actor import AuthMode
from snore.mcp.server import _lifespan, make_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_URL = "https://snore.example.com"


def _config(
    *,
    auth_mode: AuthMode = AuthMode.MULTIUSER,
    mcp_base_url: str = _BASE_URL,
    google_configured: bool = True,
) -> AppConfig:
    return AppConfig(
        auth_mode=auth_mode,
        session_secret="x" * 32 if auth_mode is AuthMode.MULTIUSER else "",
        public_base_url=_BASE_URL if auth_mode is AuthMode.MULTIUSER else "",
        public_origin=(
            parse_origin(_BASE_URL) if auth_mode is AuthMode.MULTIUSER else None
        ),
        bind_host="127.0.0.1",
        trusted_proxies=frozenset(),
        dev_origins=frozenset(),
        cors_origins=["http://localhost:5173"],
        google_client_id="dummy-id" if google_configured else "",
        google_client_secret="dummy-secret" if google_configured else "",
        oauth_attempt_ttl_seconds=600,
        pre_auth_cookie_ttl_seconds=600,
        max_upload_bytes=512 * 1024 * 1024,
        max_file_bytes=256 * 1024 * 1024,
        max_upload_files=10000,
        max_jobs_per_user=3,
        max_jobs_global=10,
        analysis_max_workers=4,
        mcp_base_url=mcp_base_url,
    )


@pytest.fixture(scope="module")
def _verifier() -> JWTVerifier:
    key_pair = RSAKeyPair.generate()
    return JWTVerifier(public_key=key_pair.public_key)


# ---------------------------------------------------------------------------
# is_mcp_path
# ---------------------------------------------------------------------------


class TestIsMcpPath:
    @pytest.mark.parametrize(
        "path",
        [
            "/mcp",
            "/mcp/",
            "/mcp/messages",
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
            "/.well-known/oauth-protected-resource/mcp",
            "/authorize",
            "/token",
            "/register",
            "/revoke",
            "/consent",
            "/auth/callback",
        ],
    )
    def test_mcp_paths_match(self, path: str) -> None:
        assert is_mcp_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/api/v1/profiles/",
            "/api/v1/auth/login",
            "/health",
            "/mcpx",  # prefix must not match by startswith on "/mcp"
            "/tokens",  # not an exact OAuth route match
            "/auth/callback/extra",
            "/index.html",
        ],
    )
    def test_non_mcp_paths_do_not_match(self, path: str) -> None:
        assert is_mcp_path(path) is False


# ---------------------------------------------------------------------------
# build_mcp_app
# ---------------------------------------------------------------------------


class TestBuildMcpApp:
    def test_disabled_without_base_url_returns_none(self) -> None:
        assert build_mcp_app(_config(mcp_base_url="")) is None

    def test_local_mode_returns_none_and_logs_info(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("INFO", logger="snore.api.mcp_embed"):
            result = build_mcp_app(_config(auth_mode=AuthMode.LOCAL))
        assert result is None
        assert "disabled in local mode" in caplog.text

    def test_missing_google_returns_none_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING", logger="snore.api.mcp_embed"):
            result = build_mcp_app(_config(google_configured=False))
        assert result is None
        assert "Google OAuth is not configured" in caplog.text

    def test_enabled_returns_sub_app_with_mcp_route(
        self, monkeypatch: pytest.MonkeyPatch, _verifier: JWTVerifier
    ) -> None:
        monkeypatch.setattr(mcp_embed, "_make_mcp_auth_provider", lambda cfg: _verifier)

        app = build_mcp_app(_config())

        assert app is not None
        assert any(getattr(r, "path", None) == "/mcp" for r in app.routes)


# ---------------------------------------------------------------------------
# make_server / _lifespan — manage_database seam
# ---------------------------------------------------------------------------


class TestManageDatabaseSeam:
    def test_manage_database_false_without_auth_raises(self) -> None:
        with pytest.raises(ValueError, match="manage_database=False requires"):
            make_server(manage_database=False)

    def test_manage_database_false_with_auth_builds(
        self, _verifier: JWTVerifier
    ) -> None:
        server = make_server(auth=_verifier, manage_database=False)
        assert server.name == "snore"

    async def test_lifespan_managed_initializes_and_cleans_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        init = AsyncMock()
        cleanup = AsyncMock()
        monkeypatch.setattr(mcp_server, "init_database_from_url", init)
        monkeypatch.setattr(mcp_server, "cleanup_database", cleanup)

        # actor_scoped=True avoids the first-live-profile query (no real DB).
        async with _lifespan(None, actor_scoped=True, manage_database=True):
            init.assert_awaited_once()
            cleanup.assert_not_awaited()
        cleanup.assert_awaited_once()

    async def test_lifespan_unmanaged_never_touches_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        init = AsyncMock()
        cleanup = AsyncMock()
        monkeypatch.setattr(mcp_server, "init_database_from_url", init)
        monkeypatch.setattr(mcp_server, "cleanup_database", cleanup)

        async with _lifespan(None, actor_scoped=True, manage_database=False):
            pass

        init.assert_not_awaited()
        cleanup.assert_not_awaited()

    async def test_lifespan_unmanaged_startup_error_skips_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cleanup = AsyncMock()
        monkeypatch.setattr(mcp_server, "cleanup_database", cleanup)

        def _boom() -> None:
            raise RuntimeError("parser registration failed")

        monkeypatch.setattr(
            "snore.parsers.register_all.ensure_registered_parsers", _boom
        )

        with pytest.raises(RuntimeError, match="parser registration failed"):
            async with _lifespan(None, actor_scoped=True, manage_database=False):
                pass  # pragma: no cover — startup fails before yield

        cleanup.assert_not_awaited()
