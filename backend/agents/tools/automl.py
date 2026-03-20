"""AutoML tool executors — used by model_researcher_node.

Four synchronous executor functions that wrap SageMaker and metadata-repo
operations. They follow the same naming and signature conventions as
``backend/agents/tools/feature.py``.

All boto3 calls flow through an injected SageMakerRunner so nothing is
instantiated inside these functions (no hardcoded regions or credentials).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _get_runner(deps: Any):
    """Return a SageMakerRunner from deps, or raise clearly."""
    runner = getattr(deps, "sagemaker_runner", None)
    if runner is None:
        raise RuntimeError(
            "deps.sagemaker_runner is required but not set. "
            "Inject a SageMakerRunner instance before calling AutoML tools."
        )
    return runner


def _get_automl_repo(deps: Any):
    """Return the AutoML metadata store from deps."""
    repo = getattr(deps, "automl_metadata_repo", None)
    if repo is not None:
        return repo
    # Fall back to the shared metadata repo keyed under "automl_jobs"
    from backend.deps import get_metadata_repo
    return get_metadata_repo()


# ---------------------------------------------------------------------------
# Executor 1: launch_automl_job
# ---------------------------------------------------------------------------


def execute_launch_automl_job(params: dict[str, Any], deps: Any) -> dict[str, Any]:
    """Launch a SageMaker AutoML v2 job and persist a record.

    Parameters
    ----------
    params:
        ``job_id``          — client-supplied unique job ID used as SageMaker job name
        ``target_type``     — ``"direction"`` | ``"return_bucket"``
        ``train_s3_uri``    — S3 URI of training data
        ``output_s3_prefix``— S3 prefix for AutoML outputs

    Returns
    -------
    dict:
        ``job_name``, ``status``, ``message``
    """
    from backend.automl.records import AutoMLJobRecord
    from backend.automl.sagemaker_runner import SageMakerRunner

    job_id: str = params["job_id"]
    target_type: str = params["target_type"]
    train_s3_uri: str = params["train_s3_uri"]
    output_s3_prefix: str = params["output_s3_prefix"]
    target_column: str = params.get("target_column", "label")
    max_runtime_seconds: int = int(params.get("max_runtime_seconds", 3600))

    runner: SageMakerRunner = _get_runner(deps)

    try:
        job_name = runner.launch_automl_job(
            job_name=job_id,
            target_column=target_column,
            target_type=target_type,
            train_s3_uri=train_s3_uri,
            output_s3_prefix=output_s3_prefix,
            max_runtime_seconds=max_runtime_seconds,
        )
    except Exception as exc:
        logger.error("launch_automl_job failed: %s", exc)
        return {"job_name": job_id, "status": "failed", "message": str(exc)}

    # Persist record
    record = AutoMLJobRecord(
        id=job_id,
        job_name=job_name,
        target_column=target_column,
        target_type=target_type,
        train_s3_uri=train_s3_uri,
        output_s3_prefix=output_s3_prefix,
        max_runtime_seconds=max_runtime_seconds,
        status="running",
    )
    metadata_repo = _get_automl_repo(deps)
    metadata_repo.put("automl_jobs", job_id, record.model_dump(mode="json"))

    logger.info("AutoML job launched and persisted: job_name=%s", job_name)
    return {"job_name": job_name, "status": "running", "message": "Job launched"}


# ---------------------------------------------------------------------------
# Executor 2: execute_get_automl_job_status
# ---------------------------------------------------------------------------


def execute_get_automl_job_status(params: dict[str, Any], deps: Any) -> dict[str, Any]:
    """Poll a SageMaker AutoML job and return its normalised status.

    Parameters
    ----------
    params:
        ``automl_job_id`` — SageMaker job name to describe.

    Returns
    -------
    dict:
        ``job_name``, ``status``, ``accepted``, ``failure_reason``,
        ``best_candidate`` (dict or None)
    """
    from backend.automl.sagemaker_runner import SageMakerRunner

    automl_job_id: str = params["automl_job_id"]
    runner: SageMakerRunner = _get_runner(deps)

    try:
        poll_result = runner.poll_job(automl_job_id)
    except Exception as exc:
        logger.error("poll_job failed for %s: %s", automl_job_id, exc)
        return {
            "job_name": automl_job_id,
            "status": "failed",
            "accepted": False,
            "failure_reason": str(exc),
            "best_candidate": None,
        }

    # Update persisted record status
    try:
        metadata_repo = _get_automl_repo(deps)
        existing = metadata_repo.get("automl_jobs", automl_job_id)
        if existing is not None:
            existing["status"] = poll_result["status"]
            existing["updated_at"] = _utcnow_iso()
            if poll_result.get("failure_reason"):
                existing["failure_reason"] = poll_result["failure_reason"]
            metadata_repo.put("automl_jobs", automl_job_id, existing)
    except Exception as exc:
        logger.warning("Could not update persisted AutoML record: %s", exc)

    return {
        "job_name": automl_job_id,
        "status": poll_result["status"],
        "accepted": poll_result["accepted"],
        "failure_reason": poll_result.get("failure_reason"),
        "best_candidate": poll_result.get("best_candidate"),
    }


# ---------------------------------------------------------------------------
# Executor 3: execute_get_automl_candidates
# ---------------------------------------------------------------------------


def execute_get_automl_candidates(params: dict[str, Any], deps: Any) -> dict[str, Any]:
    """Retrieve all candidates for a completed AutoML job.

    Parameters
    ----------
    params:
        ``automl_job_id`` — SageMaker job name.

    Returns
    -------
    dict:
        ``job_name``, ``candidates`` (list), ``count``
    """
    from backend.automl.sagemaker_runner import SageMakerRunner

    automl_job_id: str = params["automl_job_id"]
    runner: SageMakerRunner = _get_runner(deps)

    try:
        candidates = runner.get_candidates(automl_job_id)
    except Exception as exc:
        logger.error("get_candidates failed for %s: %s", automl_job_id, exc)
        return {
            "job_name": automl_job_id,
            "candidates": [],
            "count": 0,
            "error": str(exc),
        }

    return {
        "job_name": automl_job_id,
        "candidates": candidates,
        "count": len(candidates),
    }


# ---------------------------------------------------------------------------
# Executor 4: execute_convert_to_signal
# ---------------------------------------------------------------------------


def execute_convert_to_signal(params: dict[str, Any], deps: Any) -> dict[str, Any]:
    """Convert the best AutoML candidate into a Medallion Signal record.

    Reads the persisted ``AutoMLJobRecord``, validates acceptance, then
    creates a Signal via the signal bank.

    Parameters
    ----------
    params:
        ``automl_job_id`` — SageMaker job name.

    Returns
    -------
    dict:
        ``signal_id``, ``signal_name``, ``automl_job_id``, ``auc_roc``
    """
    automl_job_id: str = params["automl_job_id"]
    metadata_repo = _get_automl_repo(deps)

    record_dict = metadata_repo.get("automl_jobs", automl_job_id)
    if record_dict is None:
        return {
            "signal_id": None,
            "error": f"AutoML job record not found: {automl_job_id}",
        }

    if not record_dict.get("evaluation", {}) or not record_dict.get("best_auc_roc"):
        return {
            "signal_id": None,
            "error": "AutoML job has not been evaluated yet — run model_researcher first",
        }

    evaluation = record_dict.get("evaluation", {})
    if not evaluation.get("accept", False):
        return {
            "signal_id": None,
            "error": "AutoML evaluation did not accept this candidate (AUC-ROC below threshold or LLM rejected)",
        }

    # Build a signal record using the metadata repo
    import uuid
    signal_id = str(uuid.uuid4())
    signal_name = f"automl_{automl_job_id}"
    signal_record = {
        "id": signal_id,
        "name": signal_name,
        "signal_type": "automl",
        "automl_job_id": automl_job_id,
        "best_candidate_id": record_dict.get("best_candidate_id"),
        "best_auc_roc": record_dict.get("best_auc_roc"),
        "created_at": _utcnow_iso(),
    }
    metadata_repo.put("automl_signals", signal_id, signal_record)

    logger.info(
        "Converted AutoML job %s to signal %s (AUC-ROC=%.4f)",
        automl_job_id,
        signal_id,
        record_dict.get("best_auc_roc", 0.0),
    )

    return {
        "signal_id": signal_id,
        "signal_name": signal_name,
        "automl_job_id": automl_job_id,
        "auc_roc": record_dict.get("best_auc_roc"),
    }
