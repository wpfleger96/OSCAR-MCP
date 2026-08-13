"""Integration tests for the health import web API.

Upload, job lifecycle, field validation, and demo guard coverage for
POST /api/v1/import/ with import_type=health.
"""

from __future__ import annotations

import io
import zipfile

from unittest.mock import AsyncMock, patch

import pytest

from snore.services.schemas import HealthImportResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_HEALTH_RESULT = HealthImportResult(
    inserted=10,
    skipped=0,
    nights_recomputed=2,
    unknown_metrics={"HKQuantityTypeIdentifierStepCount": 1},
    dry_run=False,
)

_EMPTY_HEALTH_RESULT = HealthImportResult(
    inserted=0,
    skipped=10,
    nights_recomputed=0,
    dry_run=False,
)


def _make_health_zip(xml_content: bytes | None = None) -> bytes:
    """Build a minimal export.zip in-memory with the right member path."""
    content = xml_content or b"<HealthData><ExportDate/></HealthData>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("apple_health_export/export.xml", content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_client(temp_db, async_db_session, db_session):
    """TestClient where every request arrives from a demo-role actor.

    Uses make_test_client with an explicit demo ActorContext so that the
    dependency override wiring matches the api_client fixture exactly.
    """
    from snore.auth.actor import ActorContext, AuthMode, Role
    from tests.helpers.api_client import make_test_client

    demo_actor = ActorContext(
        user_id=1,
        profile_id=1,
        role=Role.DEMO,
        mode=AuthMode.MULTIUSER,
    )
    client = make_test_client(async_db_session, actor=demo_actor)
    # Suppress server exceptions so the test can inspect the 403 status.
    client.raise_server_exceptions = False
    return client


@pytest.fixture
def health_import_worker():
    """Start the import worker with _run_dispatch (routes to _run_health_import).

    Production uses _run_dispatch; the existing import_worker fixture uses
    _run_import which would reject HEALTH_UPLOAD jobs.  Teardown is handled by
    the autouse reset_import_job_store fixture.
    """
    from snore.api.import_jobs import start_import_worker
    from snore.api.import_worker import _run_dispatch

    start_import_worker(_run_dispatch)


# ---------------------------------------------------------------------------
# Upload lifecycle
# ---------------------------------------------------------------------------


class TestHealthUploadLifecycle:
    def test_upload_returns_202_with_job_id(self, api_client, health_import_worker):
        """Upload with import_type=health returns 202 and a non-empty job_id."""
        zip_bytes = _make_health_zip()
        with patch(
            "snore.api.import_worker.HealthImportService.import_file",
            new_callable=AsyncMock,
            return_value=_FIXTURE_HEALTH_RESULT,
        ):
            resp = api_client.post(
                "/api/v1/import/",
                data={"import_type": "health"},
                files=[("files", ("export.zip", zip_bytes, "application/zip"))],
            )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], str)
        assert len(data["job_id"]) > 0

    def test_upload_completes_with_correct_health_import_result(
        self, api_client, health_import_worker
    ):
        """Full upload → SSE stream → /import/jobs shows health_import_result fields.

        Verifies:
        - job_type == "health_upload"
        - stage == "done"
        - health_import_result.inserted == 10, nights_recomputed == 2
        - import_result is null (CPAP field; not populated for health jobs)
        """
        zip_bytes = _make_health_zip()
        with patch(
            "snore.api.import_worker.HealthImportService.import_file",
            new_callable=AsyncMock,
            return_value=_FIXTURE_HEALTH_RESULT,
        ):
            upload_resp = api_client.post(
                "/api/v1/import/",
                data={"import_type": "health"},
                files=[("files", ("export.zip", zip_bytes, "application/zip"))],
            )
            assert upload_resp.status_code == 202
            job_id = upload_resp.json()["job_id"]

            progress_resp = api_client.get(
                f"/api/v1/import/{job_id}/progress",
                headers={"Accept": "text/event-stream"},
            )

        assert progress_resp.status_code == 200
        assert "event: complete" in progress_resp.text

        jobs_resp = api_client.get("/api/v1/import/jobs")
        assert jobs_resp.status_code == 200
        jobs = jobs_resp.json()["jobs"]
        health_jobs = [j for j in jobs if j.get("job_type") == "health_upload"]
        assert len(health_jobs) >= 1

        j = health_jobs[0]
        assert j["state"] == "succeeded"
        assert j["stage"] == "done"
        assert j["import_result"] is None, (
            "import_result must be null for health_upload jobs"
        )
        result = j["health_import_result"]
        assert result is not None
        assert result["inserted"] == 10
        assert result["nights_recomputed"] == 2

    def test_reimport_shows_all_records_skipped(self, api_client, health_import_worker):
        """Re-uploading the same zip yields inserted=0, skipped=10 (dedup)."""
        zip_bytes = _make_health_zip()

        with patch(
            "snore.api.import_worker.HealthImportService.import_file",
            new_callable=AsyncMock,
            return_value=_EMPTY_HEALTH_RESULT,
        ):
            upload_resp = api_client.post(
                "/api/v1/import/",
                data={"import_type": "health"},
                files=[("files", ("export.zip", zip_bytes, "application/zip"))],
            )
            assert upload_resp.status_code == 202
            job_id = upload_resp.json()["job_id"]

            api_client.get(
                f"/api/v1/import/{job_id}/progress",
                headers={"Accept": "text/event-stream"},
            )

        jobs_resp = api_client.get("/api/v1/import/jobs")
        jobs = jobs_resp.json()["jobs"]
        j = next(x for x in jobs if x.get("job_id") == job_id)
        result = j["health_import_result"]
        assert result["inserted"] == 0
        assert result["skipped"] == 10


# ---------------------------------------------------------------------------
# Per-file size cap bypass
# ---------------------------------------------------------------------------


class TestPerFileSizeLimitBypass:
    def test_health_upload_bypasses_per_file_cap(self, api_client, monkeypatch):
        """A health zip larger than max_file_bytes gets 202 (uses max_upload_bytes cap).

        max_file_bytes=5 is set tiny, but health uploads are capped by
        max_upload_bytes=100_000. A 10-byte health zip is under the upload cap.
        """
        monkeypatch.setattr(
            "snore.api.routers.import_data._get_upload_limits",
            lambda: (100_000, 500, 5),
        )
        import snore.api.routers.import_data as _import_mod

        monkeypatch.setattr(
            _import_mod, "enqueue_for_execution", lambda job, root=None: None
        )

        zip_bytes = (
            b"x" * 10
        )  # 10 bytes > max_file_bytes(5), but < max_upload_bytes(100k)
        resp = api_client.post(
            "/api/v1/import/",
            data={"import_type": "health"},
            files=[("files", ("export.zip", zip_bytes, "application/zip"))],
        )
        assert resp.status_code == 202

    def test_cpap_upload_rejected_by_per_file_cap(self, api_client, monkeypatch):
        """A CPAP file larger than max_file_bytes returns 413 (per-file cap applies)."""
        monkeypatch.setattr(
            "snore.api.routers.import_data._get_upload_limits",
            lambda: (100_000, 500, 5),
        )

        # 10 bytes > max_file_bytes(5) → 413
        resp = api_client.post(
            "/api/v1/import/",
            files=[("files", ("data.edf", b"x" * 10, "application/octet-stream"))],
        )
        assert resp.status_code == 413
        assert "per-file limit" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestHealthUploadValidation:
    def test_batch_id_with_health_returns_422(self, api_client):
        """batch_id is not supported for health imports → 422."""
        zip_bytes = _make_health_zip()
        resp = api_client.post(
            "/api/v1/import/",
            data={"import_type": "health", "batch_id": "some-batch-123"},
            files=[("files", ("export.zip", zip_bytes, "application/zip"))],
        )
        assert resp.status_code == 422
        assert "Batch" in resp.json()["detail"]

    def test_batch_final_false_with_health_returns_422(self, api_client):
        """batch_final=false signals a streaming batch, not supported for health → 422."""
        zip_bytes = _make_health_zip()
        resp = api_client.post(
            "/api/v1/import/",
            data={"import_type": "health", "batch_final": "false"},
            files=[("files", ("export.zip", zip_bytes, "application/zip"))],
        )
        assert resp.status_code == 422

    def test_two_files_with_health_returns_422(self, api_client):
        """Health imports require exactly one file; two files → 422."""
        zip_bytes = _make_health_zip()
        resp = api_client.post(
            "/api/v1/import/",
            data={"import_type": "health"},
            files=[
                ("files", ("export.zip", zip_bytes, "application/zip")),
                ("files", ("extra.zip", zip_bytes, "application/zip")),
            ],
        )
        assert resp.status_code == 422
        assert "exactly one file" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Demo actor guard
# ---------------------------------------------------------------------------


class TestHealthUploadDemoGuard:
    def test_demo_actor_returns_403(self, demo_client):
        """A demo actor cannot upload health data — require_writable returns 403."""
        zip_bytes = _make_health_zip()
        resp = demo_client.post(
            "/api/v1/import/",
            data={"import_type": "health"},
            files=[("files", ("export.zip", zip_bytes, "application/zip"))],
        )
        assert resp.status_code == 403
