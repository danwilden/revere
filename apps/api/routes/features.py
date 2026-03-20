"""Feature discovery API routes — Phase 5C.

POST /api/features/discover          — launch a feature discovery job (202)
GET  /api/features/discover/{job_id} — poll discovery job + results
GET  /api/features/library           — query accumulated feature library
GET  /api/features/library/{name}    — retrieve single feature by canonical name
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.deps import get_feature_library, get_job_manager, get_market_repo, get_metadata_repo
from backend.features.compute import FEATURE_CODE_VERSION, run_feature_pipeline
from backend.schemas.enums import JobStatus, JobType, Timeframe
from backend.schemas.requests import (
    FeatureDiscoverJobResponse,
    FeatureDiscoverRequest,
    FeatureEvalResult,
    FeatureLibraryResponse,
    FeatureSpec,
    JobCreatedResponse,
    ResolveFeatureRunRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()  # No prefix — applied in main.py registration


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/discover", response_model=JobCreatedResponse, status_code=202)
async def trigger_feature_discovery(
    body: FeatureDiscoverRequest,
    background_tasks: BackgroundTasks,
    job_manager=Depends(get_job_manager),
    feature_library=Depends(get_feature_library),
) -> JobCreatedResponse:
    """Launch a feature discovery job.

    Creates a JobManager job of type FEATURE_DISCOVERY, fires a background
    task that calls feature_researcher_node, and returns immediately with the
    job_id and queued status.
    """
    discovery_run_id = str(uuid.uuid4())
    job = job_manager.create(
        job_type=JobType.FEATURE_DISCOVERY,
        requested_by=body.requested_by,
        params={
            "instrument": body.instrument,
            "timeframe": body.timeframe,
            "eval_start": body.eval_start,
            "eval_end": body.eval_end,
            "feature_run_id": body.feature_run_id,
            "model_id": body.model_id,
            "families": body.families,
            "max_candidates": body.max_candidates,
            "discovery_run_id": discovery_run_id,
        },
    )
    background_tasks.add_task(
        _run_feature_discovery,
        job_id=job.id,
        discovery_run_id=discovery_run_id,
        body=body,
        job_manager=job_manager,
        feature_library=feature_library,
    )
    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/discover/{job_id}", response_model=FeatureDiscoverJobResponse)
async def get_feature_discovery_job(
    job_id: str,
    job_manager=Depends(get_job_manager),
    feature_library=Depends(get_feature_library),
) -> FeatureDiscoverJobResponse:
    """Return status and (when complete) evaluated feature results for a discovery job.

    Returns 404 if the job does not exist or is not a FEATURE_DISCOVERY job.
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.get("job_type") != JobType.FEATURE_DISCOVERY.value:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' is not a feature discovery job",
        )

    # Populate feature_eval_results only on SUCCEEDED
    feature_eval_results: list[FeatureEvalResult] = []
    params = job.get("params_json") or {}
    discovery_run_id: str | None = params.get("discovery_run_id")

    if job.get("status") == JobStatus.SUCCEEDED.value and discovery_run_id:
        feature_eval_results = feature_library.list_by_discovery_run(discovery_run_id)

    return FeatureDiscoverJobResponse(
        job_id=job["id"],
        status=JobStatus(job["status"]),
        progress_pct=job.get("progress_pct", 0.0),
        stage_label=job.get("stage_label", ""),
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error_code=job.get("error_code"),
        error_message=job.get("error_message"),
        discovery_run_id=discovery_run_id,
        feature_eval_results=feature_eval_results,
    )


@router.get("/library", response_model=FeatureLibraryResponse)
async def list_feature_library(
    family: str | None = Query(default=None),
    max_leakage: float | None = Query(default=None),
    min_f_statistic: float | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    feature_library=Depends(get_feature_library),
) -> FeatureLibraryResponse:
    """Query the accumulated feature library with optional filters.

    Results are ordered newest discovered_at first.
    """
    features: list[FeatureSpec] = feature_library.list_features(
        family=family,
        max_leakage=max_leakage,
        min_f_statistic=min_f_statistic,
        limit=limit,
    )
    return FeatureLibraryResponse(features=features, count=len(features))


@router.get("/library/{name}", response_model=FeatureSpec)
async def get_feature_by_name(
    name: str,
    feature_library=Depends(get_feature_library),
) -> FeatureSpec:
    """Retrieve a single FeatureSpec by its canonical name.

    Returns 404 if no feature with that name exists in the library.
    """
    try:
        return feature_library.get_by_name(name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Feature '{name}' not found in library",
        )


@router.post("/runs/resolve", status_code=200)
async def resolve_feature_run(
    body: ResolveFeatureRunRequest,
    metadata_repo=Depends(get_metadata_repo),
    market_repo=Depends(get_market_repo),
) -> dict:
    """Find an existing feature run covering the requested range, or create one.

    Looks for a feature run with matching instrument_id, timeframe, and a date
    range that fully covers [start_date, end_date]. Prefers runs at the current
    FEATURE_CODE_VERSION. If none found, runs the feature pipeline synchronously.

    Returns:
        {"feature_run_id": str, "created": bool, "code_version": str}
    """
    import json as _json

    try:
        start = datetime.fromisoformat(body.start_date)
        end = datetime.fromisoformat(body.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date format: {exc}")

    try:
        tf = Timeframe(body.timeframe)
    except ValueError:
        valid = [t.value for t in Timeframe]
        raise HTTPException(status_code=422, detail=f"Invalid timeframe. Must be one of: {valid}")

    # Search existing feature runs for a compatible match
    # TODO: cloud implementation needs list_feature_runs() on MetadataRepository
    all_runs: list[dict] = metadata_repo._list("feature_runs")
    best: dict | None = None

    for run in all_runs:
        try:
            params = run.get("parameters_json", "{}")
            if isinstance(params, str):
                params = _json.loads(params)
            if params.get("instrument_id") != body.instrument:
                continue
            if params.get("timeframe") != body.timeframe:
                continue
            run_start = datetime.fromisoformat(str(run.get("start_date", ""))[:19])
            run_end = datetime.fromisoformat(str(run.get("end_date", ""))[:19])
            if run_start > start or run_end < end:
                continue
            # Prefer current code version
            if best is None:
                best = run
            elif run.get("code_version") == FEATURE_CODE_VERSION and best.get("code_version") != FEATURE_CODE_VERSION:
                best = run
        except Exception:
            continue

    if best is not None:
        return {
            "feature_run_id": best["id"],
            "created": False,
            "code_version": best.get("code_version", "unknown"),
        }

    # No compatible run found — compute synchronously
    try:
        feature_run_id = run_feature_pipeline(
            instrument_id=body.instrument,
            timeframe=tf,
            start=start,
            end=end,
            market_repo=market_repo,
            metadata_repo=metadata_repo,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Feature pipeline failed (no bars available): {exc}",
        )

    return {
        "feature_run_id": feature_run_id,
        "created": True,
        "code_version": FEATURE_CODE_VERSION,
    }


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------


async def _run_feature_discovery(
    job_id: str,
    discovery_run_id: str,
    body: FeatureDiscoverRequest,
    job_manager,
    feature_library,
) -> None:
    """Invoke feature_researcher_node and persist results.

    feature_researcher_node is synchronous — dispatched via run_in_executor
    to avoid blocking the FastAPI event loop. Matches the research.py pattern
    exactly.
    """
    job_manager.start(job_id)
    try:
        from backend.agents.feature_researcher import feature_researcher_node

        initial_state = {
            "instrument": body.instrument,
            "timeframe": body.timeframe,
            "eval_start": body.eval_start,
            "eval_end": body.eval_end,
            "feature_run_id": body.feature_run_id,
            "model_id": body.model_id,
            "families": body.families,
            "max_candidates": body.max_candidates,
            "discovery_run_id": discovery_run_id,
            "feature_eval_results": None,
            "research_mode": "discover_features",
            "task": "discover_features",
        }
        loop = asyncio.get_event_loop()
        final_state = await loop.run_in_executor(
            None,
            lambda: feature_researcher_node(initial_state),
        )
        eval_results: list[dict] = final_state.get("feature_eval_results") or []
        for result in eval_results:
            feature_library.upsert(
                FeatureEvalResult.model_validate(result),
                instrument=body.instrument,
                timeframe=body.timeframe,
                eval_start=body.eval_start,
                eval_end=body.eval_end,
            )
        job_manager.succeed(job_id, result_ref=discovery_run_id)
    except Exception as exc:
        logger.exception("Feature discovery failed for job %s", job_id)
        job_manager.fail(job_id, str(exc), "FEATURE_DISCOVERY_ERROR")
