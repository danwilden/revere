"""Strategy CRUD API routes.

POST /api/strategies                         — create strategy record (201)
GET  /api/strategies                         — list all strategies
GET  /api/strategies/{strategy_id}           — get strategy by ID
POST /api/strategies/{strategy_id}/validate  — validate definition_json
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.deps import get_metadata_repo
from backend.schemas.enums import StrategyType
from backend.schemas.models import Strategy
from backend.schemas.requests import (
    StrategyCreateRequest,
    StrategyListResponse,
    StrategyResponse,
    StrategyValidateRequest,
    StrategyValidateResponse,
)
from backend.strategies.rules_engine import validate_signal_fields
from backend.strategies.validation import (
    validate_field_availability,
    validate_python_strategy,
    validate_rules_strategy,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Extended validation schemas (route-local; requests.py is not modified)
# ---------------------------------------------------------------------------

class StrategyValidateWithSignalsRequest(BaseModel):
    """Extends StrategyValidateRequest with optional signal_ids and feature_run_id."""
    definition_json: dict[str, Any]
    strategy_type: StrategyType
    signal_ids: list[str] | None = None
    feature_run_id: str | None = None


class StrategyValidateWithWarningsResponse(BaseModel):
    """Extends StrategyValidateResponse with warnings list."""
    valid: bool
    errors: list[str]
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_validation(strategy_type: StrategyType, definition_json: dict) -> list[str]:
    """Dispatch to the correct validator based on strategy_type."""
    if strategy_type == StrategyType.RULES_ENGINE:
        return validate_rules_strategy(definition_json)
    if strategy_type == StrategyType.PYTHON:
        return validate_python_strategy(definition_json)
    # HYBRID — validate both halves if present
    errors: list[str] = []
    if "rules" in definition_json:
        errors.extend(validate_rules_strategy(definition_json["rules"]))
    if "code" in definition_json:
        errors.extend(validate_python_strategy(definition_json))
    return errors


def _compute_signal_field_names(signal_record: dict) -> list[str]:
    """Compute the field names a signal would contribute to bar context.

    Returns a list of field name strings.
    """
    metadata = signal_record.get("metadata", {}) or {}

    names: list[str] = []
    field_name = metadata.get("field_name")
    if field_name:
        names.append(field_name)

    # HMM_STATE_PROB or similar with field_name_prefix and n_states
    prefix = metadata.get("field_name_prefix")
    if prefix:
        n_states = metadata.get("n_states", 0)
        for i in range(n_states):
            names.append(f"{prefix}_{i}_prob")

    return names


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=StrategyResponse, status_code=201)
def create_strategy(
    req: StrategyCreateRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    """Create and persist a new strategy record."""
    strategy = Strategy(
        name=req.name,
        description=req.description,
        strategy_type=req.strategy_type,
        definition_json=req.definition_json,
        tags=req.tags,
    )
    metadata_repo.save_strategy(strategy.model_dump(mode="json"))
    return strategy.model_dump(mode="json")


@router.get("", response_model=StrategyListResponse)
def list_strategies(metadata_repo=Depends(get_metadata_repo)):
    """Return all strategy records."""
    strategies = metadata_repo.list_strategies()
    return {"strategies": strategies, "count": len(strategies)}


@router.get("/{strategy_id}", response_model=StrategyResponse)
def get_strategy(strategy_id: str, metadata_repo=Depends(get_metadata_repo)):
    """Return a single strategy by ID."""
    record = metadata_repo.get_strategy(strategy_id)
    if not record:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return record


@router.post(
    "/{strategy_id}/validate",
    response_model=StrategyValidateWithWarningsResponse,
)
def validate_strategy(
    strategy_id: str,
    req: StrategyValidateWithSignalsRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    """Validate the definition_json of an existing strategy (or a supplied one).

    When *signal_ids* is provided, also checks that any signal-derived field
    references in the rules DSL can be resolved against the declared signals.
    Unresolved field names are returned as *warnings* (not errors), since the
    fields may come from features or bar columns not known at validation time.
    """
    errors = _run_validation(req.strategy_type, req.definition_json)

    # Field availability check — gates on feature-dependent fields when no feature run
    if req.strategy_type == StrategyType.RULES_ENGINE:
        errors.extend(validate_field_availability(req.definition_json, req.feature_run_id))

    warnings: list[str] = []
    if req.signal_ids and req.strategy_type == StrategyType.RULES_ENGINE:
        # Build the set of field names contributed by declared signals
        available: set[str] = set()
        for sig_id in req.signal_ids:
            sig = metadata_repo.get_signal(sig_id)
            if sig is None:
                warnings.append(f"Signal '{sig_id}' not found in metadata store")
                continue
            available.update(_compute_signal_field_names(sig))

        # Validate field references against available signal fields
        unresolved = validate_signal_fields(req.definition_json, available)
        for field in unresolved:
            warnings.append(
                f"Field '{field}' referenced in rules DSL is not provided by any declared signal"
            )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
