"""
Instrument metadata from OANDA — pip locations, margin rates, minimum trade sizes.

When OANDA account ID is not set (e.g. offline validation), the registry
uses static metadata for known majors so pip_value and sizing work without API.
"""

from dataclasses import dataclass

import oandapyV20.endpoints.accounts as accts_ep
from loguru import logger

from forex_system.config import settings
from forex_system.data.oanda_client import client


# Static metadata for offline use (validation, backtests) when account_id is unset.
# pip_location: JPY pairs -2 (0.01), others typically -4 (0.0001).
_STATIC_META: dict[str, "InstrumentMeta"] = {}


def _build_static_registry() -> dict[str, "InstrumentMeta"]:
    if _STATIC_META:
        return _STATIC_META
    pairs = [
        ("USD_JPY", -2),
        ("USD_CHF", -4),
        ("EUR_USD", -4),
        ("GBP_USD", -4),
        ("AUD_USD", -4),
        ("NZD_USD", -4),
        ("USD_CAD", -4),
    ]
    for name, pip_loc in pairs:
        _STATIC_META[name] = InstrumentMeta(
            name=name,
            display_name=name.replace("_", "/"),
            pip_location=pip_loc,
            trade_units_precision=0,
            margin_rate=0.02,
            min_trade_size=1,
        )
    return _STATIC_META


@dataclass
class InstrumentMeta:
    name: str               # e.g. "EUR_USD"
    display_name: str       # e.g. "EUR/USD"
    pip_location: int       # e.g. -4  → pip = 0.0001
    trade_units_precision: int
    margin_rate: float      # e.g. 0.02 for 50:1 max leverage
    min_trade_size: int

    @property
    def pip_size(self) -> float:
        """One pip in price units. e.g. 0.0001 for EUR_USD, 0.01 for USD_JPY."""
        return 10.0 ** self.pip_location


class InstrumentRegistry:
    """
    Fetches and caches instrument metadata from OANDA.
    Lazy-loaded: first call to .get() triggers a fetch if not cached.
    """

    def __init__(self) -> None:
        self._cache: dict[str, InstrumentMeta] = {}

    def load(self, instruments: list[str] | None = None) -> None:
        """
        Fetch metadata for given instruments (or all available instruments).
        When account_id is unset (offline validation), use static metadata for known majors.
        """
        if not client.account_id:
            static = _build_static_registry()
            to_load = instruments or list(static)
            for name in to_load:
                if name in static:
                    self._cache[name] = static[name]
            if self._cache:
                logger.debug(
                    f"Loaded {len(self._cache)} instruments from static (no account_id)"
                )
            return

        params: dict = {}
        if instruments:
            params["instruments"] = ",".join(instruments)

        req = accts_ep.AccountInstruments(
            accountID=client.account_id,
            params=params,
        )
        resp = client.request(req)

        for inst in resp.get("instruments", []):
            meta = InstrumentMeta(
                name=inst["name"],
                display_name=inst["displayName"],
                pip_location=int(inst["pipLocation"]),
                trade_units_precision=int(inst["tradeUnitsPrecision"]),
                margin_rate=float(inst["marginRate"]),
                min_trade_size=int(float(inst.get("minimumTradeSize", "1"))),
            )
            self._cache[meta.name] = meta

        logger.info(f"Loaded metadata for {len(self._cache)} instruments")

    def get(self, instrument: str) -> InstrumentMeta:
        """Return metadata for an instrument, fetching from API if needed."""
        if instrument not in self._cache:
            self.load([instrument])
        if instrument not in self._cache:
            raise KeyError(f"Instrument not found: {instrument}")
        return self._cache[instrument]

    def load_majors(self) -> None:
        """Convenience: pre-load all major pairs from settings."""
        self.load(settings.major_pairs)

    def all_cached(self) -> dict[str, InstrumentMeta]:
        return dict(self._cache)


# Module-level singleton
registry = InstrumentRegistry()
