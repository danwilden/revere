"""HMM training job runner.

Orchestrates the full Phase 2 pipeline:
  1. Compute features (or reuse existing feature_run)
  2. Train GaussianHMM
  3. Auto-label semantic regimes
  4. Persist model record + artifact + regime labels
  5. Update job status throughout

Designed to run in a background thread (FastAPI BackgroundTasks) locally,
or in a Fargate task in the cloud.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from backend.data.repositories import ArtifactRepository, MarketDataRepository, MetadataRepository
from backend.features.compute import run_feature_pipeline
from backend.jobs.status import JobManager
from backend.models.hmm_regime import train_hmm
from backend.models.labeling import apply_label_map, auto_label_states
from backend.schemas.enums import JobStatus, JobType, Timeframe


def run_hmm_training_job(
    job_id: str,
    instrument: str,
    timeframe: Timeframe,
    train_start: datetime,
    train_end: datetime,
    num_states: int,
    feature_set_name: str,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
    feature_run_id: str | None = None,
) -> str:
    """Run HMM training pipeline and return the new model_id.

    Args:
        job_id: pre-created job run ID (caller creates via job_manager.create())
        instrument: instrument symbol (e.g. 'EUR_USD')
        timeframe: Timeframe enum value
        train_start / train_end: training window
        num_states: number of HMM states (default 7)
        feature_set_name: feature set identifier
        feature_run_id: if provided, reuse this feature run; otherwise compute fresh
        market_repo, metadata_repo, artifact_repo, job_manager: dependencies

    Returns:
        model_id — ID of the created ModelRecord.
    """
    model_id = str(uuid.uuid4())

    try:
        job_manager.start(job_id)

        # ------------------------------------------------------------------
        # Step 1: Feature computation
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 10.0, "Computing features")

        instrument_id = instrument  # instrument symbol is used as instrument_id

        if feature_run_id is None:
            feature_run_id = run_feature_pipeline(
                instrument_id=instrument_id,
                timeframe=timeframe,
                start=train_start,
                end=train_end,
                market_repo=market_repo,
                metadata_repo=metadata_repo,
                feature_set_name=feature_set_name,
            )

        # ------------------------------------------------------------------
        # Step 2: Create model record (pending)
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 30.0, "Training HMM model")

        model_record = {
            "id": model_id,
            "model_type": "hmm",
            "instrument_id": instrument_id,
            "timeframe": timeframe.value,
            "training_start": train_start.isoformat(),
            "training_end": train_end.isoformat(),
            "parameters_json": json.dumps({
                "num_states": num_states,
                "feature_set_name": feature_set_name,
                "feature_run_id": feature_run_id,
            }),
            "artifact_ref": None,
            "label_map_json": {},
            "created_at": datetime.utcnow().isoformat(),
            "status": JobStatus.RUNNING.value,
        }
        metadata_repo.save_model(model_record)

        # ------------------------------------------------------------------
        # Step 3: Train HMM
        # ------------------------------------------------------------------
        result = train_hmm(
            instrument_id=instrument_id,
            timeframe=timeframe,
            train_start=train_start,
            train_end=train_end,
            num_states=num_states,
            feature_run_id=feature_run_id,
            model_id=model_id,
            market_repo=market_repo,
            metadata_repo=metadata_repo,
            artifact_repo=artifact_repo,
        )

        # ------------------------------------------------------------------
        # Step 4: Semantic labeling
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 80.0, "Applying semantic labels")

        state_stats = result["state_stats"]
        label_map = auto_label_states(state_stats)
        apply_label_map(label_map, model_id, metadata_repo)

        # ------------------------------------------------------------------
        # Step 5: Finalize model record
        # ------------------------------------------------------------------
        metadata_repo.update_model(model_id, {
            "artifact_ref": result["artifact_ref"],
            "label_map_json": label_map,
            "status": JobStatus.SUCCEEDED.value,
            "log_likelihood": result.get("log_likelihood"),
            "state_stats_json": json.dumps(state_stats),
        })

        job_manager.succeed(job_id, result_ref=model_id)
        return model_id

    except Exception as exc:
        job_manager.fail(
            job_id,
            error_code="HMM_TRAINING_FAILED",
            error_message=str(exc),
        )
        # Also mark model as failed if it was created
        try:
            if metadata_repo.get_model(model_id):
                metadata_repo.update_model(model_id, {"status": JobStatus.FAILED.value})
        except Exception:
            pass
        raise
