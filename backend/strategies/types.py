"""Shared TypedDict contracts for the strategy layer.

These types are consumed by StrategyState (Position), BaseStrategy (ActionDict),
and the backtesting engine. Centralising them here avoids circular imports and
gives the backtester a single import target for the strategy interface contract.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


# ---------------------------------------------------------------------------
# Position — shape of StrategyState.open_position
# ---------------------------------------------------------------------------

class Position(TypedDict, total=False):
    """Represents a single open position carried in StrategyState.

    All fields are optional (total=False) to allow partial construction,
    but in practice all fields are populated by StrategyState.open_trade().
    """
    side: str            # "long" or "short"
    entry_time: datetime
    entry_price: float
    quantity: float
    stop: float | None
    target: float | None
    reason: str          # entry reason label — written to Trade.entry_reason


# ---------------------------------------------------------------------------
# ActionDict — return type of BaseStrategy.on_bar()
#
# The backtester pattern-matches on action["action"]. Using TypedDict variants
# makes the contract explicit and prevents silent KeyError bugs at integration.
# ---------------------------------------------------------------------------

class EnterAction(TypedDict):
    """Returned when on_bar() decides to open a position."""
    action: Literal["enter_long", "enter_short"]
    quantity: float
    stop: float | None
    target: float | None
    reason: str          # written to Trade.entry_reason


class ExitAction(TypedDict):
    """Returned when on_bar() decides to close the current position."""
    action: Literal["exit"]
    reason: str          # written to Trade.exit_reason


class HoldAction(TypedDict):
    """Returned when on_bar() takes no action."""
    action: Literal["hold"]


# Union type used as the on_bar() return annotation.
# Backtester checks: action["action"] in ("enter_long", "enter_short", "exit", "hold")
ActionDict = EnterAction | ExitAction | HoldAction
