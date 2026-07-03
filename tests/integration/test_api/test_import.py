from unittest.mock import patch

import pytest

from fastapi import HTTPException, Request

from snore.api.routers.import_data import _require_localhost
from snore.services.schemas import ImportResult, ImportSource


class TestDetectSources:
    def test_remote_client_gets_403(self, api_client):
        """Default TestClient host is 'testclient', should get 403."""
        response = api_client.post("/api/v1/import/detect", json={"path": "/tmp"})
        assert response.status_code == 403

    def test_localhost_returns_empty_list_for_nonexistent_path(
        self, localhost_api_client
    ):
        """Localhost client with nonexistent path returns 200 with empty list."""
        response = localhost_api_client.post(
            "/api/v1/import/detect", json={"path": "/tmp/nonexistent_snore_path"}
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_localhost_returns_sources_shape(self, localhost_api_client):
        """Mocked sources have expected keys."""
        fake_sources = [
            ImportSource(
                parser_name="resmed",
                root_path="/mnt/sd",
                device_serial="12345",
            )
        ]
        with patch(
            "snore.api.routers.import_data.ImportService.detect_sources",
            return_value=fake_sources,
        ):
            response = localhost_api_client.post(
                "/api/v1/import/detect", json={"path": "/mnt/sd"}
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["parser_name"] == "resmed"
        assert data[0]["root_path"] == "/mnt/sd"


class TestImportUpload:
    def test_upload_small_file_returns_200(self, api_client):
        """Upload with mocked service returns 200 with ImportResult shape."""
        fake_result = ImportResult(
            total_imported=1,
            total_skipped=0,
            total_failed=0,
            sources=[],
            warnings=[],
        )
        with patch(
            "snore.api.routers.import_data.ImportService.import_from_upload",
            return_value=fake_result,
        ):
            response = api_client.post(
                "/api/v1/import",
                files=[
                    (
                        "files",
                        ("test.edf", b"fake edf content", "application/octet-stream"),
                    )
                ],
            )
        assert response.status_code == 200
        data = response.json()
        assert data["total_imported"] == 1
        assert "sources" in data

    def test_upload_size_limit_exceeded(self, api_client, monkeypatch):
        """Cumulative upload size exceeding limit returns 413."""
        monkeypatch.setattr("snore.api.routers.import_data.MAX_UPLOAD_BYTES", 10)
        response = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 20, "application/octet-stream"))],
        )
        assert response.status_code == 413

    def test_upload_path_traversal_forwarded_intact(self, api_client):
        """Filename with path traversal components is passed through to the service.

        The router uses `f.filename or "unknown"` — actual sanitization happens in
        ImportService.import_from_upload via _safe_relative_path. Verify the router
        forwards the filename as-is.
        """
        captured_args = {}

        def capture_upload(self, files, **kwargs):
            captured_args["files"] = files
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with patch(
            "snore.api.routers.import_data.ImportService.import_from_upload",
            capture_upload,
        ):
            response = api_client.post(
                "/api/v1/import",
                files=[
                    ("files", ("../../etc/passwd", b"evil", "application/octet-stream"))
                ],
            )
        assert response.status_code == 200
        # Router passes filename as-is; ImportService.import_from_upload handles sanitization
        assert captured_args["files"][0][0] == "../../etc/passwd"

    def test_too_many_files_returns_400(self, api_client, monkeypatch):
        """Exceeding MAX_UPLOAD_FILES causes Starlette to return 400."""
        monkeypatch.setattr("snore.api.routers.import_data.MAX_UPLOAD_FILES", 2)
        files = [
            ("files", (f"file{i}.edf", b"x", "application/octet-stream"))
            for i in range(3)
        ]
        response = api_client.post("/api/v1/import/", files=files)
        assert response.status_code == 400

    def test_no_files_returns_422(self, api_client):
        """Request with no 'files' field returns 422 with detail."""
        response = api_client.post(
            "/api/v1/import/",
            files=[("other", ("x.edf", b"x", "application/octet-stream"))],
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "No files provided"

    def test_nested_filename_forwarded_intact(self, api_client):
        """Nested filename with directory structure arrives at the service unchanged."""
        captured_args = {}

        def capture_upload(self, files, **kwargs):
            captured_args["files"] = files
            return ImportResult(
                total_imported=0,
                total_skipped=0,
                total_failed=0,
                sources=[],
                warnings=[],
            )

        with patch(
            "snore.api.routers.import_data.ImportService.import_from_upload",
            capture_upload,
        ):
            response = api_client.post(
                "/api/v1/import/",
                files=[
                    (
                        "files",
                        ("SDCARD/DATALOG/test.edf", b"x", "application/octet-stream"),
                    )
                ],
            )
        assert response.status_code == 200
        assert captured_args["files"][0][0] == "SDCARD/DATALOG/test.edf"


class TestPathImport:
    def test_non_localhost_gets_403(self, api_client):
        """Non-localhost client is rejected with 403."""
        response = api_client.post(
            "/api/v1/import/path",
            json={"sources": []},
        )
        assert response.status_code == 403

    def test_localhost_calls_import_sources_with_backup(self, localhost_api_client):
        """Localhost import/path calls import_sources with backup=True and returns result."""
        fake_result = ImportResult(
            total_imported=3, total_skipped=0, total_failed=0, sources=[], warnings=[]
        )
        with patch(
            "snore.api.routers.import_data.ImportService.import_sources",
            return_value=fake_result,
        ) as mock_import:
            response = localhost_api_client.post(
                "/api/v1/import/path",
                json={"sources": []},
            )
        assert response.status_code == 200
        assert response.json()["total_imported"] == 3
        mock_import.assert_called_once()
        assert mock_import.call_args[1]["backup"] is True

    def test_invalid_sources_shape_returns_422(self, localhost_api_client):
        """Malformed sources payload returns 422."""
        response = localhost_api_client.post(
            "/api/v1/import/path",
            json={"sources": [{"bad": "shape"}]},
        )
        assert response.status_code == 422


class TestRequireLocalhost:
    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "127.0.0.9", "::1", "::ffff:127.0.0.1"]
    )
    def test_loopback_hosts_allowed(self, host):
        request = Request({"type": "http", "client": (host, 12345)})
        _require_localhost(request)  # does not raise

    @pytest.mark.parametrize(
        "client", [("10.0.0.5", 12345), ("testclient", 50000), None]
    )
    def test_non_loopback_rejected(self, client):
        request = Request({"type": "http", "client": client})
        with pytest.raises(HTTPException) as exc_info:
            _require_localhost(request)
        assert exc_info.value.status_code == 403
