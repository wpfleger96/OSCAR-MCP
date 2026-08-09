"""Integration tests for the /health endpoint and SPA fallback serving."""

from __future__ import annotations

from pathlib import Path

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


class TestSPAFallback:
    """Verify that the SPA fallback serves index.html for unknown paths but not
    for /api/ paths, which must always return their own error responses."""

    def _make_dist(self, tmp_path: Path) -> Path:
        """Create a minimal dist tree: assets/ dir + index.html."""
        (tmp_path / "assets").mkdir()
        (tmp_path / "index.html").write_text(
            "<html><body>SNORE SPA</body></html>", encoding="utf-8"
        )
        return tmp_path

    def test_root_returns_spa_index_html(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET / serves index.html (200 HTML) when ui/dist is present."""
        dist = self._make_dist(tmp_path)

        import snore.api.app as _app_module

        monkeypatch.setattr(_app_module, "_resolve_spa_dist", lambda: dist)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_api_404_not_overridden_by_spa(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /api/v1/nonexistent returns 404 JSON, not the SPA index.html."""
        dist = self._make_dist(tmp_path)

        import snore.api.app as _app_module

        monkeypatch.setattr(_app_module, "_resolve_spa_dist", lambda: dist)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/nonexistent")
        assert resp.status_code == 404
        assert "text/html" not in resp.headers.get("content-type", "")
