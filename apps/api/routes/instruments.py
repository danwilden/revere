"""GET /api/instruments — return available instrument specs."""
from __future__ import annotations

from fastapi import APIRouter

from backend.connectors.instruments import all_specs, get_default_specs
from backend.config import settings

router = APIRouter()


@router.get("")
async def list_instruments(defaults_only: bool = False):
    """Return instrument specs.

    Query params:
        defaults_only=true  — return only the configured default pairs
        (no param)          — return all registered instruments
    """
    specs = get_default_specs() if defaults_only else list(all_specs().values())
    return [
        {
            "symbol": s.symbol,
            "base_currency": s.base_currency,
            "quote_currency": s.quote_currency,
            "category": s.category.value,
            "pip_size": s.pip_size,
            "price_precision": s.price_precision,
            "oanda_symbol": s.oanda_symbol,
            "dukascopy_symbol": s.dukascopy_symbol,
        }
        for s in specs
    ]


@router.get("/defaults")
async def list_default_instruments():
    """Return the configured default MVP pairs (from settings.default_pairs)."""
    return {
        "default_pairs": settings.default_pairs,
        "instruments": [
            {
                "symbol": s.symbol,
                "base_currency": s.base_currency,
                "quote_currency": s.quote_currency,
                "category": s.category.value,
                "pip_size": s.pip_size,
            }
            for s in get_default_specs()
        ],
    }
