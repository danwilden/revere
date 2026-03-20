"""Risk filter signal — create a RISK_FILTER signal backed by a rules DSL expression.

A risk filter evaluates a rules DSL node against bar features and produces:
  0 = trade allowed (rules did not trigger)
  1 = trade blocked (rules triggered)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from backend.data.repositories import MetadataRepository
from backend.schemas.enums import SignalType
from backend.schemas.models import Signal


def build_risk_filter_signal(
    name: str,
    rules_node: dict,
    description: str,
    metadata_repo: MetadataRepository,
    feature_run_id: str = "",
) -> Signal:
    """Create a RISK_FILTER signal backed by a rules DSL expression.

    Args:
        name: human-readable signal name.
        rules_node: standard rules DSL node dict (see rules_engine.py).
        description: text description of the filter.
        metadata_repo: metadata store for persistence.
        feature_run_id: ID of the FeatureRun to evaluate against when materialized.

    Returns:
        Persisted Signal instance.
    """
    signal = Signal(
        id=str(uuid.uuid4()),
        name=name,
        signal_type=SignalType.RISK_FILTER,
        definition_json={
            "rules": rules_node,
            "field_name": "risk_filter",
            "description": description,
            "feature_run_id": feature_run_id,
        },
        source_model_id=None,
        version=1,
        created_at=datetime.utcnow(),
    )
    metadata_repo.save_signal(signal.model_dump(mode="json"))
    return signal
