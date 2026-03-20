"""Experiments API routes — Phase 5B/5F experiment container CRUD.

GET   /api/experiments                              — list all experiments (newest first)
GET   /api/experiments/{id}                         — get single experiment with iterations
POST  /api/experiments                              — create new experiment (no graph trigger)
PATCH /api/experiments/{id}/status                  — transition experiment status
POST  /api/experiments/{id}/promote                 — launch robustness battery job (202)
GET   /api/experiments/{id}/robustness              — poll latest battery result (200)
POST  /api/experiments/{id}/approve                 — mark validated after passed battery (200)
POST  /api/experiments/{id}/discard                 — mark discarded with reason (200)
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.deps import get_artifact_repo, get_job_manager, get_market_repo, get_metadata_repo
from backend.schemas.enums import ExperimentStatus, JobType
from backend.schemas.requests import (
    DiscardExperimentRequest,
    ExperimentCreateRequest,
    ExperimentDetailResponse,
    ExperimentListResponse,
    ExperimentRecord,
    ExperimentResponse,
    ExperimentStatusUpdateRequest,
    JobCreatedResponse,
    RobustnessResult,
    RobustnessStatusResponse,
)

router = APIRouter()

# Store name used for API-level experiment records in LocalMetadataRepository
_STORE = "api_experiments"

# Valid status transitions: from_status -> set of permitted to_statuses
# VALIDATED and DISCARDED are set via dedicated endpoints (/approve, /discard),
# not via the generic PATCH /status endpoint, so they are terminal here.
_PERMITTED_TRANSITIONS: dict[str, set[str]] = {
    ExperimentStatus.ACTIVE.value:    {ExperimentStatus.PAUSED.value, ExperimentStatus.COMPLETED.value, ExperimentStatus.ARCHIVED.value},
    ExperimentStatus.PAUSED.value:    {ExperimentStatus.ACTIVE.value, ExperimentStatus.ARCHIVED.value},
    ExperimentStatus.COMPLETED.value: {ExperimentStatus.ARCHIVED.value},
    ExperimentStatus.ARCHIVED.value:  set(),
    ExperimentStatus.VALIDATED.value: set(),
    ExperimentStatus.DISCARDED.value: set(),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _get_record(metadata_repo, experiment_id: str) -> dict:
    """Return raw dict or raise 404."""
    raw = metadata_repo._get(_STORE, experiment_id)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not found",
        )
    return raw


def _dict_to_record(raw: dict) -> ExperimentRecord:
    return ExperimentRecord.model_validate(raw)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=ExperimentListResponse)
async def list_experiments(
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    metadata_repo=Depends(get_metadata_repo),
) -> ExperimentListResponse:
    """List all experiments, newest first, with optional status filter."""
    # Validate status query param if provided
    if status is not None:
        valid_values = {s.value for s in ExperimentStatus}
        if status not in valid_values:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {sorted(valid_values)}",
            )

    records = metadata_repo._list(_STORE)
    if status is not None:
        records = [r for r in records if r.get("status") == status]
    records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    records = records[:limit]

    return ExperimentListResponse(
        experiments=[_dict_to_record(r) for r in records],
        count=len(records),
    )


@router.get("/{experiment_id}", response_model=ExperimentDetailResponse)
async def get_experiment(
    experiment_id: str,
    metadata_repo=Depends(get_metadata_repo),
) -> ExperimentDetailResponse:
    """Return a single experiment by ID with its iteration history."""
    raw = _get_record(metadata_repo, experiment_id)
    record = _dict_to_record(raw)

    # Iterations are stored separately under "experiment_iterations"
    all_iterations = metadata_repo._list("experiment_iterations")
    iterations = [
        i for i in all_iterations
        if i.get("experiment_id") == experiment_id
    ]
    iterations.sort(key=lambda i: i.get("generation", 0))

    from backend.schemas.requests import ExperimentIteration
    typed_iterations = [ExperimentIteration.model_validate(i) for i in iterations]

    return ExperimentDetailResponse(experiment=record, iterations=typed_iterations)


@router.post("", response_model=ExperimentResponse, status_code=201)
async def create_experiment(
    body: ExperimentCreateRequest,
    metadata_repo=Depends(get_metadata_repo),
) -> ExperimentResponse:
    """Create a new experiment container record.

    Does not start a research run — use POST /api/research/run for that.
    """
    now = _now_iso()
    record_dict: dict = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "description": body.description,
        "instrument": body.instrument,
        "timeframe": body.timeframe,
        "test_start": body.test_start.isoformat(),
        "test_end": body.test_end.isoformat(),
        "model_id": body.model_id,
        "feature_run_id": body.feature_run_id,
        "status": ExperimentStatus.ACTIVE.value,
        "created_at": now,
        "updated_at": now,
        "requested_by": body.requested_by,
        "generation_count": 0,
        "best_strategy_id": None,
        "best_backtest_run_id": None,
        "tags": body.tags,
    }
    metadata_repo._upsert(_STORE, record_dict)
    return ExperimentResponse(experiment=_dict_to_record(record_dict))


@router.patch("/{experiment_id}/status", response_model=ExperimentResponse)
async def update_experiment_status(
    experiment_id: str,
    body: ExperimentStatusUpdateRequest,
    metadata_repo=Depends(get_metadata_repo),
) -> ExperimentResponse:
    """Transition an experiment's status.

    Permitted transitions:
      active    -> paused, completed, archived
      paused    -> active, archived
      completed -> archived
      archived  -> (terminal, no transitions allowed)

    Returns 409 for disallowed transitions.
    """
    raw = _get_record(metadata_repo, experiment_id)
    current_status = raw.get("status", ExperimentStatus.ACTIVE.value)
    new_status = body.status.value

    permitted = _PERMITTED_TRANSITIONS.get(current_status, set())
    if new_status not in permitted:
        if current_status == ExperimentStatus.ARCHIVED.value:
            raise HTTPException(
                status_code=409,
                detail="Experiment is archived and cannot be transitioned",
            )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transition from '{current_status}' to '{new_status}' is not permitted. "
                f"Allowed: {sorted(permitted) if permitted else 'none (terminal state)'}"
            ),
        )

    metadata_repo._update(_STORE, experiment_id, {
        "status": new_status,
        "updated_at": _now_iso(),
    })
    updated = metadata_repo._get(_STORE, experiment_id)
    return ExperimentResponse(experiment=_dict_to_record(updated))


# ---------------------------------------------------------------------------
# Phase 5F — robustness battery background runner
# ---------------------------------------------------------------------------

def _run_battery_bg(
    job_id: str,
    experiment_id: str,
    metadata_repo,
    market_repo,
    artifact_repo,
    job_manager,
) -> None:
    """Target for the battery background thread.

    Uses a lazy import so the server stays startable while
    backend/jobs/robustness_battery.py is not yet implemented (Wave 3).
    """
    try:
        from backend.jobs.robustness_battery import run_robustness_battery_job
        run_robustness_battery_job(
            job_id=job_id,
            experiment_id=experiment_id,
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
        )
    except ImportError:
        job_manager.fail(
            job_id=job_id,
            error_message="robustness_battery module not yet implemented",
            error_code="NOT_IMPLEMENTED",
        )
    except Exception:
        # run_robustness_battery_job is responsible for calling job_manager.fail()
        pass


# ---------------------------------------------------------------------------
# Phase 5F — four new routes
# ---------------------------------------------------------------------------

@router.post("/{experiment_id}/promote", response_model=JobCreatedResponse, status_code=202)
async def promote_experiment(
    experiment_id: str,
    metadata_repo=Depends(get_metadata_repo),
    market_repo=Depends(get_market_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager=Depends(get_job_manager),
) -> JobCreatedResponse:
    """Launch a robustness battery job for the experiment.

    Requires best_backtest_run_id to be set on the experiment.
    Returns 409 if the experiment is in a terminal state or a battery is already running.
    """
    raw = _get_record(metadata_repo, experiment_id)

    if raw.get("best_backtest_run_id") is None:
        raise HTTPException(
            status_code=409,
            detail="Experiment has no best_backtest_run_id — run a backtest first",
        )

    current_status = raw.get("status", ExperimentStatus.ACTIVE.value)
    if current_status in (ExperimentStatus.ARCHIVED.value, ExperimentStatus.DISCARDED.value):
        raise HTTPException(
            status_code=409,
            detail=f"Experiment is '{current_status}' and cannot be promoted",
        )

    # Guard against duplicate battery runs
    existing_jobs = job_manager.list(job_type="robustness_battery", limit=100)
    running = [
        j for j in existing_jobs
        if j.params.get("experiment_id") == experiment_id
        and j.status.value in ("queued", "running")
    ]
    if running:
        raise HTTPException(
            status_code=409,
            detail="A robustness battery is already running for this experiment",
        )

    job = job_manager.create(
        job_type=JobType.ROBUSTNESS_BATTERY,
        params={"experiment_id": experiment_id},
    )

    metadata_repo._update(_STORE, experiment_id, {
        "robustness_job_id": job.id,
        "updated_at": _now_iso(),
    })

    thread = threading.Thread(
        target=_run_battery_bg,
        args=(job.id, experiment_id, metadata_repo, market_repo, artifact_repo, job_manager),
        daemon=True,
        name=f"battery-{job.id[:8]}",
    )
    thread.start()

    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/{experiment_id}/robustness", response_model=RobustnessStatusResponse)
async def get_robustness_status(
    experiment_id: str,
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager=Depends(get_job_manager),
) -> RobustnessStatusResponse:
    """Return the latest robustness battery status for an experiment.

    Always returns HTTP 200; use the job_status field to determine progress.
    Returns an empty response (no job_id) if no battery has been launched yet.
    """
    _get_record(metadata_repo, experiment_id)  # 404 if not found

    all_jobs = job_manager.list(job_type="robustness_battery", limit=100)
    battery_jobs = [
        j for j in all_jobs
        if j.params.get("experiment_id") == experiment_id
    ]
    battery_jobs.sort(key=lambda j: str(j.created_at), reverse=True)

    if not battery_jobs:
        return RobustnessStatusResponse(experiment_id=experiment_id)

    latest = battery_jobs[0]
    status_val = latest.status.value

    if status_val in ("queued", "running"):
        return RobustnessStatusResponse(
            experiment_id=experiment_id,
            job_id=latest.id,
            job_status=status_val,
            progress_pct=latest.progress_pct,
        )

    if status_val == "succeeded":
        artifact_key = f"robustness/{experiment_id}/battery_{latest.id}.json"
        raw_bytes = artifact_repo.load(artifact_key)
        result = RobustnessResult.model_validate(json.loads(raw_bytes))
        return RobustnessStatusResponse(
            experiment_id=experiment_id,
            job_id=latest.id,
            job_status=status_val,
            progress_pct=latest.progress_pct,
            result=result,
        )

    # FAILED or CANCELLED
    return RobustnessStatusResponse(
        experiment_id=experiment_id,
        job_id=latest.id,
        job_status=status_val,
        progress_pct=latest.progress_pct,
        error_message=latest.error_message,
    )


@router.post("/{experiment_id}/approve", response_model=ExperimentResponse)
async def approve_experiment(
    experiment_id: str,
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager=Depends(get_job_manager),
) -> ExperimentResponse:
    """Approve an experiment after a passed robustness battery.

    Transitions status to VALIDATED and sets tier='validated'.
    Returns 409 if the experiment is in a terminal state, has no succeeded
    battery, or the most recent battery result was not promoted.
    """
    raw = _get_record(metadata_repo, experiment_id)

    current_status = raw.get("status", ExperimentStatus.ACTIVE.value)
    if current_status in (ExperimentStatus.ARCHIVED.value, ExperimentStatus.DISCARDED.value):
        raise HTTPException(
            status_code=409,
            detail=f"Experiment is '{current_status}' and cannot be approved",
        )

    all_jobs = job_manager.list(job_type="robustness_battery", limit=100)
    succeeded_jobs = [
        j for j in all_jobs
        if j.params.get("experiment_id") == experiment_id
        and j.status.value == "succeeded"
    ]
    succeeded_jobs.sort(key=lambda j: str(j.created_at), reverse=True)

    if not succeeded_jobs:
        raise HTTPException(
            status_code=409,
            detail="No succeeded robustness battery found for this experiment",
        )

    latest_succeeded = succeeded_jobs[0]
    artifact_key = f"robustness/{experiment_id}/battery_{latest_succeeded.id}.json"
    raw_bytes = artifact_repo.load(artifact_key)
    result = RobustnessResult.model_validate(json.loads(raw_bytes))

    if not result.promoted:
        raise HTTPException(
            status_code=409,
            detail=(
                "Most recent robustness battery did not pass promotion gate. "
                f"Block reasons: {result.block_reasons}"
            ),
        )

    metadata_repo._update(_STORE, experiment_id, {
        "status": ExperimentStatus.VALIDATED.value,
        "tier": "validated",
        "updated_at": _now_iso(),
    })
    updated = metadata_repo._get(_STORE, experiment_id)
    return ExperimentResponse(experiment=_dict_to_record(updated))


@router.post("/{experiment_id}/discard", response_model=ExperimentResponse)
async def discard_experiment(
    experiment_id: str,
    body: DiscardExperimentRequest,
    metadata_repo=Depends(get_metadata_repo),
) -> ExperimentResponse:
    """Mark an experiment as discarded with an explicit reason.

    Returns 409 if the experiment is already in a terminal state.
    """
    raw = _get_record(metadata_repo, experiment_id)

    current_status = raw.get("status", ExperimentStatus.ACTIVE.value)
    if current_status in (ExperimentStatus.ARCHIVED.value, ExperimentStatus.DISCARDED.value):
        raise HTTPException(
            status_code=409,
            detail=f"Experiment is already '{current_status}'",
        )

    metadata_repo._update(_STORE, experiment_id, {
        "status": ExperimentStatus.DISCARDED.value,
        "discard_reason": body.reason,
        "updated_at": _now_iso(),
    })
    updated = metadata_repo._get(_STORE, experiment_id)
    return ExperimentResponse(experiment=_dict_to_record(updated))
