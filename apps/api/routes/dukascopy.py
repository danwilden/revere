"""POST /api/dukascopy/jobs  — start a Dukascopy download + ingest job
GET  /api/dukascopy/jobs/{job_id} — poll job status
GET  /api/dukascopy/jobs          — list recent Dukascopy jobs
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_job_manager, get_market_repo, get_metadata_repo
from backend.jobs.dukascopy_download import run_dukascopy_download_job
from backend.schemas.enums import JobType
from backend.schemas.requests import (
    BacktestJobRequest,
    DukascopyDownloadRequest,
    JobCreatedResponse,
    JobResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/jobs", response_model=JobCreatedResponse, status_code=202)
async def submit_dukascopy_download_job(
    body: DukascopyDownloadRequest,
    market_repo=Depends(get_market_repo),
    job_manager=Depends(get_job_manager),
):
    """Submit a new Dukascopy download + ingest job.

    Immediately returns the job_id so the caller can poll for status via
    GET /api/dukascopy/jobs/{job_id} or the generic GET /api/jobs/{job_id}.
    The download and ingestion run in a background thread.
    """
    job = job_manager.create(
        job_type=JobType.DUKASCOPY_DOWNLOAD,
        params={
            "instruments": body.instruments,
            "start_date": body.start_date.isoformat(),
            "end_date": body.end_date.isoformat(),
        },
    )
    logger.info("Dukascopy job created: job_id=%s", job.id)

    thread = threading.Thread(
        target=_run_job_bg,
        args=(
            job.id,
            body.instruments,
            body.start_date,
            body.end_date,
            market_repo,
            job_manager,
        ),
        daemon=True,
        name=f"dukascopy-{job.id[:8]}",
    )
    thread.start()

    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_dukascopy_job(
    job_id: str,
    job_manager=Depends(get_job_manager),
):
    """Poll the status of a Dukascopy download + ingest job."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.get("/jobs")
async def list_dukascopy_jobs(
    limit: int = 20,
    job_manager=Depends(get_job_manager),
):
    """List recent Dukascopy download + ingest jobs (newest first)."""
    jobs = job_manager.list(job_type=JobType.DUKASCOPY_DOWNLOAD.value, limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


@router.post("/jobs/{job_id}/pending-backtest", status_code=204)
async def register_pending_backtest(
    job_id: str,
    body: BacktestJobRequest,
    job_manager=Depends(get_job_manager),
    metadata_repo=Depends(get_metadata_repo),
):
    """Register a backtest to run automatically when this Dukascopy job succeeds.

    The job_id must refer to an existing Dukascopy download job (typically
    just created). When that job completes successfully, the backtest will be
    submitted automatically.
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if job.get("job_type") != JobType.DUKASCOPY_DOWNLOAD.value:
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_id}' is not a Dukascopy download job; cannot register pending backtest",
        )
    payload = body.model_dump(mode="json")
    metadata_repo.save_pending_backtest(job_id, payload)
    return None


# ---------------------------------------------------------------------------
# Background helper
# ---------------------------------------------------------------------------

def _run_job_bg(
    job_id: str,
    instruments: list[str],
    start_date,
    end_date,
    market_repo,
    job_manager,
) -> None:
    """Wrapper executed in the background thread.

    run_dukascopy_download_job handles its own exception → job_manager.fail()
    path, so there is nothing extra to do here on failure.
    """
    run_dukascopy_download_job(
        job_id=job_id,
        instruments=instruments,
        start_date=start_date,
        end_date=end_date,
        market_repo=market_repo,
        job_manager=job_manager,
    )
