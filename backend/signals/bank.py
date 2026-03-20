"""Signal bank — create and manage reusable signal definitions.

A signal bank entry describes how to produce signal values over a date range.
For HMM-derived signals, materializing the signal runs out-of-sample inference
and returns regime labels as signal values.

Signal types:
  - hmm_regime: derived from a trained HMM model
  - code: custom Python signal (Phase 3+)
  - declarative: rules-based (Phase 3+)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from backend.data.repositories import MetadataRepository
from backend.schemas.enums import SignalType
from backend.schemas.models import Signal


def create_signal_from_hmm(
    name: str,
    model_id: str,
    feature_run_id: str,
    metadata_repo: MetadataRepository,
    description: str = "",
) -> Signal:
    """Create a signal bank entry backed by a trained HMM model.

    The signal produces regime label strings when materialized.

    Args:
        name: human-readable signal name
        model_id: ID of the trained ModelRecord
        feature_run_id: ID of the FeatureRun used for training (used for
                        out-of-sample inference by the materializer)
        metadata_repo: metadata store
        description: optional description

    Returns:
        Persisted Signal instance.
    """
    model_record = metadata_repo.get_model(model_id)
    if not model_record:
        raise ValueError(f"Model {model_id} not found in metadata store")

    signal = Signal(
        id=str(uuid.uuid4()),
        name=name,
        signal_type=SignalType.HMM_REGIME,
        definition_json={
            "model_id": model_id,
            "feature_run_id": feature_run_id,
            "instrument_id": model_record.get("instrument_id", ""),
            "timeframe": model_record.get("timeframe", ""),
            "description": description,
        },
        metadata={"field_name": "hmm_regime"},
        source_model_id=model_id,
        version=1,
        created_at=datetime.utcnow(),
    )
    metadata_repo.save_signal(signal.model_dump(mode="json"))
    return signal


def get_signal(signal_id: str, metadata_repo: MetadataRepository) -> dict | None:
    return metadata_repo.get_signal(signal_id)


def list_signals(metadata_repo: MetadataRepository) -> list[dict]:
    return metadata_repo.list_signals()
