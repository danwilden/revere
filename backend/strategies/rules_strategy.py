"""Rules-engine-backed strategy adapter.

Implements BaseStrategy using a declarative JSON rule definition evaluated
by the rules engine.
"""
from __future__ import annotations

from typing import Any

from backend.strategies.base import BaseStrategy
from backend.strategies.rules_engine import evaluate
from backend.strategies.state import StrategyState


class RulesStrategy(BaseStrategy):
    """Strategy driven by a declarative rules DSL definition.

    The definition_json must conform to the schema validated by
    ``validate_rules_strategy``:
    {
        "entry_long":  <rule_node>,
        "entry_short": <rule_node or null>,
        "exit":        <rule_node>,
        "stop_atr_multiplier": 2.0,            # optional
        "take_profit_atr_multiplier": 3.0,     # optional
        "cooldown_hours": 48.0,                # optional
        "position_size_units": 10000.0,        # optional
        "named_conditions": {}                 # optional
    }
    """

    def __init__(self, definition_json: dict) -> None:
        self._def = definition_json
        self._named: dict = definition_json.get("named_conditions", {}) or {}

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context(bar: dict, features: dict) -> dict:
        ctx = {}
        ctx.update(features)
        ctx.update(bar)
        return ctx

    # ------------------------------------------------------------------
    # BaseStrategy implementation
    # ------------------------------------------------------------------

    def should_enter_long(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
    ) -> bool:
        entry_long_node = self._def.get("entry_long")
        if entry_long_node is None:
            return False
        ctx = self._build_context(bar, features)
        return evaluate(entry_long_node, ctx, self._named)

    def should_enter_short(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
    ) -> bool:
        entry_short_node = self._def.get("entry_short")
        if entry_short_node is None:
            return False
        ctx = self._build_context(bar, features)
        return evaluate(entry_short_node, ctx, self._named)

    def should_exit(
        self,
        bar: dict,
        features: dict,
        position: dict,
        state: StrategyState,
    ) -> bool:
        exit_node = self._def.get("exit")
        if exit_node is None:
            return False
        ctx = self._build_context(bar, features)
        return evaluate(exit_node, ctx, self._named)

    def position_size(self, bar: dict, equity: float, params: dict) -> float:
        units = self._def.get("position_size_units")
        if units is not None:
            return float(units)
        return super().position_size(bar, equity, params)

    def stop_price(self, bar: dict, side: str, params: dict) -> float | None:
        merged = {**params}
        if "stop_atr_multiplier" in self._def:
            merged["stop_atr_multiplier"] = self._def["stop_atr_multiplier"]
        return super().stop_price(bar, side, merged)

    def take_profit_price(self, bar: dict, side: str, params: dict) -> float | None:
        merged = {**params}
        if "take_profit_atr_multiplier" in self._def:
            merged["take_profit_atr_multiplier"] = self._def["take_profit_atr_multiplier"]
        return super().take_profit_price(bar, side, merged)
