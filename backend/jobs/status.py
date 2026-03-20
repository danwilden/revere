"""Job run lifecycle management.

Thin layer on top of MetadataRepository that owns the status-state-machine
logic so all callers use a consistent API.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend.schemas.enums import JobStatus, JobType
from backend.schemas.models import JobRun

logger = logging.getLogger(__name__)


class JobManager:
    """Create and update JobRun records via a MetadataRepository."""

    def __init__(self, metadata_repo) -> None:
        self._repo = metadata_repo

    def create(
        self,
        job_type: JobType,
        params: dict[str, Any] | None = None,
        requested_by: str = "system",
    ) -> JobRun:
        job = JobRun(
            job_type=job_type,
            status=JobStatus.QUEUED,
            params_json=params or {},
            requested_by=requested_by,
        )
        self._repo.save_job_run(job.model_dump())
        return job

    def start(self, job_id: str, stage_label: str = "") -> None:
        self._repo.update_job_run(job_id, {
            "status": JobStatus.RUNNING.value,
            "started_at": datetime.utcnow().isoformat(),
            "stage_label": stage_label,
        })

    def progress(self, job_id: str, pct: float, stage_label: str = "") -> None:
        updates: dict[str, Any] = {"progress_pct": pct}
        if stage_label:
            updates["stage_label"] = stage_label
        self._repo.update_job_run(job_id, updates)

    def succeed(
        self,
        job_id: str,
        result_ref: str | None = None,
        logs_ref: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {
            "status": JobStatus.SUCCEEDED.value,
            "progress_pct": 100.0,
            "completed_at": datetime.utcnow().isoformat(),
        }
        if result_ref:
            updates["result_ref"] = result_ref
        if logs_ref:
            updates["logs_ref"] = logs_ref
        self._repo.update_job_run(job_id, updates)
        logger.info("Job succeeded: job_id=%s", job_id)

    def fail(
        self,
        job_id: str,
        error_message: str,
        error_code: str = "UNKNOWN_ERROR",
        logs_ref: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {
            "status": JobStatus.FAILED.value,
            "completed_at": datetime.utcnow().isoformat(),
            "error_message": error_message,
            "error_code": error_code,
        }
        if logs_ref:
            updates["logs_ref"] = logs_ref
        self._repo.update_job_run(job_id, updates)
        snippet = (error_message or "")[:200]
        logger.error("Job failed: job_id=%s error_code=%s error_message=%s", job_id, error_code, snippet)

    def cancel(self, job_id: str) -> None:
        self._repo.update_job_run(job_id, {
            "status": JobStatus.CANCELLED.value,
            "completed_at": datetime.utcnow().isoformat(),
        })

    def get(self, job_id: str) -> dict | None:
        return self._repo.get_job_run(job_id)

    def list(self, job_type: str | None = None, limit: int = 50) -> list[dict]:
        return self._repo.list_job_runs(job_type=job_type, limit=limit)
