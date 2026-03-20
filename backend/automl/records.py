"""AutoML domain records — Pydantic models for AutoML job persistence.

These models are owned by the automl package and stored via
LocalMetadataRepository under the "automl_jobs" store key.

If Team 1 later centralises AutoMLJobRecord into backend/schemas/models.py,
this module can be updated to import from there and re-export.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ModelEvaluation(BaseModel):
    """Structured output produced by model_researcher_node after LLM analysis."""

    candidate_id: str
    accept: bool
    rationale: str
    auc_roc: float


class AutoMLJobRecord(BaseModel):
    """Metadata record for a SageMaker AutoML job, persisted in the metadata store."""

    id: str = Field(default_factory=_new_id)
    job_name: str                        # SageMaker AutoML job name (unique key)
    target_column: str
    target_type: str                     # "direction" | "return_bucket"
    train_s3_uri: str
    output_s3_prefix: str
    max_runtime_seconds: int = 3600
    status: str = "queued"               # "queued" | "running" | "completed" | "failed"
    failure_reason: str | None = None
    best_candidate_id: str | None = None
    best_auc_roc: float | None = None
    evaluation: dict[str, Any] | None = None   # ModelEvaluation dict when available
    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)
