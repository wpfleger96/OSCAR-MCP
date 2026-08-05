"""Integration tests for the /health endpoint."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_200_in_multiuser_mode_without_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health returns 200 with {"status": "ok"} in multiuser mode, no session."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET",
            "test-secret-at-least-32-chars-long-abcdef",
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

        from snore.api.app import create_app

        app = create_app()
        # No 'with' — skips lifespan (no DB needed for /health).
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_absent_from_openapi_schema(self) -> None:
        """/health must not appear in the OpenAPI schema (include_in_schema=False)."""
        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        schema = client.get("/openapi.json").json()
        assert "/health" not in schema.get("paths", {})
