"""Integration tests for GET /api/v1/about."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient


class TestAboutEndpoint:
    def test_about_returns_200_with_expected_fields(self) -> None:
        """GET /api/v1/about returns 200 with all expected fields in local mode."""
        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/about")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "git_sha" in data
        assert "build_time" in data
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], float)
        assert "auth_mode" in data
        assert data["auth_mode"] == "Local (single-user)"
        assert "python_version" in data
        assert "sqlite_version" in data

    def test_about_returns_200_in_multiuser_mode_without_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /api/v1/about returns 200 even without a session cookie."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET",
            "test-secret-at-least-32-chars-long-abcdef",
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/about")
        assert resp.status_code == 200
        assert resp.json()["auth_mode"] == "Multi-user"

    def test_about_git_sha_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """git_sha field reflects SNORE_GIT_SHA env var when set."""
        monkeypatch.setenv("SNORE_GIT_SHA", "abc1234")

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/about")
        assert resp.json()["git_sha"] == "abc1234"

    def test_about_absent_from_openapi_schema(self) -> None:
        """/api/v1/about must not appear in the OpenAPI schema."""
        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        schema = client.get("/openapi.json").json()
        assert "/api/v1/about" not in schema.get("paths", {})
