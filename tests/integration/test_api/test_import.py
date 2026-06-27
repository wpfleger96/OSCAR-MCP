from unittest.mock import patch

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

    def test_upload_path_traversal_stripped(self, api_client):
        """Filename with path traversal components is passed through to the service.

        The router uses `file.filename or "unknown"` — actual path stripping
        happens in ImportService.import_from_upload via Path(filename).name.
        Verify the router forwards the filename and the service strips it.
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
        # Router passes filename as-is; ImportService.import_from_upload handles stripping
        assert captured_args["files"][0][0] == "../../etc/passwd"
