"""Tests for backend/automl/sagemaker_runner.py.

All boto3 SageMaker client calls are mocked — no live AWS.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.automl.sagemaker_runner import (
    AUC_ROC_ACCEPTANCE_THRESHOLD,
    SageMakerRunner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_runner() -> tuple[SageMakerRunner, MagicMock]:
    """Return (runner, mock_client) with a fresh MagicMock client."""
    client = MagicMock()
    runner = SageMakerRunner(sagemaker_client=client, region="us-east-1")
    return runner, client


def _poll_response(sm_status: str, auc_roc: float | None = None) -> dict:
    """Build a minimal DescribeAutoMLJobV2 response dict."""
    resp: dict = {"AutoMLJobStatus": sm_status}
    if auc_roc is not None:
        resp["BestCandidate"] = {
            "FinalAutoMLJobObjectiveMetric": {
                "MetricName": "validation:auc",
                "Value": auc_roc,
            }
        }
    return resp


# ---------------------------------------------------------------------------
# launch_automl_job
# ---------------------------------------------------------------------------


def test_launch_binary_classification():
    runner, client = _make_runner()
    client.create_auto_ml_job_v2.return_value = {}

    runner.launch_automl_job(
        job_name="test-job",
        target_column="direction_label",
        target_type="direction",
        train_s3_uri="s3://bucket/train.csv",
        output_s3_prefix="s3://bucket/output/",
    )

    call_kwargs = client.create_auto_ml_job_v2.call_args[1]
    problem_config = call_kwargs["AutoMLProblemTypeConfig"]
    assert problem_config["TabularJobConfig"]["ProblemType"] == "BinaryClassification"


def test_launch_multiclass_classification():
    runner, client = _make_runner()
    client.create_auto_ml_job_v2.return_value = {}

    runner.launch_automl_job(
        job_name="test-job-bucket",
        target_column="return_bucket",
        target_type="return_bucket",
        train_s3_uri="s3://bucket/train.csv",
        output_s3_prefix="s3://bucket/output/",
    )

    call_kwargs = client.create_auto_ml_job_v2.call_args[1]
    problem_config = call_kwargs["AutoMLProblemTypeConfig"]
    assert problem_config["TabularJobConfig"]["ProblemType"] == "MulticlassClassification"


def test_launch_returns_job_name():
    runner, client = _make_runner()
    client.create_auto_ml_job_v2.return_value = {}

    result = runner.launch_automl_job(
        job_name="my-automl-job",
        target_column="label",
        target_type="direction",
        train_s3_uri="s3://bucket/train.csv",
        output_s3_prefix="s3://bucket/out/",
    )

    assert result == "my-automl-job"


def test_launch_unsupported_target_type_raises():
    runner, client = _make_runner()
    with pytest.raises(ValueError, match="Unsupported target_type"):
        runner.launch_automl_job(
            job_name="job",
            target_column="label",
            target_type="invalid_type",
            train_s3_uri="s3://x",
            output_s3_prefix="s3://y",
        )


# ---------------------------------------------------------------------------
# poll_job — status normalisation
# ---------------------------------------------------------------------------


def test_poll_running_status():
    runner, client = _make_runner()
    client.describe_auto_ml_job_v2.return_value = _poll_response("InProgress")

    result = runner.poll_job("test-job")
    assert result["status"] == "running"


def test_poll_completed_status():
    runner, client = _make_runner()
    client.describe_auto_ml_job_v2.return_value = _poll_response("Completed", auc_roc=0.72)

    result = runner.poll_job("test-job")
    assert result["status"] == "completed"


def test_poll_failed_status():
    runner, client = _make_runner()
    resp = _poll_response("Failed")
    resp["FailureReason"] = "Training timeout"
    client.describe_auto_ml_job_v2.return_value = resp

    result = runner.poll_job("test-job")
    assert result["status"] == "failed"
    assert result["failure_reason"] == "Training timeout"


# ---------------------------------------------------------------------------
# poll_job — AUC-ROC acceptance gate
# ---------------------------------------------------------------------------


def test_poll_accepted_above_threshold():
    runner, client = _make_runner()
    client.describe_auto_ml_job_v2.return_value = _poll_response(
        "Completed", auc_roc=AUC_ROC_ACCEPTANCE_THRESHOLD + 0.05
    )

    result = runner.poll_job("test-job")
    assert result["accepted"] is True


def test_poll_rejected_below_threshold():
    runner, client = _make_runner()
    client.describe_auto_ml_job_v2.return_value = _poll_response(
        "Completed", auc_roc=AUC_ROC_ACCEPTANCE_THRESHOLD - 0.05
    )

    result = runner.poll_job("test-job")
    assert result["accepted"] is False


def test_poll_no_auc_metric_not_accepted():
    """No BestCandidate in response → accepted=False."""
    runner, client = _make_runner()
    client.describe_auto_ml_job_v2.return_value = {"AutoMLJobStatus": "Completed"}

    result = runner.poll_job("test-job")
    assert result["accepted"] is False
    assert result["best_candidate"] is None


# ---------------------------------------------------------------------------
# get_candidates
# ---------------------------------------------------------------------------


def test_get_candidates_returns_list():
    runner, client = _make_runner()
    fake_candidates = [
        {"CandidateName": "candidate-1", "CandidateStatus": "Completed"},
        {"CandidateName": "candidate-2", "CandidateStatus": "Completed"},
    ]
    client.list_candidates_for_auto_ml_job.return_value = {"Candidates": fake_candidates}

    result = runner.get_candidates("test-job")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["CandidateName"] == "candidate-1"
