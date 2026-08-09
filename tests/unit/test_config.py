"""Unit tests for load_config / AppConfig.

Covers the env-var parsing helpers and AppConfig fields that are exercised
without a full lifespan setup.  All tests use local auth mode so that
SNORE_SESSION_SECRET and SNORE_PUBLIC_BASE_URL are not required.
"""

from __future__ import annotations

import pytest

from snore.api.config import ConfigError, load_config, reset_config


@pytest.fixture(autouse=True)
def _reset_config():
    """Clear the cached config singleton before and after each test."""
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# SNORE_ANALYSIS_MAX_WORKERS
# ---------------------------------------------------------------------------


def test_analysis_max_workers_defaults_to_4():
    cfg = load_config(auth_mode_override="local")
    assert cfg.analysis_max_workers == 4


def test_analysis_max_workers_env_override(monkeypatch):
    monkeypatch.setenv("SNORE_ANALYSIS_MAX_WORKERS", "8")
    cfg = load_config(auth_mode_override="local")
    assert cfg.analysis_max_workers == 8


def test_analysis_max_workers_zero_raises(monkeypatch):
    monkeypatch.setenv("SNORE_ANALYSIS_MAX_WORKERS", "0")
    with pytest.raises(ConfigError, match="SNORE_ANALYSIS_MAX_WORKERS"):
        load_config(auth_mode_override="local")


def test_analysis_max_workers_non_integer_raises(monkeypatch):
    monkeypatch.setenv("SNORE_ANALYSIS_MAX_WORKERS", "fast")
    with pytest.raises(ConfigError, match="SNORE_ANALYSIS_MAX_WORKERS"):
        load_config(auth_mode_override="local")


# ---------------------------------------------------------------------------
# SNORE_MAX_JOBS_PER_USER / SNORE_MAX_JOBS_GLOBAL (regression guard)
# ---------------------------------------------------------------------------


def test_max_jobs_per_user_defaults_to_3():
    cfg = load_config(auth_mode_override="local")
    assert cfg.max_jobs_per_user == 3


def test_max_jobs_global_defaults_to_10():
    cfg = load_config(auth_mode_override="local")
    assert cfg.max_jobs_global == 10


# ---------------------------------------------------------------------------
# SNORE_BOOTSTRAP_ADMIN_EMAIL
# ---------------------------------------------------------------------------


def test_bootstrap_admin_email_defaults_to_none():
    cfg = load_config(auth_mode_override="local")
    assert cfg.bootstrap_admin_email is None


def test_bootstrap_admin_email_normalized(monkeypatch):
    monkeypatch.setenv("SNORE_BOOTSTRAP_ADMIN_EMAIL", "  Admin@Example.COM ")
    cfg = load_config(auth_mode_override="local")
    assert cfg.bootstrap_admin_email == "admin@example.com"


def test_bootstrap_admin_email_without_at_sign_raises(monkeypatch):
    monkeypatch.setenv("SNORE_BOOTSTRAP_ADMIN_EMAIL", "notanemail")
    with pytest.raises(ConfigError, match="SNORE_BOOTSTRAP_ADMIN_EMAIL"):
        load_config(auth_mode_override="local")


# ---------------------------------------------------------------------------
# SNORE_MCP_BASE_URL / is_mcp_enabled
# ---------------------------------------------------------------------------


def _multiuser_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal env for multiuser mode (secret + public base URL)."""
    monkeypatch.setenv("SNORE_SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "https://snore.example.com")


def _google_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "dummy-secret")


def test_mcp_base_url_defaults_to_empty():
    cfg = load_config(auth_mode_override="local")
    assert cfg.mcp_base_url == ""
    assert cfg.is_mcp_enabled is False


def test_mcp_base_url_is_stripped(monkeypatch):
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "  https://mcp.example.com  ")
    cfg = load_config(auth_mode_override="local")
    assert cfg.mcp_base_url == "https://mcp.example.com"


def test_mcp_base_url_malformed_raises(monkeypatch):
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com/path")
    with pytest.raises(ConfigError, match="SNORE_MCP_BASE_URL"):
        load_config(auth_mode_override="local")


def test_mcp_base_url_non_loopback_http_raises(monkeypatch):
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "http://mcp.example.com")
    with pytest.raises(ConfigError, match="SNORE_MCP_BASE_URL"):
        load_config(auth_mode_override="local")


def test_mcp_base_url_loopback_http_accepted(monkeypatch):
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "http://127.0.0.1:8000")
    cfg = load_config(auth_mode_override="local")
    assert cfg.mcp_base_url == "http://127.0.0.1:8000"


def test_is_mcp_enabled_true_when_all_prerequisites_met(monkeypatch):
    _multiuser_env(monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
    cfg = load_config(auth_mode_override="multiuser")
    assert cfg.is_mcp_enabled is True


def test_is_mcp_enabled_false_in_local_mode(monkeypatch):
    _google_env(monkeypatch)
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
    cfg = load_config(auth_mode_override="local")
    assert cfg.is_mcp_enabled is False


def test_is_mcp_enabled_false_without_base_url(monkeypatch):
    _multiuser_env(monkeypatch)
    _google_env(monkeypatch)
    cfg = load_config(auth_mode_override="multiuser")
    assert cfg.is_mcp_enabled is False


def test_is_mcp_enabled_false_without_google_credentials(monkeypatch):
    _multiuser_env(monkeypatch)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("SNORE_MCP_BASE_URL", "https://mcp.example.com")
    cfg = load_config(auth_mode_override="multiuser")
    assert cfg.is_mcp_enabled is False
