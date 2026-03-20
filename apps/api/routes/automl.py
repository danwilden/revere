"""AutoML training job routes.

POST  /api/automl/jobs                     — submit an AutoML training job
GET   /api/automl/jobs/{job_id}            — poll combined job + record status
GET   /api/automl/jobs/{job_id}/candidates — list model candidates (post-completion)
POST  /api/automl/jobs/{job_id}/convert    — convert accepted job to a Signal
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from backend.deps import (
    get_artifact_repo,
    get_dataset_builder,
    get_job_manager,
    get_metadata_repo,
    get_sagemaker_runner,
)
from backend.jobs.automl import _load_automl_record, _save_automl_record, run_automl_job
from backend.schemas.enums import JobType
from backend.schemas.models import AutoMLJobRecord, JobRun, Signal
from backend.schemas.requests import AutoMLJobRequest, AutoMLJobStatusResponse
from backend.signals.automl_signal import create_signal_from_automl

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /jobs — submit a new AutoML job
# ---------------------------------------------------------------------------

@router.post("/jobs", status_code=202, response_model=JobRun)
async def create_automl_job(
    request: AutoMLJobRequest,
    background_tasks: BackgroundTasks,
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager=Depends(get_job_manager),
    dataset_builder=Depends(get_dataset_builder),
    sagemaker_runner=Depends(get_sagemaker_runner),
) -> JobRun:
    """Submit an AutoML training job.

    Returns immediately with the JobRun so the caller can poll for status.
    """
    job = job_manager.create(
        job_type=JobType.AUTOML_TRAIN,
        params=request.model_dump(),
    )

    # Create the AutoMLJobRecord using job.id as its primary key so that
    # GET /jobs/{job_id} can retrieve it with a single direct lookup.
    automl_record = AutoMLJobRecord(
        id=job.id,
        job_id=job.id,
        instrument_id=request.instrument_id,
        timeframe=request.timeframe,
        feature_run_id=request.feature_run_id,
        model_id=request.model_id,
        target_type=request.target_type,
        status="queued",
    )
    _save_automl_record(metadata_repo, automl_record.model_dump(mode="json"))

    background_tasks.add_task(
        run_automl_job,
        job.id,
        request,
        dataset_builder,
        sagemaker_runner,
        metadata_repo,
        artifact_repo,
        job_manager,
    )

    return job


# ---------------------------------------------------------------------------
# GET /jobs/{job_id} — poll combined status
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}", response_model=AutoMLJobStatusResponse)
async def get_automl_job(
    job_id: str,
    metadata_repo=Depends(get_metadata_repo),
    job_manager=Depends(get_job_manager),
) -> AutoMLJobStatusResponse:
    """Return the combined JobRun + AutoMLJobRecord for polling."""
    job_dict = job_manager.get(job_id)
    if job_dict is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    automl_dict = _load_automl_record(metadata_repo, job_id)
    if not automl_dict:
        raise HTTPException(status_code=404, detail=f"AutoML record for job '{job_id}' not found")

    return AutoMLJobStatusResponse(
        job_run=JobRun(**job_dict),
        automl_record=AutoMLJobRecord(**automl_dict),
    )


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/candidates
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/candidates", response_model=list[dict])
async def get_automl_candidates(
    job_id: str,
    metadata_repo=Depends(get_metadata_repo),
    job_manager=Depends(get_job_manager),
) -> list[dict]:
    """Return the list of AutoPilot model candidates.

    Returns 404 if the job does not exist.
    Returns 409 if the job has not completed yet.
    """
    job_dict = job_manager.get(job_id)
    if job_dict is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    automl_dict = _load_automl_record(metadata_repo, job_id)
    if not automl_dict:
        raise HTTPException(status_code=404, detail=f"AutoML record for job '{job_id}' not found")

    if automl_dict.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' has not completed (status={automl_dict.get('status')})",
        )

    return automl_dict.get("candidates", [])


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/convert
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/convert", status_code=202, response_model=Signal)
async def convert_to_signal(
    job_id: str,
    signal_name: str | None = None,
    metadata_repo=Depends(get_metadata_repo),
    job_manager=Depends(get_job_manager),
) -> Signal:
    """Convert an accepted AutoML job to a reusable Signal bank entry.

    Returns 409 if the job is not completed or the evaluation has not been accepted.
    """
    job_dict = job_manager.get(job_id)
    if job_dict is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    automl_dict = _load_automl_record(metadata_repo, job_id)
    if not automl_dict:
        raise HTTPException(status_code=404, detail=f"AutoML record for job '{job_id}' not found")

    if automl_dict.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' has not completed (status={automl_dict.get('status')})",
        )

    evaluation = automl_dict.get("evaluation")
    if evaluation is None or evaluation.get("accept") is not True:
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' has not been accepted for signal conversion",
        )

    automl_record = AutoMLJobRecord(**automl_dict)
    signal = create_signal_from_automl(automl_record, metadata_repo, signal_name)

    # Update the AutoMLJobRecord with the newly created signal_id.
    automl_dict["signal_id"] = signal.id
    _save_automl_record(metadata_repo, automl_dict)

    return signal
