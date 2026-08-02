import json

from unittest.mock import AsyncMock, patch

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
    def test_upload_returns_202_with_job_id(self, api_client):
        """Upload returns 202 with a job_id for SSE progress tracking."""
        response = api_client.post(
            "/api/v1/import",
            files=[
                (
                    "files",
                    ("test.edf", b"fake edf content", "application/octet-stream"),
                )
            ],
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], str)
        assert len(data["job_id"]) > 0

    def test_upload_writes_files_to_temp_dir(self, api_client, monkeypatch):
        """Upload creates temp files that can be retrieved via the job store.

        _start_worker is patched to a no-op so the background worker never runs
        and its finally-cleanup_files() cannot race the file-existence assertions.
        These tests validate upload path layout and content, not worker execution.
        """
        import snore.api.routers.import_data as import_mod  # noqa: PLC0415

        from snore.api.import_jobs import get_job  # noqa: PLC0415

        monkeypatch.setattr(import_mod, "_start_worker", lambda job, root=None: None)

        response = api_client.post(
            "/api/v1/import",
            files=[
                (
                    "files",
                    (
                        "SDCARD/DATALOG/test.edf",
                        b"edf content",
                        "application/octet-stream",
                    ),
                )
            ],
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        job = get_job(job_id)
        assert job is not None
        assert job.temp_dir is not None
        assert (job.temp_dir / "SDCARD" / "DATALOG" / "test.edf").exists()
        assert (
            job.temp_dir / "SDCARD" / "DATALOG" / "test.edf"
        ).read_bytes() == b"edf content"

        # Cleanup
        import shutil  # noqa: PLC0415

        from snore.api.import_jobs import remove_job  # noqa: PLC0415

        shutil.rmtree(job.temp_dir, ignore_errors=True)
        remove_job(job_id)

    def test_upload_path_traversal_sanitized(self, api_client, monkeypatch):
        """Path traversal components are stripped from uploaded filenames.

        _start_worker is patched to a no-op so the background worker never runs
        and its finally-cleanup_files() cannot race the temp-dir rglob assertions.
        """
        import snore.api.routers.import_data as import_mod  # noqa: PLC0415

        from snore.api.import_jobs import get_job  # noqa: PLC0415

        monkeypatch.setattr(import_mod, "_start_worker", lambda job, root=None: None)

        response = api_client.post(
            "/api/v1/import",
            files=[
                ("files", ("../../etc/passwd", b"evil", "application/octet-stream"))
            ],
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        job = get_job(job_id)
        assert job is not None
        assert job.temp_dir is not None
        # All written files must be inside the temp dir
        tmp_root = job.temp_dir.resolve()
        for path in job.temp_dir.rglob("*"):
            assert path.resolve().is_relative_to(tmp_root)

        import shutil  # noqa: PLC0415

        from snore.api.import_jobs import remove_job  # noqa: PLC0415

        shutil.rmtree(job.temp_dir, ignore_errors=True)
        remove_job(job_id)

    def test_upload_size_limit_exceeded(self, api_client, monkeypatch):
        """Cumulative upload size exceeding limit returns 413."""
        monkeypatch.setattr("snore.api.routers.import_data.MAX_UPLOAD_BYTES", 10)
        response = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 20, "application/octet-stream"))],
        )
        assert response.status_code == 413

    def test_no_files_returns_422(self, api_client):
        """Request with no 'files' field returns 422 with detail."""
        response = api_client.post(
            "/api/v1/import/",
            files=[("other", ("x.edf", b"x", "application/octet-stream"))],
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "No files provided"


class TestImportProgress:
    def test_progress_nonexistent_job_returns_404(self, api_client):
        """GET progress for unknown job_id returns 404."""
        response = api_client.get("/api/v1/import/nonexistent/progress")
        assert response.status_code == 404

    def test_progress_stream_emits_events(self, api_client):
        """SSE stream emits progress events and a complete event."""
        fake_result = ImportResult(
            total_imported=1,
            total_skipped=0,
            total_failed=0,
            sources=[],
            warnings=[],
        )
        with (
            patch(
                "snore.api.routers.import_data.ImportService.detect_sources",
                return_value=[],
            ),
            patch(
                "snore.api.routers.import_data.ImportService.import_sources",
                new_callable=AsyncMock,
                return_value=fake_result,
            ),
        ):
            upload_response = api_client.post(
                "/api/v1/import",
                files=[
                    (
                        "files",
                        ("test.edf", b"fake content", "application/octet-stream"),
                    )
                ],
            )
            assert upload_response.status_code == 202
            job_id = upload_response.json()["job_id"]

            response = api_client.get(
                f"/api/v1/import/{job_id}/progress",
                headers={"Accept": "text/event-stream"},
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            body = response.text
            assert "event: complete" in body
            result_line = [
                line
                for line in body.split("\n")
                if line.startswith("data:") and "total_imported" in line
            ]
            assert len(result_line) == 1
            result_data = json.loads(result_line[0].removeprefix("data: "))
            assert result_data["result"]["total_imported"] == 1


class TestUploadBackupEnabled:
    """Upload job must call import_sources with backup=True (WS2 fix)."""

    def test_upload_job_calls_import_with_backup_true(self, api_client):
        """_run_import for UPLOAD type must pass backup=True to import_sources."""
        fake_result = ImportResult(
            total_imported=1,
            total_skipped=0,
            total_failed=0,
            sources=[],
            warnings=[],
        )
        with (
            patch(
                "snore.api.routers.import_data.ImportService.detect_sources",
                return_value=[],
            ),
            patch(
                "snore.api.routers.import_data.ImportService.import_sources",
                new_callable=AsyncMock,
                return_value=fake_result,
            ) as mock_import,
        ):
            upload_response = api_client.post(
                "/api/v1/import",
                files=[
                    (
                        "files",
                        ("test.edf", b"fake content", "application/octet-stream"),
                    )
                ],
            )
            assert upload_response.status_code == 202
            job_id = upload_response.json()["job_id"]

            api_client.get(
                f"/api/v1/import/{job_id}/progress",
                headers={"Accept": "text/event-stream"},
            )

        # Verify the upload path called import_sources with backup=True
        mock_import.assert_called_once()
        _, kwargs = mock_import.call_args
        assert kwargs.get("backup") is True, (
            "Upload route must call import_sources(backup=True); "
            f"got backup={kwargs.get('backup')!r}"
        )


class TestPathImport:
    def test_non_localhost_gets_403(self, api_client):
        """Non-localhost client is rejected with 403."""
        response = api_client.post(
            "/api/v1/import/path",
            json={"sources": []},
        )
        assert response.status_code == 403

    def test_localhost_returns_202_with_job_id(self, localhost_api_client):
        """Localhost import/path returns 202 with a job_id."""
        response = localhost_api_client.post(
            "/api/v1/import/path",
            json={"sources": []},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], str)

        from snore.api.import_jobs import get_job, remove_job

        job = get_job(data["job_id"])
        assert job is not None
        assert job.sources == []
        remove_job(data["job_id"])


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
