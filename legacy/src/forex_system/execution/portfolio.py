"""
Portfolio manager: combines broker, exposure guard, and drawdown throttler.

PortfolioManager is the single entry point for order placement in both
notebooks/07_paper_trading_validation.ipynb and the Streamlit order staging page.

It enforces all risk checks before execution:
    1. DrawdownThrottler: adjusts risk multiplier based on current DD
    2. ExposureGuard: checks gross notional and margin usage limits
    3. Max concurrent positions check

Usage:
    from forex_system.execution.portfolio import PortfolioManager

    pm = PortfolioManager()
    result = pm.check_and_place(order_request, new_notional=10_000)
    if result is None:
        print("Order blocked by risk guard")
"""

import pandas as pd
from loguru import logger

from forex_system.config import settings
from forex_system.execution.broker import OandaBroker
from forex_system.execution.orders import OrderRequest
from forex_system.risk.leverage import DrawdownThrottler, ExposureGuard


# CHANGE 6 (v2.4): USD_JPY and USD_CHF have correlation ~0.7-0.8 (both are
# USD-strength pairs). Allowing simultaneous same-direction positions doubles
# correlated drawdown. This guard halves that exposure.
_CORRELATED_PAIRS: list[tuple[str, str]] = [("USD_JPY", "USD_CHF")]


def correlation_guard(
    open_positions: dict[str, int],
    new_instrument: str,
    new_direction: int,
) -> tuple[bool, str]:
    """
    CHANGE 6 (v2.4): Block same-direction simultaneous exposure on correlated pairs.

    USD_JPY and USD_CHF are both USD-strength proxies (correlation ~0.7-0.8).
    Entering both long (or both short) at the same time doubles the effective
    USD directional bet. This guard rejects the second entry.

    Opposite directions (e.g., long USD_JPY + short USD_CHF) are allowed —
    they form a quasi pairs trade and partially offset USD exposure.

    Args:
        open_positions:  {instrument: direction (1=long, -1=short)} for live positions.
        new_instrument:  Instrument being considered for new entry.
        new_direction:   Proposed direction (1=long, -1=short).

    Returns:
        (allowed: bool, reason: str)
        reason is "ok" when allowed, or "CORRELATION_GUARD: ..." when blocked.

    Example:
        open_positions = {"USD_JPY": 1}   # currently long JPY
        allowed, msg = correlation_guard(open_positions, "USD_CHF", 1)
        # → (False, "CORRELATION_GUARD: USD_JPY already long (+1)")
        allowed, msg = correlation_guard(open_positions, "USD_CHF", -1)
        # → (True, "ok")  — opposite direction, allowed
    """
    if new_direction == 0:
        return True, "ok"   # flat signal, nothing to check

    for pair_a, pair_b in _CORRELATED_PAIRS:
        if new_instrument == pair_a:
            partner = pair_b
        elif new_instrument == pair_b:
            partner = pair_a
        else:
            continue

        if partner in open_positions and open_positions[partner] == new_direction:
            direction_str = "long" if new_direction == 1 else "short"
            reason = (
                f"CORRELATION_GUARD: {partner} already {direction_str} "
                f"({new_direction:+d}); same-direction USD bet rejected"
            )
            return False, reason

    return True, "ok"


class PortfolioManager:
    """
    Manages the lifecycle of a multi-instrument portfolio.

    Tracks equity history for drawdown monitoring.
    All order placements flow through check_and_place().
    """

    def __init__(
        self,
        broker: OandaBroker | None = None,
        exposure_guard: ExposureGuard | None = None,
        dd_throttler: DrawdownThrottler | None = None,
    ) -> None:
        self.broker = broker or OandaBroker()
        self.exposure_guard = exposure_guard or ExposureGuard()
        self.dd_throttler = dd_throttler or DrawdownThrottler()
        self._equity_history: list[float] = []

    # ── State ─────────────────────────────────────────────────────────────────

    def refresh_state(self) -> dict:
        """
        Fetch current account state and append NAV to equity history.

        Returns:
            Account summary dict (balance, nav, margin_used, etc.)
        """
        summary = self.broker.get_account_summary()
        self._equity_history.append(summary["nav"])
        return summary

    def risk_multiplier(self) -> float:
        """Current drawdown-based risk multiplier (0.25 to 1.0)."""
        if not self._equity_history:
            return 1.0
        return self.dd_throttler.get_multiplier(
            pd.Series(self._equity_history)
        )

    def max_positions(self) -> int:
        """Max concurrent positions given current drawdown throttle."""
        return self.dd_throttler.get_max_positions(self.risk_multiplier())

    # ── Order placement ───────────────────────────────────────────────────────

    def check_and_place(
        self,
        order_request: OrderRequest,
        new_notional: float,
    ) -> dict | None:
        """
        Run all pre-trade risk checks, then place the order if safe.

        Args:
            order_request: The order to place.
            new_notional:  Approximate notional value of the new position
                           (units × current price). Used for exposure check.

        Returns:
            OANDA fill response dict, or None if any risk check blocked the trade.
        """
        state = self.refresh_state()
        equity = state["nav"]
        margin_used = state["margin_used"]
        margin_available = state["margin_available"]

        # Current open positions notional (approximate: sum of abs units as proxy)
        open_positions = self.broker.get_open_positions()
        current_notional = sum(abs(p["net_units"]) for p in open_positions)

        # Max concurrent positions check
        if len(open_positions) >= self.max_positions():
            msg = (
                f"Max positions reached: "
                f"{len(open_positions)} >= {self.max_positions()} "
                f"(multiplier={self.risk_multiplier():.2f})"
            )
            logger.warning(f"Order blocked: {msg}")
            return None

        # CHANGE 6 (v2.4): Correlation guard — block same-direction USD_JPY/USD_CHF
        live_directions: dict[str, int] = {
            p["instrument"]: (1 if p["net_units"] > 0 else -1)
            for p in open_positions
            if p["net_units"] != 0
        }
        new_direction = 1 if order_request.units > 0 else -1
        corr_allowed, corr_reason = correlation_guard(
            live_directions, order_request.instrument, new_direction
        )
        if not corr_allowed:
            logger.warning(f"Order blocked: {corr_reason}")
            return None

        # Approximate margin required (50:1 max leverage → 2% margin)
        estimated_margin = new_notional * 0.02

        allowed, reason = self.exposure_guard.check(
            equity=equity,
            current_notional=current_notional,
            new_notional=new_notional,
            margin_used=margin_used,
            margin_available=margin_available,
            new_margin_required=estimated_margin,
        )

        if not allowed:
            logger.warning(f"Order blocked by ExposureGuard: {reason}")
            return None

        return self.broker.place_order(order_request)

    def close_all(self) -> list[dict]:
        """Close all open positions. Use with caution."""
        positions = self.broker.get_open_positions()
        results = []
        for pos in positions:
            if pos["net_units"] != 0:
                resp = self.broker.close_position(pos["instrument"])
                results.append(resp)
        return results
