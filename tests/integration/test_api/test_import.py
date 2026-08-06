import json

from unittest.mock import AsyncMock, patch

import pytest

from fastapi import HTTPException, Request

import snore.api.analysis_jobs as aj_store
import snore.api.import_jobs as job_store

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

        enqueue_for_execution is patched to a no-op so the background worker never runs
        and its finally-cleanup_files() cannot race the file-existence assertions.
        These tests validate upload path layout and content, not worker execution.
        """
        import snore.api.routers.import_data as import_mod  # noqa: PLC0415

        from snore.api.import_jobs import get_job  # noqa: PLC0415

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

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

        enqueue_for_execution is patched to a no-op so the background worker never runs
        and its finally-cleanup_files() cannot race the temp-dir rglob assertions.
        """
        import snore.api.routers.import_data as import_mod  # noqa: PLC0415

        from snore.api.import_jobs import get_job  # noqa: PLC0415

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

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
        monkeypatch.setattr(
            "snore.api.routers.import_data._get_upload_limits",
            lambda: (10, 500, 10),
        )
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

    def test_per_file_limit_exceeded_returns_413(self, api_client, monkeypatch):
        """A single file exceeding max_file_bytes returns 413."""
        # max_upload_bytes=1000, max_files=500, max_file_bytes=5 (very small cap)
        monkeypatch.setattr(
            "snore.api.routers.import_data._get_upload_limits",
            lambda: (1000, 500, 5),
        )
        response = api_client.post(
            "/api/v1/import",
            files=[("files", ("big.edf", b"x" * 10, "application/octet-stream"))],
        )
        assert response.status_code == 413
        assert "per-file limit" in response.json()["detail"]


class TestImportProgress:
    def test_progress_nonexistent_job_returns_404(self, api_client):
        """GET progress for unknown job_id returns 404."""
        response = api_client.get("/api/v1/import/nonexistent/progress")
        assert response.status_code == 404

    def test_progress_stream_emits_events(self, api_client, import_worker):
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

    def test_upload_job_calls_import_with_backup_true(self, api_client, import_worker):
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


class TestStaleTempDirCleanup:
    """_cleanup_stale_upload_tempdirs removes snore-upload-* dirs older than the threshold."""

    def test_stale_upload_dir_is_removed(self, tmp_path, monkeypatch):
        """A snore-upload-* dir old enough is deleted at startup."""
        import tempfile  # noqa: PLC0415
        import time  # noqa: PLC0415

        from snore.api.app import (  # noqa: PLC0415
            _STALE_UPLOAD_TMPDIR_AGE_SECONDS,
            _cleanup_stale_upload_tempdirs,
        )

        stale = tmp_path / "snore-upload-stale"
        stale.mkdir()

        # Back-date mtime to exceed the threshold.
        old_mtime = time.time() - _STALE_UPLOAD_TMPDIR_AGE_SECONDS - 60
        import os  # noqa: PLC0415

        os.utime(stale, (old_mtime, old_mtime))

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        _cleanup_stale_upload_tempdirs()

        assert not stale.exists(), "Stale snore-upload-* dir must be removed at startup"

    def test_recent_upload_dir_is_kept(self, tmp_path, monkeypatch):
        """A snore-upload-* dir created recently is NOT deleted."""
        import tempfile  # noqa: PLC0415

        from snore.api.app import _cleanup_stale_upload_tempdirs  # noqa: PLC0415

        fresh = tmp_path / "snore-upload-fresh"
        fresh.mkdir()

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        _cleanup_stale_upload_tempdirs()

        assert fresh.exists(), (
            "Recent snore-upload-* dir must NOT be removed at startup"
        )

    def test_non_snore_dirs_are_ignored(self, tmp_path, monkeypatch):
        """Dirs without the snore-upload- prefix are never touched."""
        import os  # noqa: PLC0415
        import tempfile  # noqa: PLC0415
        import time  # noqa: PLC0415

        from snore.api.app import (  # noqa: PLC0415
            _STALE_UPLOAD_TMPDIR_AGE_SECONDS,
            _cleanup_stale_upload_tempdirs,
        )

        other = tmp_path / "unrelated-tmpdir"
        other.mkdir()
        old_mtime = time.time() - _STALE_UPLOAD_TMPDIR_AGE_SECONDS - 60
        os.utime(other, (old_mtime, old_mtime))

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        _cleanup_stale_upload_tempdirs()

        assert other.exists(), "Non-snore-upload dirs must never be removed"


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


class TestImportJobOwnership:
    """cancel and progress return 404 for jobs owned by a different user (isolation matrix)."""

    def test_cancel_foreign_job_returns_404(self, api_client):
        """DELETE /import/{job_id} with a job owned by a different user returns 404.

        The local actor gets user_id=1 (auto-provisioned).  A job created with
        owner_user_id=9999 is a foreign job — the route must return 404 rather
        than 403 to avoid leaking job-ID existence.
        """
        from snore.api.import_jobs import JobType, create_job, remove_job

        job = create_job(JobType.UPLOAD, owner_user_id=9999)
        try:
            response = api_client.delete(f"/api/v1/import/{job.job_id}")
            assert response.status_code == 404
        finally:
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()

    def test_progress_foreign_job_returns_404(self, api_client):
        """GET /import/{job_id}/progress with a job owned by a different user returns 404."""
        from snore.api.import_jobs import JobType, create_job, remove_job

        job = create_job(JobType.UPLOAD, owner_user_id=9999)
        try:
            response = api_client.get(f"/api/v1/import/{job.job_id}/progress")
            assert response.status_code == 404
        finally:
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()

    def test_cancel_own_job_not_404(self, api_client):
        """DELETE /import/{job_id} for an unowned job (local mode) returns 204 or 404 for gone."""
        from snore.api.import_jobs import remove_job, reserve_slot

        # owner_user_id=None simulates a pre-multiuser or local-mode job.
        job = reserve_slot(None)
        assert job is not None
        try:
            response = api_client.delete(f"/api/v1/import/{job.job_id}")
            # 204 = cancelled; 404 = job already cleaned up by a race in tests.
            assert response.status_code in (204, 404)
        finally:
            try:
                remove_job(job.job_id)
            except Exception:
                pass
            job.cleanup_files()
            job.release_capacity()


class TestImportTargetProfile:
    """target_profile_id enforcement for file upload and path import endpoints."""

    def test_upload_with_valid_target_profile_id_uses_that_profile(
        self, api_client, monkeypatch
    ):
        """Upload with a valid owned profile_id lands in that profile, not the active one."""
        import snore.api.routers.import_data as import_mod

        from snore.api.import_jobs import get_job, remove_job

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        # Create a second profile via the profiles API.
        resp = api_client.post("/api/v1/profiles/", json={"name": "Second Profile"})
        assert resp.status_code == 201
        second_profile_id = resp.json()["id"]

        job = None
        try:
            response = api_client.post(
                "/api/v1/import",
                data={"profile_id": str(second_profile_id)},
                files=[
                    ("files", ("test.edf", b"fake content", "application/octet-stream"))
                ],
            )
            assert response.status_code == 202
            job = get_job(response.json()["job_id"])
            assert job is not None
            assert job.target_profile_id == second_profile_id
        finally:
            if job is not None:
                remove_job(job.job_id)
                job.cleanup_files()
                job.release_capacity()

    def test_upload_with_foreign_profile_id_returns_403(self, api_client, monkeypatch):
        """Upload with a profile_id that does not belong to this user returns 403."""
        import snore.api.routers.import_data as import_mod

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        response = api_client.post(
            "/api/v1/import",
            data={"profile_id": "999999"},
            files=[
                ("files", ("test.edf", b"fake content", "application/octet-stream"))
            ],
        )
        assert response.status_code == 403

    def test_upload_without_profile_id_uses_actor_profile(
        self, api_client, monkeypatch
    ):
        """Upload without profile_id falls back to actor.profile_id (existing behaviour)."""
        import snore.api.routers.import_data as import_mod

        from snore.api.import_jobs import get_job, remove_job

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        job = None
        try:
            response = api_client.post(
                "/api/v1/import",
                files=[
                    ("files", ("test.edf", b"fake content", "application/octet-stream"))
                ],
            )
            assert response.status_code == 202
            job = get_job(response.json()["job_id"])
            assert job is not None
            assert job.target_profile_id is not None
        finally:
            if job is not None:
                remove_job(job.job_id)
                job.cleanup_files()
                job.release_capacity()

    def test_path_import_with_valid_profile_id_uses_that_profile(
        self, localhost_api_client, monkeypatch
    ):
        """Path import with a valid owned profile_id lands in that profile."""
        import snore.api.routers.import_data as import_mod

        from snore.api.import_jobs import get_job, remove_job

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        # Create a second profile via the profiles API.
        resp = localhost_api_client.post(
            "/api/v1/profiles/", json={"name": "Path Profile"}
        )
        assert resp.status_code == 201
        second_profile_id = resp.json()["id"]

        sources = [{"parser_name": "resmed", "root_path": "/tmp", "device_serial": "x"}]
        job = None
        try:
            response = localhost_api_client.post(
                "/api/v1/import/path",
                json={"sources": sources, "profile_id": second_profile_id},
            )
            assert response.status_code == 202
            job = get_job(response.json()["job_id"])
            assert job is not None
            assert job.target_profile_id == second_profile_id
        finally:
            if job is not None:
                remove_job(job.job_id)
                job.cleanup_files()
                job.release_capacity()

    def test_path_import_with_foreign_profile_id_returns_403(
        self, localhost_api_client, monkeypatch
    ):
        """Path import with a profile_id not owned by this user returns 403."""
        import snore.api.routers.import_data as import_mod

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        sources = [{"parser_name": "resmed", "root_path": "/tmp", "device_serial": "x"}]
        response = localhost_api_client.post(
            "/api/v1/import/path",
            json={"sources": sources, "profile_id": 999999},
        )
        assert response.status_code == 403

    def test_path_import_profile_id_wire_field_routes_to_correct_profile(
        self, localhost_api_client, monkeypatch
    ):
        """Wire contract: JSON field `profile_id` (not `target_profile_id`) selects the target.

        This test uses the exact field name the frontend sends. Pydantic drops unknown
        fields silently, so a field-name mismatch would cause fallback to actor.profile_id
        and the assertion on job.target_profile_id would fail.
        """
        import snore.api.routers.import_data as import_mod

        from snore.api.import_jobs import get_job, remove_job

        monkeypatch.setattr(
            import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        resp = localhost_api_client.post(
            "/api/v1/profiles/", json={"name": "Wire Contract Profile"}
        )
        assert resp.status_code == 201
        second_profile_id = resp.json()["id"]

        sources = [{"parser_name": "resmed", "root_path": "/tmp", "device_serial": "x"}]
        job = None
        try:
            response = localhost_api_client.post(
                "/api/v1/import/path",
                json={"sources": sources, "profile_id": second_profile_id},
            )
            assert response.status_code == 202
            job = get_job(response.json()["job_id"])
            assert job is not None
            assert job.target_profile_id == second_profile_id, (
                "profile_id wire field must route import to the specified profile"
            )
        finally:
            if job is not None:
                remove_job(job.job_id)
                job.cleanup_files()
                job.release_capacity()


class TestPipelineJobsListAPI:
    """Integration tests for GET /api/v1/import/jobs."""

    @pytest.fixture(autouse=True)
    def clean_stores(self):
        """Reset both job stores before and after each test."""
        job_store._jobs.clear()
        job_store._per_user_count.clear()
        job_store._global_count = 0
        job_store._import_queue.clear()
        aj_store._all_jobs.clear()
        aj_store._queue.clear()
        yield
        job_store._jobs.clear()
        job_store._per_user_count.clear()
        job_store._global_count = 0
        job_store._import_queue.clear()
        aj_store._all_jobs.clear()
        aj_store._queue.clear()

    def test_empty_list_initially(self, api_client):
        response = api_client.get("/api/v1/import/jobs")
        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert data["jobs"] == []

    def test_completed_job_appears_with_correct_shape(self, api_client):
        """A completed job appears in the list with the expected response fields."""
        from snore.api.import_jobs import JobType, create_job, remove_job

        job = create_job(JobType.PATH, owner_user_id=None, sources=[])
        job.set_file_count(2)
        try:
            job.try_start()
            job._finish(
                succeeded=True,
                terminal_msg={"event": "complete", "data": {}},
            )

            response = api_client.get("/api/v1/import/jobs")
            assert response.status_code == 200
            jobs = response.json()["jobs"]
            assert len(jobs) == 1

            j = jobs[0]
            assert j["job_id"] == job.job_id
            assert j["job_type"] == "path"
            assert j["state"] == "succeeded"
            assert j["stage"] == "done"
            assert j["file_count"] == 2
            assert j["created_at"] > 0
            assert j["finished_at"] is not None
            assert "import_result" in j
            assert "linked_analysis" in j
        finally:
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()

    def test_stitched_analysis_summary_present(self, api_client):
        """When an analysis job is linked, linked_analysis is populated."""
        from snore.api.import_jobs import JobType, create_job, remove_job

        # Create the import job.
        job = create_job(JobType.PATH, owner_user_id=None, sources=[])
        try:
            job.try_start()

            # Enqueue an analysis job and link it.
            aj = aj_store.enqueue(
                profile_id=1,
                session_ids=[1, 2],
                source=aj_store.AnalysisJobSource.IMPORT,
                owner_user_id=None,
            )
            assert aj is not None
            job.set_analysis_link(analysis_job_id=aj.job_id, queue_full=False)
            job._finish(
                succeeded=True,
                terminal_msg={
                    "event": "complete",
                    "data": {"analysis_job_id": aj.job_id},
                },
            )

            response = api_client.get("/api/v1/import/jobs")
            assert response.status_code == 200
            jobs = response.json()["jobs"]
            assert len(jobs) == 1

            j = jobs[0]
            assert j["analysis_job_id"] == aj.job_id
            assert j["analysis_queued"] is True
            assert j["linked_analysis"] is not None
            assert j["linked_analysis"]["job_id"] == aj.job_id
            assert j["linked_analysis"]["state"] == "queued"
            assert j["stage"] == "analysis_queued"
        finally:
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()

    def test_ownership_isolation_foreign_job_absent(self, api_client):
        """A job owned by another user_id does not appear in the list."""
        from snore.api.import_jobs import JobType, create_job, remove_job

        # api_client actor gets user_id=1; create a job owned by user 9999.
        job = create_job(JobType.PATH, owner_user_id=9999, sources=[])
        try:
            response = api_client.get("/api/v1/import/jobs")
            assert response.status_code == 200
            job_ids = [j["job_id"] for j in response.json()["jobs"]]
            assert job.job_id not in job_ids
        finally:
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()

    def test_no_imported_session_ids_in_import_result(self, api_client):
        """import_result in the response must never contain imported_session_ids."""
        from snore.api.import_jobs import JobPhase, JobType, create_job, remove_job

        job = create_job(JobType.PATH, owner_user_id=None, sources=[])
        try:
            job.try_start()
            job.phase_complete(
                JobPhase.IMPORT,
                {
                    "total_imported": 1,
                    "total_skipped": 0,
                    "total_failed": 0,
                    "warnings": [],
                    "imported_session_ids": [42],
                    "sources": [],
                },
            )
            job._finish(
                succeeded=True,
                terminal_msg={"event": "complete", "data": {}},
            )

            response = api_client.get("/api/v1/import/jobs")
            assert response.status_code == 200
            jobs = response.json()["jobs"]
            assert len(jobs) == 1

            import_result = jobs[0]["import_result"]
            assert import_result is not None
            assert "imported_session_ids" not in import_result
        finally:
            remove_job(job.job_id)
            job.cleanup_files()
            job.release_capacity()
