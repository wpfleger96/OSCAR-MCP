"""Strict timestamp and state contracts for job response schemas."""

import json

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pydantic import BaseModel, ValidationError

from snore.api.app import create_app
from snore.api.schemas import (
    AnalysisJobStatus,
    PipelineJobStatus,
    ValidationRunStatus,
)

_TIMESTAMP_FIELDS = ("created_at", "started_at", "finished_at")
_ANALYSIS_STATES = {"queued", "running", "succeeded", "failed", "cancelled"}


PayloadFactory = Callable[[], dict[str, object]]


def _analysis_payload() -> dict[str, object]:
    return {
        "job_id": "analysis-job",
        "state": "queued",
        "source": "batch",
        "session_count": 1,
        "progress_completed": 0,
        "progress_total": 1,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "started_at": None,
        "finished_at": None,
        "owner_user_id": None,
    }


def _pipeline_payload() -> dict[str, object]:
    return {
        "job_id": "import-job",
        "job_type": "upload",
        "state": "pending",
        "stage": "uploading",
        "file_count": 1,
        "created_at": datetime.now(UTC),
        "started_at": None,
        "finished_at": None,
        "progress_message": None,
        "sessions_imported": None,
        "import_result": None,
        "error_message": None,
        "analysis_job_id": None,
        "analysis_queued": None,
        "linked_analysis": None,
    }


def _validation_payload() -> dict[str, object]:
    return {
        "run_id": 1,
        "job_id": "validation-job",
        "validator_type": "events",
        "date_from": "2025-01-01",
        "date_to": "2025-01-02",
        "state": "queued",
        "error_message": None,
        "engine_identity": {},
        "validator_params": {},
        "owner_user_id": None,
        "created_at": datetime.now(UTC),
        "started_at": None,
        "finished_at": None,
    }


_SCHEMA_CASES = (
    (AnalysisJobStatus, _analysis_payload),
    (PipelineJobStatus, _pipeline_payload),
    (ValidationRunStatus, _validation_payload),
)


@pytest.mark.parametrize(("model_type", "payload_factory"), _SCHEMA_CASES)
@pytest.mark.parametrize("field", _TIMESTAMP_FIELDS)
@pytest.mark.parametrize("epoch", [1_700_000_000, 1_700_000_000.0])
def test_job_response_timestamps_reject_numeric_epochs(
    model_type: type[BaseModel],
    payload_factory: PayloadFactory,
    field: str,
    epoch: int | float,
) -> None:
    payload = payload_factory()
    payload[field] = epoch

    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


@pytest.mark.parametrize(("model_type", "payload_factory"), _SCHEMA_CASES)
@pytest.mark.parametrize("field", _TIMESTAMP_FIELDS)
def test_job_response_timestamps_reject_naive_datetimes(
    model_type: type[BaseModel], payload_factory: PayloadFactory, field: str
) -> None:
    payload = payload_factory()
    payload[field] = datetime(2025, 1, 2, 3, 4, 5)

    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


@pytest.mark.parametrize(("model_type", "payload_factory"), _SCHEMA_CASES)
@pytest.mark.parametrize("field", _TIMESTAMP_FIELDS)
def test_job_response_timestamps_normalize_to_utc_and_serialize_as_z(
    model_type: type[BaseModel], payload_factory: PayloadFactory, field: str
) -> None:
    source = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=5)))
    payload = payload_factory()
    payload[field] = source

    model = model_type.model_validate(payload)
    timestamp = getattr(model, field)
    assert timestamp == source.astimezone(UTC)
    assert timestamp.utcoffset() == timedelta(0)
    assert json.loads(model.model_dump_json())[field].endswith("Z")


@pytest.mark.parametrize("state", sorted(_ANALYSIS_STATES))
def test_analysis_job_status_accepts_every_documented_state(state: str) -> None:
    payload = _analysis_payload()
    payload["state"] = state

    assert AnalysisJobStatus.model_validate(payload).state == state


def test_analysis_job_status_rejects_unknown_state() -> None:
    payload = _analysis_payload()
    payload["state"] = "unknown"

    with pytest.raises(ValidationError):
        AnalysisJobStatus.model_validate(payload)


def test_analysis_job_status_openapi_has_exact_state_enum() -> None:
    schema = create_app().openapi()["components"]["schemas"]["AnalysisJobStatus"]

    assert set(schema["properties"]["state"]["enum"]) == _ANALYSIS_STATES
