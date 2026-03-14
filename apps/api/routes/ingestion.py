"""POST /api/ingestion/jobs — submit ingestion job
GET  /api/ingestion/jobs/{jobId} — poll job status
GET  /api/ingestion/jobs — list recent ingestion jobs
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_job_manager, get_market_repo
from backend.jobs.ingestion import run_ingestion_job
from backend.schemas.enums import JobType
from backend.schemas.requests import IngestionJobRequest, JobCreatedResponse

router = APIRouter()


@router.post("/jobs", response_model=JobCreatedResponse, status_code=202)
async def submit_ingestion_job(
    body: IngestionJobRequest,
    market_repo=Depends(get_market_repo),
    job_manager=Depends(get_job_manager),
):
    """Submit a new ingestion job.

    Immediately returns the job_id so the caller can poll for status.
    The actual ingestion runs in a background thread.
    """
    job = job_manager.create(
        job_type=JobType.INGESTION,
        params={
            "instruments": body.instruments,
            "source": body.source.value,
            "start_date": body.start_date.isoformat(),
            "end_date": body.end_date.isoformat(),
        },
    )

    # Fire ingestion in a background thread (local dev mode)
    # In production this would enqueue to SQS/Fargate
    thread = threading.Thread(
        target=_run_job_bg,
        args=(
            job.id,
            body.instruments,
            body.source,
            body.start_date,
            body.end_date,
            market_repo,
            job_manager,
        ),
        daemon=True,
        name=f"ingestion-{job.id[:8]}",
    )
    thread.start()

    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}")
async def get_ingestion_job(
    job_id: str,
    job_manager=Depends(get_job_manager),
):
    """Poll the status of an ingestion job."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.get("/jobs")
async def list_ingestion_jobs(
    limit: int = 20,
    job_manager=Depends(get_job_manager),
):
    """List recent ingestion jobs (newest first)."""
    jobs = job_manager.list(job_type=JobType.INGESTION.value, limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


# ---------------------------------------------------------------------------
# Background helper
# ---------------------------------------------------------------------------

def _run_job_bg(job_id, instruments, source, start_date, end_date, market_repo, job_manager):
    """Wrapper so exceptions in the thread are caught and written to job state."""
    try:
        run_ingestion_job(
            job_id=job_id,
            instruments=instruments,
            source=source,
            start_date=start_date,
            end_date=end_date,
            market_repo=market_repo,
            job_manager=job_manager,
        )
    except Exception:
        # run_ingestion_job already calls job_manager.fail() — nothing more to do
        pass
