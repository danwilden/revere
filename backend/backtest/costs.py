"""Transaction cost model for backtest fill simulation.

All cost values are in price-point terms (same units as bar OHLC prices).
CostModel is a value-type dataclass so it serializes cleanly to and from
the BacktestRun.cost_model_json field for reproducible auditing.

Cost accounting:
  - Half-spread at entry + half-spread at exit → full round-trip spread cost.
  - Slippage at entry only (strategy/stop exits are limit-style; no extra slippage).
  - Commission deducted as a flat per-unit fee on both sides.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    """Encapsulates transaction cost assumptions for one backtest run.

    Attributes
    ----------
    spread_pips:
        Round-trip bid-ask spread in pips.  Half-spread is applied at entry,
        half at strategy-signal exit (not at stop/target fills — those are
        treated as limit executions already priced at the stop/target level).
    slippage_pips:
        Additional market-impact slippage at entry only (pips).
    commission_per_unit:
        Flat commission per unit, charged on both entry and exit sides.
    pip_size:
        Price value of one pip for the instrument (0.0001 for 4-decimal pairs,
        0.01 for JPY pairs).
    """

    spread_pips: float = 2.0
    slippage_pips: float = 0.5
    commission_per_unit: float = 0.0
    pip_size: float = 0.0001

    def entry_price_adjustment(self, side: str) -> float:
        """Signed price delta applied at the entry fill.

        Long  → buy at ask = mid + (half-spread + slippage) → positive.
        Short → sell at bid = mid − (half-spread + slippage) → negative.
        """
        cost_pips = (self.spread_pips / 2.0) + self.slippage_pips
        adjustment = cost_pips * self.pip_size
        return adjustment if side == "long" else -adjustment

    def exit_price_adjustment(self, side: str) -> float:
        """Signed price delta applied at a strategy-signal exit fill.

        Long exit  → sell at bid = mid − half-spread → negative.
        Short exit → buy at ask = mid + half-spread  → positive.
        No slippage on exit — limit-style execution assumed.
        """
        half_spread = (self.spread_pips / 2.0) * self.pip_size
        return -half_spread if side == "long" else half_spread

    def commission_cost(self, quantity: float) -> float:
        """Total round-trip commission for a trade (both entry and exit sides)."""
        return 2.0 * self.commission_per_unit * abs(quantity)

    @classmethod
    def from_dict(cls, d: dict) -> "CostModel":
        """Reconstruct from the cost_model_json dict stored on BacktestRun."""
        return cls(
            spread_pips=d.get("spread_pips", 2.0),
            slippage_pips=d.get("slippage_pips", 0.5),
            commission_per_unit=d.get("commission_per_unit", 0.0),
            pip_size=d.get("pip_size", 0.0001),
        )

    def to_dict(self) -> dict:
        return {
            "spread_pips": self.spread_pips,
            "slippage_pips": self.slippage_pips,
            "commission_per_unit": self.commission_per_unit,
            "pip_size": self.pip_size,
        }
