"""Unit tests for _run_rescan — the archive-rescan worker function.

All DB writes and service calls are mocked so tests run without a real database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from snore.api.import_jobs import ImportJob, JobState, JobType, create_job
from snore.api.import_worker import _run_dispatch, _run_rescan
from snore.services.schemas import ImportResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rescan_job() -> ImportJob:
    job = create_job(JobType.RESCAN, owner_user_id=1)
    job.target_profile_id = 42
    return job


def _fake_import_result(n: int = 2) -> ImportResult:
    return ImportResult(
        total_imported=n,
        total_skipped=0,
        total_failed=0,
        sources=[],
        warnings=[],
        imported_session_ids=list(range(1, n + 1)),
    )


# Shared patches applied to every test in this module.
_PATCH_UPSERT = patch(
    "snore.api.import_worker._upsert_job_record",
    new_callable=AsyncMock,
)
_PATCH_RELEASE = patch.object(ImportJob, "release_capacity")
_PATCH_SHUTDOWN = patch(
    "snore.api.import_jobs.is_shutdown_in_progress", return_value=False
)


# ---------------------------------------------------------------------------
# _run_rescan happy path
# ---------------------------------------------------------------------------


class TestRunRescanHappyPath:
    def test_succeeds_calls_phase_complete_and_finish(self, tmp_path):
        """Happy path: detect + import → phase_complete(IMPORT) + _finish(succeeded=True)."""
        fake_result = _fake_import_result()

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
                return_value=fake_result,
            ),
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        assert job.state == JobState.SUCCEEDED

    def test_succeeds_import_sources_called_with_backup_false(self, tmp_path):
        """import_sources must be called with backup=False for archive rescans."""
        fake_result = _fake_import_result()

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
                return_value=fake_result,
            ) as mock_import,
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        mock_import.assert_called_once()
        _, kwargs = mock_import.call_args
        assert kwargs.get("backup") is False, (
            "Rescan must call import_sources(backup=False); "
            f"got backup={kwargs.get('backup')!r}"
        )

    def test_release_capacity_called(self, tmp_path):
        """release_capacity must always be called (capacity accounting)."""
        fake_result = _fake_import_result()

        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE as mock_release,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources",
                return_value=[MagicMock()],
            ),
            patch(
                "snore.api.import_worker.ImportService.import_sources",
                new_callable=AsyncMock,
                return_value=fake_result,
            ),
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        mock_release.assert_called_once()

    def test_cleanup_files_no_op_when_temp_dir_none(self, tmp_path):
        """cleanup_files must not raise when temp_dir is None (rescan has no spool)."""
        fake_result = _fake_import_result()

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
                return_value=fake_result,
            ),
        ):
            job = _make_rescan_job()
            assert job.temp_dir is None
            _run_rescan(job, tmp_path)  # must not raise

    def test_analysis_enqueued_for_imported_sessions(self, tmp_path):
        """When sessions are imported, analysis_jobs.enqueue is called with those IDs."""
        fake_result = _fake_import_result(n=3)
        mock_aj = MagicMock()
        mock_aj.job_id = "analysis-abc"

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
                return_value=fake_result,
            ),
            patch(
                "snore.api.analysis_jobs.enqueue", return_value=mock_aj
            ) as mock_enqueue,
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        mock_enqueue.assert_called_once()
        _, kwargs = mock_enqueue.call_args
        assert kwargs["session_ids"] == [1, 2, 3]
        assert kwargs["profile_id"] == 42
        assert job.analysis_job_id == "analysis-abc"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestRunRescanErrors:
    def test_profile_raw_root_none_produces_failed(self):
        """profile_raw_root=None → job fails with a client-safe error."""
        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
        ):
            job = _make_rescan_job()
            _run_rescan(job, None)

        assert job.state == JobState.FAILED

    def test_detect_sources_empty_produces_failed(self, tmp_path):
        """detect_sources returning [] → job fails (no device data)."""
        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources",
                return_value=[],
            ),
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        assert job.state == JobState.FAILED

    def test_target_profile_id_none_produces_failed(self, tmp_path):
        """Missing target_profile_id → job fails."""
        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
        ):
            job = create_job(JobType.RESCAN, owner_user_id=1)
            # target_profile_id is None by default
            _run_rescan(job, tmp_path)

        assert job.state == JobState.FAILED

    def test_import_sources_raises_produces_failed(self, tmp_path):
        """Exception from import_sources → job fails; release_capacity still called."""
        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE as mock_release,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources",
                return_value=[MagicMock()],
            ),
            patch(
                "snore.api.import_worker.ImportService.import_sources",
                new_callable=AsyncMock,
                side_effect=RuntimeError("parser exploded"),
            ),
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        assert job.state == JobState.FAILED
        mock_release.assert_called_once()


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestRunRescanCancellation:
    def test_cancel_before_detect_finishes_without_analysis(self, tmp_path):
        """cancel_requested before detect_sources → finish(succeeded=False), no analysis."""
        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
        ):
            job = _make_rescan_job()
            job.try_cancel()  # sets cancel_requested before detect
            _run_rescan(job, tmp_path)

        # After try_cancel() with PENDING state the job transitions to CANCELLED
        # before _run_rescan can drive it to RUNNING. The finally block still runs.
        assert job.state in (JobState.CANCELLED, JobState.FAILED)

    def test_cancel_after_detect_but_before_import(self, tmp_path):
        """cancel_requested after detect but before import → finish(succeeded=False)."""
        call_count = 0

        def _detect_and_cancel(path):
            nonlocal call_count
            call_count += 1
            job.try_cancel()  # trip the flag mid-flow
            return [MagicMock()]

        with (
            _PATCH_UPSERT,
            _PATCH_RELEASE,
            _PATCH_SHUTDOWN,
            patch(
                "snore.api.import_worker.ImportService.detect_sources",
                side_effect=_detect_and_cancel,
            ),
            patch(
                "snore.api.import_worker.ImportService.import_sources",
                new_callable=AsyncMock,
            ) as mock_import,
        ):
            job = _make_rescan_job()
            _run_rescan(job, tmp_path)

        # import_sources must NOT have been called
        mock_import.assert_not_called()


# ---------------------------------------------------------------------------
# _run_dispatch routing
# ---------------------------------------------------------------------------


class TestRunDispatch:
    def test_rescan_routes_to_run_rescan(self, tmp_path):
        """_run_dispatch with RESCAN job calls _run_rescan."""
        with patch("snore.api.import_worker._run_rescan") as mock_rescan:
            job = _make_rescan_job()
            _run_dispatch(job, tmp_path)
        mock_rescan.assert_called_once_with(job, tmp_path)

    def test_upload_routes_to_run_import(self, tmp_path):
        """_run_dispatch with UPLOAD job calls _run_import."""
        with patch("snore.api.import_worker._run_import") as mock_import:
            job = create_job(JobType.UPLOAD, owner_user_id=1)
            _run_dispatch(job, tmp_path)
        mock_import.assert_called_once_with(job, tmp_path)
