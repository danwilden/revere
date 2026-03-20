"""AutoML signal bank — convert an accepted AutoML job result into a Signal."""
from __future__ import annotations

import uuid
from datetime import datetime

from backend.data.repositories import MetadataRepository
from backend.schemas.enums import SignalType
from backend.schemas.models import AutoMLJobRecord, Signal


def create_signal_from_automl(
    automl_record: AutoMLJobRecord,
    metadata_repo: MetadataRepository,
    signal_name: str | None = None,
) -> Signal:
    """Convert an accepted AutoML job to a Signal bank entry.

    Args:
        automl_record: The completed and accepted AutoMLJobRecord.
        metadata_repo: Metadata store.
        signal_name: Optional override for the signal name.

    Returns:
        Persisted Signal instance.
    """
    signal_type = (
        SignalType.AUTOML_DIRECTION_PROB
        if automl_record.target_type == "direction"
        else SignalType.AUTOML_RETURN_BUCKET
    )

    name = signal_name or (
        f"automl_{automl_record.instrument_id}_{automl_record.target_type}_{automl_record.id[:8]}"
    )

    definition_json = {
        "automl_job_id": automl_record.job_id,
        "candidate_id": automl_record.best_candidate_id,
        "auc_roc": automl_record.best_auc_roc,
        "best_model_artifact_key": automl_record.best_model_artifact_key,
        "instrument_id": automl_record.instrument_id,
        "timeframe": automl_record.timeframe,
        "feature_run_id": automl_record.feature_run_id,
        "model_id": automl_record.model_id,
    }

    metadata: dict = {}
    if signal_type == SignalType.AUTOML_DIRECTION_PROB:
        metadata["field_name"] = "automl_direction_prob"
    else:
        metadata["field_name"] = "automl_return_bucket"

    signal = Signal(
        id=str(uuid.uuid4()),
        name=name,
        signal_type=signal_type,
        definition_json=definition_json,
        source_model_id=automl_record.model_id,
        version=1,
        created_at=datetime.utcnow(),
        metadata=metadata,
    )
    metadata_repo.save_signal(signal.model_dump(mode="json"))
    return signal
