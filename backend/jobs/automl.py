"""AutoML job runner.

Orchestrates the full AutoML training pipeline:
  1. Build a feature dataset.
  2. Launch a SageMaker Autopilot job.
  3. Poll until the job completes.
  4. Persist candidates and update job status.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from backend.data.repositories import ArtifactRepository, MetadataRepository
from backend.jobs.status import JobManager

if TYPE_CHECKING:
    from backend.schemas.requests import AutoMLJobRequest


def run_automl_job(
    job_id: str,
    request: "AutoMLJobRequest",
    dataset_builder: Any,
    sagemaker_runner: Any,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
    poll_interval: int = 30,
) -> None:
    """Execute an AutoML job end-to-end.

    Uses job_id as the AutoMLJobRecord primary key so that GET /jobs/{job_id}
    can retrieve the record with a single metadata_repo lookup.
    """
    try:
        # ----------------------------------------------------------------
        # Stage 1: Start job
        # ----------------------------------------------------------------
        job_manager.start(job_id)

        # ----------------------------------------------------------------
        # Stage 2: Build dataset
        # ----------------------------------------------------------------
        manifest = dataset_builder.build(
            instrument_id=request.instrument_id,
            timeframe=request.timeframe,
            feature_run_id=request.feature_run_id,
            model_id=request.model_id,
            train_end_date=request.train_end_date,
            test_end_date=request.test_end_date,
            target_type=request.target_type,
            target_horizon_bars=request.target_horizon_bars,
            job_id=job_id,
        )

        # ----------------------------------------------------------------
        # Stage 3: Persist dataset manifest on the AutoMLJobRecord
        # ----------------------------------------------------------------
        record = metadata_repo.get_job_run(job_id)  # use job_id as record id
        automl_record = _load_automl_record(metadata_repo, job_id)
        automl_record["dataset_manifest"] = manifest.model_dump(mode="json")
        _save_automl_record(metadata_repo, automl_record)
        job_manager.progress(job_id, 25, "dataset_built")

        # ----------------------------------------------------------------
        # Stage 4: Construct S3 URIs and launch Autopilot
        # ----------------------------------------------------------------
        s3_bucket = _resolve_s3_bucket()
        train_s3_uri = f"{s3_bucket}/automl/{job_id}/train/"
        output_s3_prefix = f"{s3_bucket}/automl/{job_id}/output/"

        sagemaker_job_name = sagemaker_runner.launch_automl_job(
            job_name=f"automl-{job_id[:8]}",
            target_column=manifest.target_column,
            target_type=request.target_type,
            train_s3_uri=train_s3_uri,
            output_s3_prefix=output_s3_prefix,
            max_runtime_seconds=request.max_runtime_seconds,
        )

        automl_record["sagemaker_job_name"] = sagemaker_job_name
        _save_automl_record(metadata_repo, automl_record)

        # ----------------------------------------------------------------
        # Stage 5: Poll until done
        # ----------------------------------------------------------------
        while True:
            result = sagemaker_runner.poll_job(sagemaker_job_name)
            if result["status"] in ("completed", "failed", "stopped"):
                break
            time.sleep(poll_interval)

        job_manager.progress(job_id, 75, "autopilot_done")

        # ----------------------------------------------------------------
        # Stage 6: Finalize
        # ----------------------------------------------------------------
        if result["status"] == "completed":
            automl_record["candidates"] = result.get("candidates", [])
            automl_record["status"] = "completed"
            _save_automl_record(metadata_repo, automl_record)
            job_manager.succeed(job_id)
        else:
            failure_reason = result.get("failure_reason", "sagemaker_failed")
            automl_record["status"] = "failed"
            _save_automl_record(metadata_repo, automl_record)
            job_manager.fail(job_id, failure_reason)

    except Exception as exc:
        try:
            automl_record = _load_automl_record(metadata_repo, job_id)
            automl_record["status"] = "failed"
            _save_automl_record(metadata_repo, automl_record)
        except Exception:
            pass
        job_manager.fail(job_id, str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_automl_record(metadata_repo: MetadataRepository, job_id: str) -> dict:
    """Load AutoMLJobRecord dict by job_id (stored with id=job_id)."""
    record = metadata_repo.get_job_run.__self__  # not used directly
    # LocalMetadataRepository uses _get("automl_jobs", key) keyed by record["id"]
    # We store records with id=job_id so this is a direct lookup.
    from backend.data.local_metadata import LocalMetadataRepository
    if hasattr(metadata_repo, "_get"):
        return metadata_repo._get("automl_jobs", job_id) or {}
    # Fallback for other implementations
    return {}


def _save_automl_record(metadata_repo: MetadataRepository, record: dict) -> None:
    """Persist AutoMLJobRecord dict."""
    if hasattr(metadata_repo, "_upsert"):
        metadata_repo._upsert("automl_jobs", record)
    else:
        raise NotImplementedError("metadata_repo does not support _upsert for automl_jobs")


def _resolve_s3_bucket() -> str:
    """Return an S3 base URI; falls back to local-dev placeholder."""
    from backend.config import settings
    bucket = settings.s3_bucket
    if bucket:
        return f"s3://{bucket}"
    return "s3://local-dev"
