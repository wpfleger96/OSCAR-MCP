"""Unit tests for the stitched pipeline-jobs read model.

Covers:
- _derive_stage: full matrix of import/analysis state combinations
- ImportJob.set_analysis_link: three-way semantics (id set, queue-full, no attempt)
- ImportJob properties: error_message, latest_progress_message, sessions_imported,
  finished_at, import_result_snapshot
- list_jobs: ownership filtering (None-owner, own, foreign)
- _to_import_result_summary: strips imported_session_ids at both top and per-source level
- set_analysis_link + _finish ordering: stored fields and terminal SSE payload are
  independent — both carry the analysis link info
"""

from __future__ import annotations

import pytest

import snore.api.import_jobs as job_store

from snore.api.import_jobs import (
    ImportJob,
    JobPhase,
    JobState,
    JobType,
    create_job,
    list_jobs,
)
from snore.api.routers.import_data import _derive_stage, _to_import_result_summary
from snore.api.schemas import LinkedAnalysisSummary

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_job_store():
    """Reset the job store before and after each test."""
    job_store._jobs.clear()
    job_store._per_user_count.clear()
    job_store._global_count = 0
    yield
    job_store._jobs.clear()
    job_store._per_user_count.clear()
    job_store._global_count = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(owner_user_id: int | None = None) -> ImportJob:
    return create_job(JobType.PATH, owner_user_id=owner_user_id, sources=[])


def _fake_import_result() -> dict:
    return {
        "total_imported": 3,
        "total_skipped": 1,
        "total_failed": 0,
        "warnings": ["w1"],
        "imported_session_ids": [10, 11, 12],
        "sources": [
            {
                "source": {
                    "parser_name": "resmed",
                    "root_path": "/mnt",
                    "device_serial": "SN1",
                    "profile_name": None,
                    "structure_type": None,
                    "data_root": None,
                },
                "imported": 3,
                "skipped": 1,
                "failed": 0,
                "warnings": ["w1"],
                "imported_session_ids": [10, 11, 12],
            }
        ],
    }


def _linked(state: str) -> LinkedAnalysisSummary:
    """Minimal LinkedAnalysisSummary for stage-derivation tests."""
    return LinkedAnalysisSummary(
        job_id="test",
        state=state,
        progress_completed=0,
        progress_total=0,
        error_message=None,
    )


# ---------------------------------------------------------------------------
# _derive_stage — full matrix
# ---------------------------------------------------------------------------


class TestDeriveStage:
    def test_pending_upload(self):
        assert _derive_stage(JobState.PENDING_UPLOAD, None, None, None) == "uploading"

    def test_pending(self):
        assert _derive_stage(JobState.PENDING, None, None, None) == "queued"

    def test_running(self):
        assert _derive_stage(JobState.RUNNING, None, None, None) == "importing"

    def test_failed(self):
        assert _derive_stage(JobState.FAILED, None, None, None) == "failed"

    def test_cancelled(self):
        assert _derive_stage(JobState.CANCELLED, None, None, None) == "cancelled"

    def test_succeeded_no_import_no_analysis(self):
        # Nothing was imported; analysis_queued is None → "done"
        assert _derive_stage(JobState.SUCCEEDED, None, None, None) == "done"

    def test_succeeded_queue_full(self):
        # Sessions were imported but the queue was full → analysis_skipped.
        assert (
            _derive_stage(JobState.SUCCEEDED, None, False, None) == "analysis_skipped"
        )

    def test_succeeded_analysis_reaped(self):
        # analysis_job_id set but the job was reaped (linked=None) → done.
        assert _derive_stage(JobState.SUCCEEDED, "abc", True, None) == "done"

    def test_succeeded_analysis_queued(self):
        assert (
            _derive_stage(JobState.SUCCEEDED, "abc", True, _linked("queued"))
            == "analysis_queued"
        )

    def test_succeeded_analysis_running(self):
        assert (
            _derive_stage(JobState.SUCCEEDED, "abc", True, _linked("running"))
            == "analyzing"
        )

    def test_succeeded_analysis_succeeded(self):
        assert (
            _derive_stage(JobState.SUCCEEDED, "abc", True, _linked("succeeded"))
            == "done"
        )

    def test_succeeded_analysis_failed(self):
        assert (
            _derive_stage(JobState.SUCCEEDED, "abc", True, _linked("failed"))
            == "analysis_failed"
        )

    def test_succeeded_analysis_cancelled(self):
        assert (
            _derive_stage(JobState.SUCCEEDED, "abc", True, _linked("cancelled"))
            == "analysis_cancelled"
        )

    def test_succeeded_unknown_linked_state(self):
        assert (
            _derive_stage(JobState.SUCCEEDED, "abc", True, _linked("bogus"))
            == "unknown"
        )


# ---------------------------------------------------------------------------
# set_analysis_link — three-way semantics
# ---------------------------------------------------------------------------


class TestSetAnalysisLink:
    def test_id_set_marks_queued_true(self):
        job = _make_job()
        job.set_analysis_link(analysis_job_id="abc123", queue_full=False)
        assert job.analysis_job_id == "abc123"
        assert job.analysis_queued is True

    def test_queue_full_marks_queued_false(self):
        job = _make_job()
        job.set_analysis_link(analysis_job_id=None, queue_full=True)
        assert job.analysis_job_id is None
        assert job.analysis_queued is False

    def test_no_sessions_leaves_queued_none(self):
        # analysis_job_id=None and queue_full=False → nothing to record.
        job = _make_job()
        job.set_analysis_link(analysis_job_id=None, queue_full=False)
        assert job.analysis_job_id is None
        assert job.analysis_queued is None


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_error_message_only_on_failed(self):
        job = _make_job()
        job.try_start()
        job._finish(
            succeeded=False,
            terminal_msg={"event": "error", "data": {"message": "boom"}},
        )
        state = job.state
        assert state == JobState.FAILED
        assert job.error_message == "boom"

    def test_error_message_only_on_cancelled(self):
        job = _make_job()
        job.try_start()
        job._finish_cancelled()
        state = job.state
        assert state == JobState.CANCELLED
        assert job.error_message == "Cancelled"

    def test_error_message_none_on_succeeded(self):
        job = _make_job()
        job.try_start()
        job._finish(succeeded=True, terminal_msg={"event": "complete", "data": {}})
        assert job.error_message is None

    def test_error_message_none_before_terminal(self):
        job = _make_job()
        job.try_start()
        assert job.error_message is None

    def test_latest_progress_message_extraction(self):
        job = _make_job()
        job.try_start()
        assert job.latest_progress_message is None
        job.report_progress("Detecting sources...")
        assert job.latest_progress_message == "Detecting sources..."

    def test_latest_progress_message_updates(self):
        job = _make_job()
        job.try_start()
        job.report_progress("step 1")
        job.report_progress("step 2")
        assert job.latest_progress_message == "step 2"

    def test_sessions_imported_gated_on_commit(self):
        job = _make_job()
        job.try_start()
        # Not yet committed.
        assert job.sessions_imported is None
        result = _fake_import_result()
        job.phase_complete(JobPhase.IMPORT, result)
        assert job.sessions_imported == 3

    def test_sessions_imported_none_without_result(self):
        job = _make_job()
        job.try_start()
        assert job.sessions_imported is None

    def test_finished_at_set_on_terminal(self):
        job = _make_job()
        job.try_start()
        assert job.finished_at is None
        job._finish(succeeded=True, terminal_msg={"event": "complete", "data": {}})
        assert job.finished_at is not None

    def test_finished_at_none_while_running(self):
        job = _make_job()
        job.try_start()
        assert job.finished_at is None

    def test_import_result_snapshot_is_copy(self):
        job = _make_job()
        job.try_start()
        result = _fake_import_result()
        job.phase_complete(JobPhase.IMPORT, result)
        snapshot = job.import_result_snapshot
        assert snapshot is not None
        assert snapshot == result
        # Mutating snapshot must not affect the stored result.
        snapshot["total_imported"] = 9999
        assert job.import_result_snapshot is not None
        assert job.import_result_snapshot["total_imported"] == 3

    def test_import_result_snapshot_none_before_commit(self):
        job = _make_job()
        assert job.import_result_snapshot is None


# ---------------------------------------------------------------------------
# list_jobs — ownership filtering
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_none_owner_job_visible_to_any_user(self):
        job = _make_job(owner_user_id=None)
        visible = list_jobs(owner_user_id=42)
        assert job in visible

    def test_own_job_visible_to_owner(self):
        job = _make_job(owner_user_id=7)
        assert job in list_jobs(owner_user_id=7)

    def test_foreign_job_not_visible(self):
        job = _make_job(owner_user_id=7)
        visible = list_jobs(owner_user_id=99)
        assert job not in visible

    def test_none_parameter_returns_all(self):
        job_a = _make_job(owner_user_id=1)
        job_b = _make_job(owner_user_id=2)
        all_jobs = list_jobs(owner_user_id=None)
        assert job_a in all_jobs
        assert job_b in all_jobs

    def test_empty_store_returns_empty(self):
        assert list_jobs(owner_user_id=1) == []


# ---------------------------------------------------------------------------
# _to_import_result_summary — strips imported_session_ids
# ---------------------------------------------------------------------------


class TestToImportResultSummary:
    def test_strips_imported_session_ids_at_top_level(self):
        result = _fake_import_result()
        summary = _to_import_result_summary(result)
        # The Pydantic model has no imported_session_ids field, so it must not appear.
        data = summary.model_dump()
        assert "imported_session_ids" not in data

    def test_strips_imported_session_ids_per_source(self):
        result = _fake_import_result()
        summary = _to_import_result_summary(result)
        assert len(summary.sources) == 1
        per_source = summary.sources[0].model_dump()
        assert "imported_session_ids" not in per_source

    def test_totals_preserved(self):
        result = _fake_import_result()
        summary = _to_import_result_summary(result)
        assert summary.total_imported == 3
        assert summary.total_skipped == 1
        assert summary.total_failed == 0
        assert summary.warnings == ["w1"]

    def test_per_source_fields_preserved(self):
        result = _fake_import_result()
        summary = _to_import_result_summary(result)
        src = summary.sources[0]
        assert src.imported == 3
        assert src.skipped == 1
        assert src.failed == 0
        assert src.warnings == ["w1"]
        assert src.source.parser_name == "resmed"

    def test_empty_sources(self):
        result = {
            "total_imported": 0,
            "total_skipped": 0,
            "total_failed": 0,
            "warnings": [],
            "sources": [],
        }
        summary = _to_import_result_summary(result)
        assert summary.total_imported == 0
        assert summary.sources == []


# ---------------------------------------------------------------------------
# set_analysis_link before _finish: both stored fields and terminal SSE are set
# ---------------------------------------------------------------------------


class TestSetAnalysisLinkOrdering:
    def test_stored_fields_and_terminal_msg_are_independent(self):
        """_run_import calls set_analysis_link BEFORE _finish.  The stored
        properties must reflect the link, and the terminal SSE payload also
        carries analysis_job_id independently (via terminal_extra).
        """
        job = _make_job()
        job.try_start()

        result = _fake_import_result()
        job.phase_complete(JobPhase.IMPORT, result)

        # Simulate worker calling set_analysis_link before _finish.
        job.set_analysis_link(analysis_job_id="deadbeef", queue_full=False)

        # Worker then builds terminal_extra independently and calls _finish.
        terminal_msg = {
            "event": "complete",
            "data": {
                "result": result,
                "import_committed": True,
                "import_result": result,
                "analysis_job_id": "deadbeef",
            },
        }
        job._finish(succeeded=True, terminal_msg=terminal_msg)

        # Stored properties reflect the link.
        assert job.analysis_job_id == "deadbeef"
        assert job.analysis_queued is True

        # Late observer receives the terminal that also carries analysis_job_id.

        ch = job.attach_observer()
        msg = ch.get(timeout=0.1)
        assert msg is not None
        assert msg["event"] == "complete"
        assert msg["data"].get("analysis_job_id") == "deadbeef"
        assert msg["data"].get("import_committed") is True
