"""Fill simulation for the event-driven backtesting engine.

Three fill scenarios:
  1. Entry fills    — market order at bar close ± CostModel spread/slippage.
  2. Stop/target    — detected intrabar via high/low; fills at exact stop/target price.
  3. Strategy exits — market order at bar close ± CostModel exit spread.

Stop priority rule: when both stop and target are crossed on the same bar (a
wide-range bar), the stop fill takes priority.  This is the conservative choice
— it prevents an unrealistically optimistic "stop AND target both hit" scenario.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.backtest.costs import CostModel


@dataclass
class FillResult:
    """Result of checking a bar for an intrabar stop or target hit."""

    hit: bool
    exit_price: float
    exit_reason: Literal["stop_hit", "target_hit", "none"]


def compute_entry_fill(bar: dict, side: str, cost_model: CostModel) -> float:
    """Return the fill price for a new position opened at this bar's close.

    Simulates a market order at close, adjusted for half-spread + slippage.
    """
    return bar["close"] + cost_model.entry_price_adjustment(side)


def compute_exit_fill(bar: dict, side: str, cost_model: CostModel) -> float:
    """Return the fill price for a strategy-signal exit at this bar's close.

    Simulates a market close order, adjusted for half-spread only.
    No extra slippage — strategy exits are treated as limit-style executions.
    """
    return bar["close"] + cost_model.exit_price_adjustment(side)


def check_stop_target(
    bar: dict,
    side: str,
    stop: float | None,
    target: float | None,
) -> FillResult:
    """Detect whether stop-loss or take-profit was hit during this bar.

    Uses the bar's high and low for intrabar detection (not just the close).
    If both stop and target are crossed on the same bar, stop takes priority.

    Long  — stop hit when low  <= stop;  target hit when high >= target.
    Short — stop hit when high >= stop;  target hit when low  <= target.
    """
    low = bar["low"]
    high = bar["high"]

    stop_hit = False
    target_hit = False

    if side == "long":
        stop_hit = stop is not None and low <= stop
        target_hit = target is not None and high >= target
    else:  # short
        stop_hit = stop is not None and high >= stop
        target_hit = target is not None and low <= target

    # Conservative: stop takes priority when both are triggered on the same bar.
    if stop_hit:
        assert stop is not None
        return FillResult(hit=True, exit_price=stop, exit_reason="stop_hit")
    if target_hit:
        assert target is not None
        return FillResult(hit=True, exit_price=target, exit_reason="target_hit")
    return FillResult(hit=False, exit_price=bar["close"], exit_reason="none")
