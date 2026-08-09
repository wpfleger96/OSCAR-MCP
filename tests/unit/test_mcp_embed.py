"""Unit tests for snore.api.mcp_embed — is_mcp_path, build_mcp_app, AppConfig.is_mcp_enabled."""

from __future__ import annotations

import pytest

from snore.api.config import load_config, reset_config
from snore.api.mcp_embed import is_mcp_path

# ---------------------------------------------------------------------------
# is_mcp_path
# ---------------------------------------------------------------------------


class TestIsMcpPath:
    def test_mcp_root(self) -> None:
        assert is_mcp_path("/mcp") is True

    def test_mcp_sub_path(self) -> None:
        assert is_mcp_path("/mcp/messages") is True
        assert is_mcp_path("/mcp/foo/bar") is True

    def test_well_known(self) -> None:
        assert is_mcp_path("/.well-known/oauth-authorization-server") is True
        assert is_mcp_path("/.well-known/oauth-protected-resource/mcp") is True

    def test_oauth_flow_paths(self) -> None:
        for path in (
            "/authorize",
            "/token",
            "/register",
            "/revoke",
            "/consent",
            "/auth/callback",
        ):
            assert is_mcp_path(path) is True, (
                f"Expected is_mcp_path({path!r}) to be True"
            )

    def test_api_paths_not_mcp(self) -> None:
        assert is_mcp_path("/api/v1/sessions") is False
        assert is_mcp_path("/api/v1/admin/users") is False

    def test_health_not_mcp(self) -> None:
        assert is_mcp_path("/health") is False

    def test_spa_asset_not_mcp(self) -> None:
        assert is_mcp_path("/assets/main.js") is False

    def test_mcp_prefix_without_slash_not_mcp(self) -> None:
        # /mcpfoo must NOT match — requires /mcp followed by / or end of string
        assert is_mcp_path("/mcpfoo") is False

    def test_root_not_mcp(self) -> None:
        assert is_mcp_path("/") is False


# ---------------------------------------------------------------------------
# AppConfig.is_mcp_enabled
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


_SESSION_SECRET = "test-secret-at-least-32-chars-long-zzzzz"
_PUBLIC_BASE_URL = "http://127.0.0.1:8000"


class TestIsMcpEnabled:
    def test_local_mode_never_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
        cfg = load_config(auth_mode_override="local")
        assert cfg.is_mcp_enabled is False

    def test_multiuser_no_google_not_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SNORE_SESSION_SECRET", _SESSION_SECRET)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", _PUBLIC_BASE_URL)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        cfg = load_config(auth_mode_override="multiuser")
        assert cfg.is_mcp_enabled is False

    def test_multiuser_google_creds_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SNORE_SESSION_SECRET", _SESSION_SECRET)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", _PUBLIC_BASE_URL)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
        cfg = load_config(auth_mode_override="multiuser")
        assert cfg.is_mcp_enabled is True


# ---------------------------------------------------------------------------
# build_mcp_app returns None when disabled
# ---------------------------------------------------------------------------


class TestBuildMcpApp:
    def test_returns_none_in_local_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from snore.api.mcp_embed import build_mcp_app  # noqa: PLC0415

        cfg = load_config(auth_mode_override="local")
        assert build_mcp_app(cfg) is None

    def test_returns_none_when_google_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from snore.api.mcp_embed import build_mcp_app  # noqa: PLC0415

        monkeypatch.setenv("SNORE_SESSION_SECRET", _SESSION_SECRET)
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", _PUBLIC_BASE_URL)
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        cfg = load_config(auth_mode_override="multiuser")
        assert build_mcp_app(cfg) is None
