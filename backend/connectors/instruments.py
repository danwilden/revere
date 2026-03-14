"""Instrument registry for the MVP currency pairs.

Config-driven: the four default pairs come from settings.default_pairs.
Static metadata is defined here; OANDA pip_location is queried live only when
needed for sizing (not at import time).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import settings
from backend.schemas.enums import InstrumentCategory


@dataclass(frozen=True)
class InstrumentSpec:
    """Static metadata for a tradeable FX instrument."""

    symbol: str                     # e.g. "EUR_USD"
    base_currency: str              # e.g. "EUR"
    quote_currency: str             # e.g. "USD"
    category: InstrumentCategory
    pip_size: float                 # e.g. 0.0001 for EUR_USD, 0.01 for USD_JPY
    price_precision: int            # decimal places for display
    # Mapping of source name → source-specific symbol (if it differs)
    source_symbol_map: dict[str, str] = field(default_factory=dict)

    @property
    def oanda_symbol(self) -> str:
        return self.source_symbol_map.get("oanda", self.symbol)

    @property
    def dukascopy_symbol(self) -> str:
        return self.source_symbol_map.get("dukascopy", self.symbol.replace("_", ""))


# ---------------------------------------------------------------------------
# Static specs for all supported instruments
# ---------------------------------------------------------------------------

_SPECS: dict[str, InstrumentSpec] = {
    "EUR_USD": InstrumentSpec(
        symbol="EUR_USD",
        base_currency="EUR",
        quote_currency="USD",
        category=InstrumentCategory.MAJOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "EUR_USD", "dukascopy": "EURUSD"},
    ),
    "GBP_USD": InstrumentSpec(
        symbol="GBP_USD",
        base_currency="GBP",
        quote_currency="USD",
        category=InstrumentCategory.MAJOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "GBP_USD", "dukascopy": "GBPUSD"},
    ),
    "USD_JPY": InstrumentSpec(
        symbol="USD_JPY",
        base_currency="USD",
        quote_currency="JPY",
        category=InstrumentCategory.MAJOR,
        pip_size=0.01,
        price_precision=3,
        source_symbol_map={"oanda": "USD_JPY", "dukascopy": "USDJPY"},
    ),
    "AUD_USD": InstrumentSpec(
        symbol="AUD_USD",
        base_currency="AUD",
        quote_currency="USD",
        category=InstrumentCategory.MAJOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "AUD_USD", "dukascopy": "AUDUSD"},
    ),
    "USD_CHF": InstrumentSpec(
        symbol="USD_CHF",
        base_currency="USD",
        quote_currency="CHF",
        category=InstrumentCategory.MAJOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "USD_CHF", "dukascopy": "USDCHF"},
    ),
    "NZD_USD": InstrumentSpec(
        symbol="NZD_USD",
        base_currency="NZD",
        quote_currency="USD",
        category=InstrumentCategory.MAJOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "NZD_USD", "dukascopy": "NZDUSD"},
    ),
    "USD_CAD": InstrumentSpec(
        symbol="USD_CAD",
        base_currency="USD",
        quote_currency="CAD",
        category=InstrumentCategory.MAJOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "USD_CAD", "dukascopy": "USDCAD"},
    ),
    "EUR_GBP": InstrumentSpec(
        symbol="EUR_GBP",
        base_currency="EUR",
        quote_currency="GBP",
        category=InstrumentCategory.MINOR,
        pip_size=0.0001,
        price_precision=5,
        source_symbol_map={"oanda": "EUR_GBP", "dukascopy": "EURGBP"},
    ),
    "EUR_JPY": InstrumentSpec(
        symbol="EUR_JPY",
        base_currency="EUR",
        quote_currency="JPY",
        category=InstrumentCategory.MINOR,
        pip_size=0.01,
        price_precision=3,
        source_symbol_map={"oanda": "EUR_JPY", "dukascopy": "EURJPY"},
    ),
    "GBP_JPY": InstrumentSpec(
        symbol="GBP_JPY",
        base_currency="GBP",
        quote_currency="JPY",
        category=InstrumentCategory.MINOR,
        pip_size=0.01,
        price_precision=3,
        source_symbol_map={"oanda": "GBP_JPY", "dukascopy": "GBPJPY"},
    ),
}


def get_spec(symbol: str) -> InstrumentSpec:
    """Return InstrumentSpec for the given symbol or raise KeyError."""
    if symbol not in _SPECS:
        raise KeyError(f"No instrument spec for '{symbol}'. Available: {list(_SPECS)}")
    return _SPECS[symbol]


def get_default_specs() -> list[InstrumentSpec]:
    """Return specs for the configured default pairs (from settings)."""
    return [get_spec(s) for s in settings.default_pairs if s in _SPECS]


def all_specs() -> dict[str, InstrumentSpec]:
    return dict(_SPECS)
