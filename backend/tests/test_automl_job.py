"""Unit tests for the AutoML job runner (backend/jobs/automl.py).

All SageMaker and DatasetBuilder calls are mocked.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from backend.data.local_metadata import LocalMetadataRepository
from backend.jobs.automl import _load_automl_record, _save_automl_record, run_automl_job
from backend.jobs.status import JobManager
from backend.schemas.enums import JobType
from backend.schemas.models import AutoMLJobRecord, DatasetManifest
from backend.schemas.requests import AutoMLJobRequest


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
def automl_request():
    return AutoMLJobRequest(
        instrument_id="EUR_USD",
        timeframe="H1",
        feature_run_id="feat-001",
        model_id="model-001",
        train_end_date="2023-06-30",
        test_end_date="2023-12-31",
        target_type="direction",
        target_horizon_bars=1,
        max_runtime_seconds=600,
    )


@pytest.fixture()
def mock_manifest():
    return DatasetManifest(
        job_id="placeholder",
        instrument_id="EUR_USD",
        timeframe="H1",
        feature_run_id="feat-001",
        model_id="model-001",
        target_column="direction",
        target_type="direction",
        row_count=1000,
    )


def _make_dataset_builder(manifest):
    builder = MagicMock()
    builder.build.return_value = manifest
    return builder


def _make_sagemaker_runner(status="completed", candidates=None):
    runner = MagicMock()
    runner.launch_automl_job.return_value = "sm-job-abc123"
    runner.poll_job.return_value = {
        "status": status,
        "candidates": candidates or [],
    }
    return runner


def _seed_automl_record(metadata_repo, job_id: str, instrument_id="EUR_USD"):
    """Create an AutoMLJobRecord in the store keyed by job_id."""
    record = AutoMLJobRecord(
        id=job_id,
        job_id=job_id,
        instrument_id=instrument_id,
        timeframe="H1",
        feature_run_id="feat-001",
        model_id="model-001",
        target_type="direction",
        status="queued",
    )
    _save_automl_record(metadata_repo, record.model_dump(mode="json"))
    return record


# ---------------------------------------------------------------------------
# Test 11: run_automl_job calls dataset_builder.build() first
# ---------------------------------------------------------------------------

def test_run_automl_job_calls_dataset_builder_build(
    tmp_metadata, tmp_artifact_repo, job_manager, automl_request, mock_manifest
):
    job = job_manager.create(job_type=JobType.AUTOML_TRAIN, params=automl_request.model_dump())
    _seed_automl_record(tmp_metadata, job.id)

    dataset_builder = _make_dataset_builder(mock_manifest)
    sagemaker_runner = _make_sagemaker_runner()

    run_automl_job(
        job_id=job.id,
        request=automl_request,
        dataset_builder=dataset_builder,
        sagemaker_runner=sagemaker_runner,
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=job_manager,
        poll_interval=0,
    )

    dataset_builder.build.assert_called_once()
    call_kwargs = dataset_builder.build.call_args.kwargs
    assert call_kwargs["instrument_id"] == "EUR_USD"
    assert call_kwargs["feature_run_id"] == "feat-001"
    assert call_kwargs["job_id"] == job.id


# ---------------------------------------------------------------------------
# Test 12: run_automl_job calls sagemaker_runner.launch_automl_job() after dataset built
# ---------------------------------------------------------------------------

def test_run_automl_job_calls_launch_after_dataset(
    tmp_metadata, tmp_artifact_repo, job_manager, automl_request, mock_manifest
):
    job = job_manager.create(job_type=JobType.AUTOML_TRAIN, params=automl_request.model_dump())
    _seed_automl_record(tmp_metadata, job.id)

    dataset_builder = _make_dataset_builder(mock_manifest)
    sagemaker_runner = _make_sagemaker_runner()

    run_automl_job(
        job_id=job.id,
        request=automl_request,
        dataset_builder=dataset_builder,
        sagemaker_runner=sagemaker_runner,
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=job_manager,
        poll_interval=0,
    )

    sagemaker_runner.launch_automl_job.assert_called_once()
    call_kwargs = sagemaker_runner.launch_automl_job.call_args.kwargs
    assert call_kwargs["target_column"] == "direction"
    assert call_kwargs["target_type"] == "direction"
    assert "automl-" in call_kwargs["job_name"]


# ---------------------------------------------------------------------------
# Test 13: Polling loop exits on status="completed"
# ---------------------------------------------------------------------------

def test_poll_loop_exits_on_completed(
    tmp_metadata, tmp_artifact_repo, job_manager, automl_request, mock_manifest
):
    job = job_manager.create(job_type=JobType.AUTOML_TRAIN, params=automl_request.model_dump())
    _seed_automl_record(tmp_metadata, job.id)

    # poll returns "running" twice then "completed"
    sagemaker_runner = MagicMock()
    sagemaker_runner.launch_automl_job.return_value = "sm-job-abc"
    sagemaker_runner.poll_job.side_effect = [
        {"status": "running"},
        {"status": "running"},
        {"status": "completed", "candidates": []},
    ]

    run_automl_job(
        job_id=job.id,
        request=automl_request,
        dataset_builder=_make_dataset_builder(mock_manifest),
        sagemaker_runner=sagemaker_runner,
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=job_manager,
        poll_interval=0,
    )

    assert sagemaker_runner.poll_job.call_count == 3
    job_run = job_manager.get(job.id)
    assert job_run["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Test 14: Polling loop exits on status="failed" and calls job_manager.fail()
# ---------------------------------------------------------------------------

def test_poll_loop_exits_on_failed(
    tmp_metadata, tmp_artifact_repo, job_manager, automl_request, mock_manifest
):
    job = job_manager.create(job_type=JobType.AUTOML_TRAIN, params=automl_request.model_dump())
    _seed_automl_record(tmp_metadata, job.id)

    sagemaker_runner = _make_sagemaker_runner(status="failed")
    sagemaker_runner.poll_job.return_value = {
        "status": "failed",
        "failure_reason": "ResourceLimit exceeded",
    }

    run_automl_job(
        job_id=job.id,
        request=automl_request,
        dataset_builder=_make_dataset_builder(mock_manifest),
        sagemaker_runner=sagemaker_runner,
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=job_manager,
        poll_interval=0,
    )

    job_run = job_manager.get(job.id)
    assert job_run["status"] == "failed"
    assert "ResourceLimit exceeded" in job_run["error_message"]


# ---------------------------------------------------------------------------
# Test 15: job_manager.progress(job_id, 25, ...) called after dataset built
# ---------------------------------------------------------------------------

def test_progress_25_called_after_dataset_built(
    tmp_metadata, tmp_artifact_repo, automl_request, mock_manifest
):
    mock_jm = MagicMock(spec=JobManager)
    _seed_automl_record(tmp_metadata, "test-job-id")

    run_automl_job(
        job_id="test-job-id",
        request=automl_request,
        dataset_builder=_make_dataset_builder(mock_manifest),
        sagemaker_runner=_make_sagemaker_runner(),
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=mock_jm,
        poll_interval=0,
    )

    # First progress call must be (job_id, 25, "dataset_built")
    progress_calls = [c for c in mock_jm.progress.call_args_list]
    assert any(
        c.args == ("test-job-id", 25, "dataset_built") or
        (c.args[0] == "test-job-id" and c.args[1] == 25)
        for c in progress_calls
    ), f"progress(25) not called. Calls: {progress_calls}"


# ---------------------------------------------------------------------------
# Test 16: job_manager.succeed(job_id) called on completion
# ---------------------------------------------------------------------------

def test_succeed_called_on_completion(
    tmp_metadata, tmp_artifact_repo, automl_request, mock_manifest
):
    mock_jm = MagicMock(spec=JobManager)
    _seed_automl_record(tmp_metadata, "test-job-id")

    run_automl_job(
        job_id="test-job-id",
        request=automl_request,
        dataset_builder=_make_dataset_builder(mock_manifest),
        sagemaker_runner=_make_sagemaker_runner(status="completed"),
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=mock_jm,
        poll_interval=0,
    )

    mock_jm.succeed.assert_called_once_with("test-job-id")


# ---------------------------------------------------------------------------
# Test 17: Exception during dataset build → job_manager.fail() called
# ---------------------------------------------------------------------------

def test_exception_during_dataset_build_calls_fail(
    tmp_metadata, tmp_artifact_repo, job_manager, automl_request
):
    job = job_manager.create(job_type=JobType.AUTOML_TRAIN, params=automl_request.model_dump())
    _seed_automl_record(tmp_metadata, job.id)

    failing_builder = MagicMock()
    failing_builder.build.side_effect = RuntimeError("Dataset build exploded")

    run_automl_job(
        job_id=job.id,
        request=automl_request,
        dataset_builder=failing_builder,
        sagemaker_runner=_make_sagemaker_runner(),
        metadata_repo=tmp_metadata,
        artifact_repo=tmp_artifact_repo,
        job_manager=job_manager,
        poll_interval=0,
    )

    job_run = job_manager.get(job.id)
    assert job_run["status"] == "failed"
    assert "Dataset build exploded" in job_run["error_message"]
