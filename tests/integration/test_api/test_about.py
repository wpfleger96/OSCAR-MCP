"""Integration tests for GET /api/v1/about."""

from __future__ import annotations

import os
import time

from pathlib import Path

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


class TestAboutUpdatePending:
    def test_update_pending_false_when_no_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Absent marker -> update_pending is False and update_pending_since is None."""
        marker = tmp_path / "deploy-deferred.pending"
        # marker does not exist

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/api/v1/about").json()
        assert data["update_pending"] is False
        assert data["update_pending_since"] is None

    def test_update_pending_true_when_marker_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Fresh marker with ISO8601 content -> True; since round-trips exactly."""
        marker = tmp_path / "deploy-deferred.pending"
        since_str = "2026-08-13T20:00:00+00:00"
        marker.write_text(since_str)

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/api/v1/about").json()
        assert data["update_pending"] is True
        assert data["update_pending_since"] == "2026-08-13T20:00:00+00:00"

    def test_update_pending_since_parses_hook_z_suffix_format(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Marker written by the hook (Z suffix + trailing newline) parses correctly."""
        marker = tmp_path / "deploy-deferred.pending"
        marker.write_text("2026-08-13T20:00:00Z\n")

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/api/v1/about").json()
        assert data["update_pending"] is True
        assert data["update_pending_since"] == "2026-08-13T20:00:00+00:00"

    def test_update_pending_true_just_under_freshness_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Marker aged 10s under the 30-minute boundary is still treated as fresh."""
        marker = tmp_path / "deploy-deferred.pending"
        marker.write_text("2026-08-13T20:00:00+00:00")
        # Age the mtime to just inside the freshness window (30 min - 10 s).
        # The 10s buffer prevents a race with request latency in slow CI.
        fresh_time = time.time() - (30 * 60 - 10)
        os.utime(marker, (fresh_time, fresh_time))

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/api/v1/about").json()
        assert data["update_pending"] is True

    def test_update_pending_since_fallback_when_content_malformed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Malformed marker content -> still True; since is a non-null string (mtime fallback)."""
        marker = tmp_path / "deploy-deferred.pending"
        marker.write_text("garbage")

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/api/v1/about").json()
        assert data["update_pending"] is True
        assert isinstance(data["update_pending_since"], str)
        assert data["update_pending_since"] != ""

    def test_update_pending_false_when_marker_stale(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Marker older than 30 minutes -> False and None."""
        marker = tmp_path / "deploy-deferred.pending"
        marker.write_text("2026-08-13T18:00:00+00:00")
        # Age the mtime by 31 minutes
        stale_time = time.time() - (31 * 60)
        os.utime(marker, (stale_time, stale_time))

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        data = client.get("/api/v1/about").json()
        assert data["update_pending"] is False
        assert data["update_pending_since"] is None

    def test_startup_clears_deploy_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """App startup removes the deploy-deferred marker if it exists."""
        monkeypatch.setenv("SNORE_DB_PATH", str(tmp_path / "snore-test.db"))

        marker = tmp_path / "deploy-deferred.pending"
        marker.write_text("2026-08-13T20:00:00+00:00")
        assert marker.exists()

        import snore.constants as constants_mod

        monkeypatch.setattr(constants_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        with TestClient(app, raise_server_exceptions=True):
            # Lifespan has run; marker should be gone
            assert not marker.exists()

    def test_update_fields_hidden_in_multiuser_without_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """In multiuser mode without a session, update fields are hidden (False/None)."""
        monkeypatch.setenv("SNORE_AUTH_MODE", "multiuser")
        monkeypatch.setenv(
            "SNORE_SESSION_SECRET",
            "test-secret-at-least-32-chars-long-abcdef",
        )
        monkeypatch.setenv("SNORE_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

        marker = tmp_path / "deploy-deferred.pending"
        marker.write_text("2026-08-13T20:00:00+00:00")

        import snore.api.routers.about as about_mod

        monkeypatch.setattr(about_mod, "DEFAULT_DEPLOY_DEFERRED_MARKER", marker)

        from snore.api.app import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/v1/about")
        assert resp.status_code == 200
        data = resp.json()
        assert data["update_pending"] is False
        assert data["update_pending_since"] is None
