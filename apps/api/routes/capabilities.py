"""Capabilities discovery and inspection API.

Exposes the platform's capability taxonomy so agents and frontend tooling
can inspect what fields and primitives are available in the strategy DSL.

Routes:
  GET  /api/capabilities           — list all known capabilities (optional ?taxonomy= filter)
  GET  /api/capabilities/inspect   — classify one field by name (?field=<name>)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.strategies.capabilities import (
    CapabilityTaxonomy,
    inspect_capability,
    list_capabilities,
    list_native_fields,
)

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class CapabilityResponse(BaseModel):
    name: str
    taxonomy: str         # CapabilityTaxonomy.value (string)
    description: str
    available: bool
    resolution_hint: str
    requires_feature_run: bool = False


def _to_response(record: Any) -> CapabilityResponse:
    return CapabilityResponse(
        name=record.name,
        taxonomy=record.taxonomy.value,
        description=record.description,
        available=record.available,
        resolution_hint=record.resolution_hint,
        requires_feature_run=record.requires_feature_run,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[CapabilityResponse])
def list_all_capabilities(
    taxonomy: str | None = Query(default=None, description="Filter by taxonomy value"),
) -> list[CapabilityResponse]:
    """List all known capabilities, optionally filtered by taxonomy.

    Valid taxonomy values: market_feature, state_marker, native_primitive,
    signal_field, unknown.
    """
    tax_enum: CapabilityTaxonomy | None = None
    if taxonomy is not None:
        try:
            tax_enum = CapabilityTaxonomy(taxonomy)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown taxonomy '{taxonomy}'. Valid values: {[t.value for t in CapabilityTaxonomy]}",
            )
    records = list_capabilities(tax_enum)
    return [_to_response(r) for r in records]


@router.get("/native-fields", response_model=list[str])
def get_native_fields() -> list[str]:
    """Return all field names unconditionally available in the backtest bar context.

    These fields are safe to use in strategy rules without a feature_run_id.
    Includes OHLCV, bar metadata, and engine-injected trade lifecycle markers.
    """
    return list_native_fields()


@router.get("/inspect", response_model=CapabilityResponse)
def inspect_field_capability(
    field: str = Query(..., description="Field name to classify"),
    feature_run_version: str | None = Query(
        default=None,
        description=(
            "Feature-run version string (e.g. 'v1.0', 'v1.1', 'v1.2'). "
            "When provided, market-feature availability is gated against the "
            "field's minimum required version. Legacy runs receive a remediation hint."
        ),
    ),
) -> CapabilityResponse:
    """Classify a single field name into the capability taxonomy.

    Always returns a result — UNKNOWN taxonomy means the field is not
    in any static registry and may need agent investigation or feature discovery.

    Pass feature_run_version to get version-aware availability for calendar and
    cyclical features that require v1.1+ or v1.2+ feature runs.
    """
    record = inspect_capability(field, feature_run_version=feature_run_version)
    return _to_response(record)
