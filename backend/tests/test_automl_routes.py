"""Route-level tests for AutoML API endpoints.

All SageMaker, S3, and DatasetBuilder calls are mocked.
Tests use FastAPI TestClient with dependency overrides.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from backend.data.local_metadata import LocalMetadataRepository
from backend.deps import (
    get_artifact_repo,
    get_dataset_builder,
    get_job_manager,
    get_metadata_repo,
    get_sagemaker_runner,
)
from backend.jobs.automl import _save_automl_record
from backend.jobs.status import JobManager
from backend.schemas.enums import JobType, SignalType
from backend.schemas.models import AutoMLJobRecord, DatasetManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_metadata(tmp_path):
    return LocalMetadataRepository(tmp_path / "metadata")


@pytest.fixture()
def tmp_artifact_repo(tmp_path):
    from backend.data.local_artifacts import LocalArtifactRepository
    return LocalArtifactRepository(tmp_path / "artifacts")


@pytest.fixture()
def job_manager(tmp_metadata):
    return JobManager(tmp_metadata)


@pytest.fixture()
def mock_dataset_builder():
    builder = MagicMock()
    manifest = DatasetManifest(
        job_id="placeholder",
        instrument_id="EUR_USD",
        timeframe="H1",
        feature_run_id="feat-001",
        model_id="model-001",
        target_column="direction",
        target_type="direction",
    )
    builder.build.return_value = manifest
    return builder


@pytest.fixture()
def mock_sagemaker_runner():
    runner = MagicMock()
    runner.launch_automl_job.return_value = "automl-test-sm-job"
    runner.poll_job.return_value = {"status": "completed", "candidates": []}
    return runner


@pytest.fixture()
def client(tmp_metadata, tmp_artifact_repo, job_manager, mock_dataset_builder, mock_sagemaker_runner):
    app.dependency_overrides[get_metadata_repo] = lambda: tmp_metadata
    app.dependency_overrides[get_artifact_repo] = lambda: tmp_artifact_repo
    app.dependency_overrides[get_job_manager] = lambda: job_manager
    app.dependency_overrides[get_dataset_builder] = lambda: mock_dataset_builder
    app.dependency_overrides[get_sagemaker_runner] = lambda: mock_sagemaker_runner
    yield TestClient(app)
    app.dependency_overrides.clear()


_VALID_REQUEST = {
    "instrument_id": "EUR_USD",
    "timeframe": "H1",
    "feature_run_id": "feat-001",
    "model_id": "model-001",
    "train_end_date": "2023-06-30",
    "test_end_date": "2023-12-31",
    "target_type": "direction",
    "target_horizon_bars": 1,
    "max_runtime_seconds": 3600,
}


# ---------------------------------------------------------------------------
# Test 1: POST /api/automl/jobs returns 202 with job_id
# ---------------------------------------------------------------------------

def test_create_automl_job_returns_202_with_job_id(client):
    resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    assert resp.status_code == 202
    body = resp.json()
    assert "id" in body
    assert body["job_type"] == "automl_train"
    assert body["status"] == "queued"


# ---------------------------------------------------------------------------
# Test 2: POST creates AutoMLJobRecord in metadata repo
# ---------------------------------------------------------------------------

def test_create_automl_job_creates_record(client, tmp_metadata):
    resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    # TestClient runs BackgroundTasks synchronously, so the record may have
    # advanced past "queued" by the time we inspect it — we only verify the
    # structural fields that are set at creation time.
    record = tmp_metadata._get("automl_jobs", job_id)
    assert record is not None
    assert record["instrument_id"] == "EUR_USD"
    assert record["timeframe"] == "H1"
    assert record["job_id"] == job_id


# ---------------------------------------------------------------------------
# Test 3: GET /api/automl/jobs/{id} returns combined AutoMLJobStatusResponse
# ---------------------------------------------------------------------------

def test_get_automl_job_returns_combined_response(client, tmp_metadata, job_manager):
    post_resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    job_id = post_resp.json()["id"]

    get_resp = client.get(f"/api/automl/jobs/{job_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert "job_run" in body
    assert "automl_record" in body
    assert body["job_run"]["id"] == job_id
    assert body["automl_record"]["job_id"] == job_id


# ---------------------------------------------------------------------------
# Test 4: GET /api/automl/jobs/{id} returns 404 for unknown job_id
# ---------------------------------------------------------------------------

def test_get_automl_job_404_unknown(client):
    resp = client.get("/api/automl/jobs/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 5: GET /api/automl/jobs/{id}/candidates returns 409 when not completed
# ---------------------------------------------------------------------------

def test_get_candidates_409_when_not_completed(client, tmp_metadata, job_manager):
    # Create a job and its automl record directly (bypass background task) so
    # we can control the status without TestClient running the task.
    from backend.jobs.automl import _save_automl_record
    from backend.schemas.enums import JobType
    from backend.schemas.models import AutoMLJobRecord

    job = job_manager.create(job_type=JobType.AUTOML_TRAIN, params={})
    record = AutoMLJobRecord(
        id=job.id,
        job_id=job.id,
        instrument_id="EUR_USD",
        timeframe="H1",
        feature_run_id="feat-001",
        model_id="model-001",
        status="queued",  # explicitly not completed
    )
    _save_automl_record(tmp_metadata, record.model_dump(mode="json"))

    resp = client.get(f"/api/automl/jobs/{job.id}/candidates")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Test 6: GET /api/automl/jobs/{id}/candidates returns list when completed
# ---------------------------------------------------------------------------

def test_get_candidates_returns_list_when_completed(client, tmp_metadata):
    post_resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    job_id = post_resp.json()["id"]

    # Manually mark record as completed with candidates
    record = tmp_metadata._get("automl_jobs", job_id)
    record["status"] = "completed"
    record["candidates"] = [{"model_name": "xgboost", "auc": 0.87}]
    tmp_metadata._upsert("automl_jobs", record)

    resp = client.get(f"/api/automl/jobs/{job_id}/candidates")
    assert resp.status_code == 200
    candidates = resp.json()
    assert isinstance(candidates, list)
    assert len(candidates) == 1
    assert candidates[0]["model_name"] == "xgboost"


# ---------------------------------------------------------------------------
# Test 7: POST /convert returns 409 when job not completed
# ---------------------------------------------------------------------------

def test_convert_409_when_not_completed(client, tmp_metadata):
    post_resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    job_id = post_resp.json()["id"]

    resp = client.post(f"/api/automl/jobs/{job_id}/convert")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Test 8: POST /convert returns 409 when evaluation.accept is False
# ---------------------------------------------------------------------------

def test_convert_409_when_evaluation_not_accepted(client, tmp_metadata):
    post_resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    job_id = post_resp.json()["id"]

    record = tmp_metadata._get("automl_jobs", job_id)
    record["status"] = "completed"
    record["evaluation"] = {"accept": False, "auc_roc": 0.72}
    tmp_metadata._upsert("automl_jobs", record)

    resp = client.post(f"/api/automl/jobs/{job_id}/convert")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Test 9: POST /convert returns Signal when accepted
# ---------------------------------------------------------------------------

def test_convert_returns_signal_when_accepted(client, tmp_metadata):
    post_resp = client.post("/api/automl/jobs", json=_VALID_REQUEST)
    job_id = post_resp.json()["id"]

    record = tmp_metadata._get("automl_jobs", job_id)
    record["status"] = "completed"
    record["evaluation"] = {"accept": True, "auc_roc": 0.88}
    record["best_candidate_id"] = "cand-001"
    record["best_auc_roc"] = 0.88
    record["best_model_artifact_key"] = "artifacts/automl/model.tar.gz"
    tmp_metadata._upsert("automl_jobs", record)

    resp = client.post(f"/api/automl/jobs/{job_id}/convert")
    assert resp.status_code == 202
    body = resp.json()
    assert "id" in body
    assert body["signal_type"] == SignalType.AUTOML_DIRECTION_PROB.value
    assert body["definition_json"]["automl_job_id"] == job_id
    assert body["definition_json"]["auc_roc"] == 0.88

    # Signal should be saved in metadata
    signal_id = body["id"]
    saved_signal = tmp_metadata.get_signal(signal_id)
    assert saved_signal is not None

    # AutoMLJobRecord should be updated with signal_id
    updated_record = tmp_metadata._get("automl_jobs", job_id)
    assert updated_record["signal_id"] == signal_id


# ---------------------------------------------------------------------------
# Test 10: All routes have typed response_model annotations (OpenAPI schema)
# ---------------------------------------------------------------------------

def test_all_routes_have_response_model_annotations(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})

    automl_paths = [p for p in paths if p.startswith("/api/automl")]
    assert len(automl_paths) >= 4, f"Expected at least 4 automl paths, got: {automl_paths}"

    # Verify each route has a 200/202 response schema (not just a plain dict)
    for path in automl_paths:
        path_item = paths[path]
        for method, operation in path_item.items():
            if method in ("get", "post"):
                responses = operation.get("responses", {})
                status_codes = list(responses.keys())
                # Each operation should have at least one success status code
                success_codes = [c for c in status_codes if c.startswith("2")]
                assert success_codes, f"Route {method.upper()} {path} has no 2xx response"
                # The success response should have a content schema
                for code in success_codes:
                    response_obj = responses[code]
                    content = response_obj.get("content", {})
                    if content:
                        schema_ref = content.get("application/json", {}).get("schema")
                        assert schema_ref is not None, (
                            f"Route {method.upper()} {path} ({code}) missing schema"
                        )
