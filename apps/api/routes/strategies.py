"""Strategy CRUD API routes.

POST /api/strategies                         — create strategy record (201)
GET  /api/strategies                         — list all strategies
GET  /api/strategies/{strategy_id}           — get strategy by ID
POST /api/strategies/{strategy_id}/validate  — validate definition_json
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_metadata_repo
from backend.schemas.enums import StrategyType
from backend.schemas.models import Strategy
from backend.schemas.requests import StrategyCreateRequest, StrategyValidateRequest
from backend.strategies.validation import (
    validate_python_strategy,
    validate_rules_strategy,
)

router = APIRouter()


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
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


@router.get("")
def list_strategies(metadata_repo=Depends(get_metadata_repo)):
    """Return all strategy records."""
    return metadata_repo.list_strategies()


@router.get("/{strategy_id}")
def get_strategy(strategy_id: str, metadata_repo=Depends(get_metadata_repo)):
    """Return a single strategy by ID."""
    record = metadata_repo.get_strategy(strategy_id)
    if not record:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return record


@router.post("/{strategy_id}/validate")
def validate_strategy(
    strategy_id: str,
    req: StrategyValidateRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    """Validate the definition_json of an existing strategy (or a supplied one)."""
    errors = _run_validation(req.strategy_type, req.definition_json)
    return {"valid": len(errors) == 0, "errors": errors}
