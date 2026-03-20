"""Strategy state management — tracks open positions and cooldown periods."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.strategies.types import Position


@dataclass
class StrategyState:
    """Mutable state for a single strategy instance during evaluation or backtesting.

    Tracks the current open position, last exit time for cooldown enforcement,
    and a bar counter. Intended to be threaded through on_bar calls.

    IMPORTANT — call reset() between backtest runs or parameter sweeps.
    Stale last_exit_time or open_position will corrupt subsequent runs.
    cooldown_hours is a configuration parameter and is NOT reset by reset().
    """

    open_position: Position | None = None
    last_exit_time: datetime | None = None
    cooldown_hours: float = 0.0
    current_regime: str = ""
    bar_count: int = 0
    entry_bar_idx: int = -1  # bar index at trade entry; -1 when flat

    # ------------------------------------------------------------------
    # Position convenience properties
    # ------------------------------------------------------------------

    @property
    def is_flat(self) -> bool:
        return self.open_position is None

    @property
    def is_long(self) -> bool:
        return self.open_position is not None and self.open_position.get("side") == "long"

    @property
    def is_short(self) -> bool:
        return self.open_position is not None and self.open_position.get("side") == "short"

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def in_cooldown(self, current_time: datetime) -> bool:
        """Return True if we are still within the cooldown window after the last trade."""
        if self.cooldown_hours <= 0 or self.last_exit_time is None:
            return False
        elapsed_seconds = (current_time - self.last_exit_time).total_seconds()
        return elapsed_seconds < self.cooldown_hours * 3600

    def hours_since_exit(self, current_time: datetime) -> float | None:
        """Return hours elapsed since the last exit, or None if no exit yet."""
        if self.last_exit_time is None:
            return None
        return (current_time - self.last_exit_time).total_seconds() / 3600

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all mutable state to initial values.

        Must be called between backtest runs and parameter sweeps to prevent
        stale open_position, last_exit_time, or bar_count from carrying over.
        cooldown_hours is a configuration parameter and is intentionally preserved.
        """
        self.open_position = None
        self.last_exit_time = None
        self.current_regime = ""
        self.bar_count = 0
        self.entry_bar_idx = -1

    def open_trade(
        self,
        side: str,
        entry_time: datetime,
        entry_price: float,
        quantity: float,
        stop: float | None = None,
        target: float | None = None,
        reason: str = "",
        bar_idx: int = -1,
    ) -> None:
        """Record that a new position has been opened.

        Parameters
        ----------
        reason:
            Human-readable label for why this trade was entered. Written to
            Trade.entry_reason in the backtest trade log.
        bar_idx:
            The bar loop index at entry time. Used to compute bars_in_trade
            during the backtest. Defaults to -1 (no tracking) for callers
            outside the engine.
        """
        self.open_position = {
            "side": side,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "quantity": quantity,
            "stop": stop,
            "target": target,
            "reason": reason,
        }
        self.entry_bar_idx = bar_idx

    def close_trade(self, exit_time: datetime) -> Position:
        """Record that the open position has been closed and start the cooldown timer.

        Returns the closed position dict.
        Raises ValueError if there is no open position.
        """
        if self.open_position is None:
            raise ValueError("No open position to close")
        closed = dict(self.open_position)
        self.open_position = None
        self.last_exit_time = exit_time
        self.entry_bar_idx = -1
        return closed
