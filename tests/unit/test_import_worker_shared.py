"""Unit tests for the shared worker runner (_run_job / _WorkerSpec).

Focus on the per-spec divergence that the shared 10-step runner must preserve:
the terminal "complete" payload carries analysis linkage for import/rescan jobs
but never for health imports. All DB writes and service calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from snore.api.import_jobs import ImportJob, JobState, JobType, create_job
from snore.api.import_worker import _run_health_import, _run_import, _run_rescan
from snore.services.schemas import ImportResult

_PATCH_UPSERT = patch(
    "snore.api.import_worker._upsert_job_record",
    new_callable=AsyncMock,
)
_PATCH_RELEASE = patch.object(ImportJob, "release_capacity")
_PATCH_SHUTDOWN = patch(
    "snore.api.import_jobs.is_shutdown_in_progress", return_value=False
)


def _fake_import_result(n: int = 2) -> ImportResult:
    return ImportResult(
        total_imported=n,
        total_skipped=0,
        total_failed=0,
        sources=[],
        warnings=[],
        imported_session_ids=list(range(1, n + 1)),
    )


def _terminal_data(job: ImportJob) -> dict:
    """Read the terminal SSE payload the run stored (job is already terminal)."""
    ch = job.attach_observer()
    msg = ch.get(timeout=1.0)
    assert msg is not None, "expected a pre-loaded terminal message"
    return msg["data"]


class TestTerminalExtraShape:
    def test_import_terminal_carries_analysis_job_id(self, tmp_path):
        mock_aj = MagicMock()
        mock_aj.job_id = "analysis-import"
        (tmp_path / "data.bin").write_bytes(b"x")
        job = create_job(JobType.UPLOAD, owner_user_id=1, temp_dir=tmp_path)
        job.target_profile_id = 42

        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources",
                return_value=[MagicMock()],
            ),
            patch(
                "snore.api.import_worker.ImportService.import_sources",
                new_callable=AsyncMock,
                return_value=_fake_import_result(),
            ),
            patch("snore.api.analysis_jobs.enqueue", return_value=mock_aj),
        ):
            _run_import(job, tmp_path)

        assert job.state == JobState.SUCCEEDED
        data = _terminal_data(job)
        assert data["analysis_job_id"] == "analysis-import"
        assert "result" in data

    def test_rescan_terminal_carries_analysis_job_id(self, tmp_path):
        mock_aj = MagicMock()
        mock_aj.job_id = "analysis-rescan"
        job = create_job(JobType.RESCAN, owner_user_id=1)
        job.target_profile_id = 42

        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources",
                return_value=[MagicMock()],
            ),
            patch(
                "snore.api.import_worker.ImportService.import_sources",
                new_callable=AsyncMock,
                return_value=_fake_import_result(),
            ),
            patch("snore.api.analysis_jobs.enqueue", return_value=mock_aj),
        ):
            _run_rescan(job, tmp_path)

        assert job.state == JobState.SUCCEEDED
        data = _terminal_data(job)
        assert data["analysis_job_id"] == "analysis-rescan"

    def test_health_terminal_lacks_analysis_job_id(self, tmp_path):
        (tmp_path / "export.zip").write_bytes(b"x")
        job = create_job(JobType.HEALTH_UPLOAD, owner_user_id=1, temp_dir=tmp_path)
        job.target_profile_id = 42

        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.HealthImportService.import_file",
                new_callable=AsyncMock,
                return_value=_fake_import_result(),
            ),
            patch("snore.api.analysis_jobs.enqueue") as mock_enqueue,
        ):
            _run_health_import(job, tmp_path)

        assert job.state == JobState.SUCCEEDED
        mock_enqueue.assert_not_called()
        data = _terminal_data(job)
        assert "analysis_job_id" not in data
        assert "analysis_queued" not in data
        assert "result" in data
