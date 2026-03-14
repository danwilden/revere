"""Generic job status and control routes.

GET  /api/jobs/{job_id}        — returns any job regardless of type.
POST /api/jobs/{job_id}/cancel — cancels a running or queued job.

Provides a single cross-type polling endpoint so frontends do not need to know
which job type produced a given job_id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_job_manager
from backend.schemas.enums import JobStatus
from backend.schemas.requests import JobResponse

router = APIRouter()

_TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    job_manager=Depends(get_job_manager),
):
    """Return job metadata and current status for any job type."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    job_manager=Depends(get_job_manager),
):
    """Cancel a QUEUED or RUNNING job.

    Returns 404 if the job does not exist.
    Returns 409 if the job is already in a terminal state.
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if JobStatus(job["status"]) in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is already {job['status']} and cannot be cancelled",
        )
    job_manager.cancel(job_id)
    return job_manager.get(job_id)
