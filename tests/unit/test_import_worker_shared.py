"""Unit tests for the shared worker runner (_run_job / _WorkerSpec).

Focus on the per-spec divergence that the shared 10-step runner must preserve:
the terminal "complete" payload carries analysis linkage for import/rescan jobs
but never for health imports. All DB writes and service calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from snore.api.import_jobs import ImportJob, JobState, JobType, create_job
from snore.api.import_worker import (
    _run_health_import,
    _run_import,
    _run_rescan,
)
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


class TestRunJobCancelAndErrorBranches:
    """The three non-happy-path branches _run_job routes cancellation and
    failure through, verified end-to-end (payload + terminal state)."""

    def test_early_cancel_yields_cancel_payload_not_error(self, tmp_path):
        """Cancellation detected during acquire raises _CancelledEarly, which
        _finish turns into the CANCELLED payload — never an _client_safe_error
        "error" message."""
        (tmp_path / "data.bin").write_bytes(b"x")
        job = create_job(JobType.UPLOAD, owner_user_id=1, temp_dir=tmp_path)
        job.target_profile_id = 42
        job._cancel_flag = True  # cancellation requested before import commits

        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources"
            ) as mock_detect,
            patch(
                "snore.api.import_worker.ImportService.import_sources",
                new_callable=AsyncMock,
            ) as mock_import,
        ):
            _run_import(job, tmp_path)

        assert job.state == JobState.CANCELLED
        # Early cancel fires before any source detection or import work.
        mock_detect.assert_not_called()
        mock_import.assert_not_called()
        data = _terminal_data(job)
        assert data["message"] == "Cancelled"
        # Import never committed, so no import_result is embedded.
        assert "import_committed" not in data

    def test_post_import_cancel_marks_cancelled_with_committed_result(self, tmp_path):
        """Cancellation that lands after the import commits still yields CANCELLED,
        and the payload carries the committed import result."""
        (tmp_path / "data.bin").write_bytes(b"x")
        job = create_job(JobType.UPLOAD, owner_user_id=1, temp_dir=tmp_path)
        job.target_profile_id = 42

        async def _import_then_cancel(*_args, **_kwargs):
            job._cancel_flag = True  # cancelled after the heavy import returns
            return _fake_import_result()

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
                side_effect=_import_then_cancel,
            ),
            patch("snore.api.analysis_jobs.enqueue") as mock_enqueue,
        ):
            _run_import(job, tmp_path)

        assert job.state == JobState.CANCELLED
        mock_enqueue.assert_not_called()  # cancel short-circuits before analysis
        data = _terminal_data(job)
        assert data["message"] == "Cancelled"
        assert data["import_committed"] is True

    def test_exception_yields_error_terminal_with_redacted_path(self, tmp_path):
        """A failure during import ends FAILED with a client-safe message that
        strips filesystem paths."""
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
                side_effect=ValueError("parse failed at /var/spool/secret/file.edf"),
            ),
        ):
            _run_import(job, tmp_path)

        assert job.state == JobState.FAILED
        data = _terminal_data(job)
        assert "/var/spool/secret" not in data["message"]
        assert "[path redacted]" in data["message"]
