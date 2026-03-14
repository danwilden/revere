"""Abstract base strategy interface.

Every concrete strategy (code-first or rules-engine-backed) must implement the
three abstract methods: should_enter_long, should_enter_short, should_exit.

The on_bar method is the single top-level call used by the backtester — it
composes the three abstract methods plus position-sizing and stop/take-profit
helpers into a single ActionDict.

ActionDict shapes (see backend.strategies.types):
  {"action": "enter_long",  "quantity": float, "stop": float|None, "target": float|None, "reason": str}
  {"action": "enter_short", "quantity": float, "stop": float|None, "target": float|None, "reason": str}
  {"action": "exit",  "reason": str}
  {"action": "hold"}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.strategies.state import StrategyState
from backend.strategies.types import ActionDict


class BaseStrategy(ABC):
    """Abstract interface all strategy implementations must satisfy."""

    # ------------------------------------------------------------------
    # Abstract methods — subclasses must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def should_enter_long(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
    ) -> bool:
        """Return True if conditions support entering a long position."""

    @abstractmethod
    def should_enter_short(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
    ) -> bool:
        """Return True if conditions support entering a short position."""

    @abstractmethod
    def should_exit(
        self,
        bar: dict,
        features: dict,
        position: dict,
        state: StrategyState,
    ) -> bool:
        """Return True if the current open position should be closed."""

    # ------------------------------------------------------------------
    # Optional overrides — sensible defaults provided
    # ------------------------------------------------------------------

    def position_size(self, bar: dict, equity: float, params: dict) -> float:
        """Return the position size in units. Default: 10 000 units."""
        return params.get("position_size_units", 10_000.0)

    def stop_price(self, bar: dict, side: str, params: dict) -> float | None:
        """Return the stop-loss price for a new entry, or None for no stop."""
        multiplier = params.get("stop_atr_multiplier")
        atr = bar.get("atr_14")
        if multiplier is None or atr is None:
            return None
        close = bar.get("close", 0.0)
        if side == "long":
            return close - multiplier * atr
        return close + multiplier * atr

    def take_profit_price(self, bar: dict, side: str, params: dict) -> float | None:
        """Return the take-profit price for a new entry, or None for no target."""
        multiplier = params.get("take_profit_atr_multiplier")
        atr = bar.get("atr_14")
        if multiplier is None or atr is None:
            return None
        close = bar.get("close", 0.0)
        if side == "long":
            return close + multiplier * atr
        return close - multiplier * atr

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def on_bar(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
        equity: float,
        params: dict,
    ) -> ActionDict:
        """Evaluate the current bar and return an ActionDict.

        Decision order is non-negotiable and must not be changed:
          1. Exit check (before entry) — prevents re-opening on the same bar
             after an exit signal fires. The backtester handles stop/target
             hits before calling on_bar, so this path fires for strategy exits.
          2. Cooldown check — enforces the post-exit standoff period before
             any new entry is considered. Supports 48-hour no-reentry rules.
          3. Entry check — only reached when flat and outside cooldown.

        Timestamps passed in bar must be naive UTC datetimes (no tzinfo).
        Timezone-aware datetimes will raise TypeError in in_cooldown().
        """
        current_time = bar.get("timestamp_utc") or bar.get("timestamp")

        # 1. Exit check first — strategy signal exit (stop/target handled by backtester).
        if state.open_position is not None:
            if self.should_exit(bar, features, state.open_position, state):
                return {"action": "exit", "reason": "strategy_signal"}
            return {"action": "hold"}

        # 2. Cooldown — do not enter during the post-exit standoff window.
        if current_time is not None and state.in_cooldown(current_time):
            return {"action": "hold"}

        # 3. Entry conditions — only checked when flat and not in cooldown.
        if self.should_enter_long(bar, features, state):
            qty = self.position_size(bar, equity, params)
            stop = self.stop_price(bar, "long", params)
            target = self.take_profit_price(bar, "long", params)
            return {"action": "enter_long", "quantity": qty, "stop": stop, "target": target, "reason": "long_signal"}

        if self.should_enter_short(bar, features, state):
            qty = self.position_size(bar, equity, params)
            stop = self.stop_price(bar, "short", params)
            target = self.take_profit_price(bar, "short", params)
            return {"action": "enter_short", "quantity": qty, "stop": stop, "target": target, "reason": "short_signal"}

        return {"action": "hold"}
