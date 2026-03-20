"""HMM model API routes.

POST /api/models/hmm/jobs          — create HMM training job
GET  /api/models/hmm/jobs/{jobId}  — poll job status
GET  /api/models/hmm               — list models
GET  /api/models/hmm/{modelId}     — get model metadata
POST /api/models/hmm/{modelId}/label — apply/update semantic label map
"""
from __future__ import annotations

import json
import threading
from copy import copy
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from backend.deps import get_artifact_repo, get_job_manager, get_market_repo, get_metadata_repo
from backend.jobs.hmm import run_hmm_training_job
from backend.jobs.status import JobManager
from backend.models.labeling import apply_label_map
from backend.schemas.enums import JobType, Timeframe
from backend.schemas.requests import (
    HMMTrainingRequest,
    JobCreatedResponse,
    JobResponse,
    LabelMapUpdateRequest,
    ModelListResponse,
    ModelRecordResponse,
)

router = APIRouter()


def _normalize_model_record(record: dict) -> dict:
    """Ensure parameters_json is a dict for ModelRecordResponse validation.

    Metadata may store it as a JSON string (legacy) or as a dict.
    """
    out = copy(record)
    raw = out.get("parameters_json")
    if isinstance(raw, str):
        try:
            out["parameters_json"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            out["parameters_json"] = {}
    elif not isinstance(raw, dict):
        out["parameters_json"] = {}
    return out


@router.post("/hmm/jobs", response_model=JobCreatedResponse, status_code=202)
def create_hmm_training_job(
    req: HMMTrainingRequest,
    background_tasks: BackgroundTasks,
    market_repo=Depends(get_market_repo),
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager: JobManager = Depends(get_job_manager),
):
    job = job_manager.create(
        job_type=JobType.HMM_TRAINING,
        params={
            "instrument": req.instrument,
            "timeframe": req.timeframe.value,
            "train_start": req.train_start.isoformat(),
            "train_end": req.train_end.isoformat(),
            "num_states": req.num_states,
            "feature_set_name": req.feature_set_name,
        },
    )

    def _run():
        run_hmm_training_job(
            job_id=job.id,
            instrument=req.instrument,
            timeframe=req.timeframe,
            train_start=req.train_start,
            train_end=req.train_end,
            num_states=req.num_states,
            feature_set_name=req.feature_set_name,
            market_repo=market_repo,
            metadata_repo=metadata_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
        )

    background_tasks.add_task(_run)
    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/hmm/jobs/{job_id}", response_model=JobResponse)
def get_hmm_job(
    job_id: str,
    job_manager: JobManager = Depends(get_job_manager),
):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/hmm", response_model=ModelListResponse)
def list_hmm_models(
    metadata_repo=Depends(get_metadata_repo),
):
    models = metadata_repo.list_models(model_type="hmm")
    normalized = [_normalize_model_record(m) for m in models]
    return {"models": normalized, "count": len(normalized)}


@router.get("/hmm/{model_id}", response_model=ModelRecordResponse)
def get_hmm_model(
    model_id: str,
    metadata_repo=Depends(get_metadata_repo),
):
    record = metadata_repo.get_model(model_id)
    if not record:
        raise HTTPException(status_code=404, detail="Model not found")
    return _normalize_model_record(record)


@router.post("/hmm/{model_id}/label")
def update_label_map(
    model_id: str,
    body: LabelMapUpdateRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    """Overwrite the semantic label map for a model.

    Body: {"label_map": {"0": "TREND_BULL_LOW_VOL", "1": "RANGE_MEAN_REVERT", ...}}
    """
    record = metadata_repo.get_model(model_id)
    if not record:
        raise HTTPException(status_code=404, detail="Model not found")

    # Validate keys are string int state IDs
    try:
        {int(k): v for k, v in body.label_map.items()}
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Label map keys must be string integers")

    apply_label_map(body.label_map, model_id, metadata_repo)
    return {"model_id": model_id, "label_map": body.label_map}
