"""
Transaction cost model for backtesting.

For OANDA (spread-only broker):
    total_cost = spread + slippage  (no commission)

Default spreads are conservative estimates for the 7 major pairs.
Use time-varying spreads if you have tick data; fixed values are fine
for initial research.
"""


class CostModel:
    """
    Transaction cost model applied per-trade in VectorizedBacktester.

    Costs are expressed in pips and applied on entry and exit.
    In practice we charge the full spread + slippage on each side
    (round-trip) and halve it per leg, but applying total_cost_pips
    on each direction change is equivalent for most strategies.

    Default spreads (in pips) — conservative estimates:
        EUR_USD: 1.0  GBP_USD: 1.5  USD_JPY: 1.2
        USD_CHF: 1.5  AUD_USD: 1.5  NZD_USD: 2.0  USD_CAD: 1.5
    """

    DEFAULT_SPREADS: dict[str, float] = {
        "EUR_USD": 1.0,
        "GBP_USD": 1.5,
        "USD_JPY": 1.2,
        "USD_CHF": 1.5,
        "AUD_USD": 1.5,
        "NZD_USD": 2.0,
        "USD_CAD": 1.5,
    }

    def __init__(
        self,
        spread_pips: dict[str, float] | None = None,
        slippage_pips: float = 0.5,
    ) -> None:
        self.spread_pips = spread_pips or dict(self.DEFAULT_SPREADS)
        self.slippage_pips = slippage_pips

    def total_cost_pips(self, instrument: str) -> float:
        """Total one-way cost in pips (spread + slippage)."""
        spread = self.spread_pips.get(instrument, 2.0)
        return spread + self.slippage_pips

    def cost_per_unit(self, instrument: str, pip_size: float = 0.0001) -> float:
        """
        Total one-way cost in price units per unit of position.
        Multiply by units to get cost in price units; then × pip_value for USD.
        """
        return self.total_cost_pips(instrument) * pip_size
