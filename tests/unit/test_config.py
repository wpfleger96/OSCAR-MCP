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
