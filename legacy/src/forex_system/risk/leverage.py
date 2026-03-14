"""
Drawdown monitoring, exposure guards, and dynamic risk throttling.

DrawdownThrottler:  monitors peak-to-trough DD and returns a risk multiplier
ExposureGuard:      checks notional and margin limits before order placement
"""

import pandas as pd

from forex_system.config import settings
from loguru import logger


class DrawdownThrottler:
    """
    Maps current peak-to-trough drawdown onto a risk multiplier.

    Throttle tiers (default from settings: 3%, 5%, 8%):
        DD < 3%  → multiplier = 1.00  (full risk)
        DD < 5%  → multiplier = 0.75
        DD < 8%  → multiplier = 0.50
        DD ≥ 8%  → multiplier = 0.25  (emergency)

    Usage:
        throttler = DrawdownThrottler()
        m = throttler.get_multiplier(equity_series)
        sized_risk = base_risk * m
    """

    MULTIPLIERS: list[float] = [1.0, 0.75, 0.50, 0.25]

    def __init__(self, levels: list[float] | None = None) -> None:
        self.levels = levels or list(settings.drawdown_throttle_levels)

    def get_multiplier(self, equity_curve: pd.Series) -> float:
        """
        Compute current drawdown and return the appropriate multiplier.

        Args:
            equity_curve: Time-ordered Series of equity / NAV values.

        Returns:
            float in (0, 1] — multiply base risk_pct by this value.
        """
        if len(equity_curve) == 0:
            return 1.0

        peak = equity_curve.expanding().max()
        current_dd = abs(float((equity_curve / peak - 1).iloc[-1]))

        for i, level in enumerate(self.levels):
            if current_dd < level:
                m = self.MULTIPLIERS[i]
                if m < 1.0:
                    logger.warning(
                        f"DrawdownThrottler: DD={current_dd:.2%} → multiplier={m}"
                    )
                return m

        # Past all levels → worst tier
        logger.warning(
            f"DrawdownThrottler: DD={current_dd:.2%} → emergency multiplier=0.25"
        )
        return self.MULTIPLIERS[-1]

    def get_max_positions(self, risk_multiplier: float) -> int:
        """
        Scale max concurrent positions with risk multiplier.
        At full risk: settings.max_concurrent_positions.
        At emergency: 1.
        """
        base = settings.max_concurrent_positions
        return max(1, round(base * risk_multiplier))


class ExposureGuard:
    """
    Pre-trade check against gross notional and margin usage limits.

    Called by PortfolioManager before every order placement.

    Usage:
        guard = ExposureGuard()
        allowed, reason = guard.check(
            equity=10_000,
            current_notional=20_000,
            new_notional=5_000,
            margin_used=400,
            margin_available=9_600,
            new_margin_required=100,
        )
        if not allowed:
            logger.warning(f"Trade blocked: {reason}")
    """

    def __init__(
        self,
        max_gross_multiple: float | None = None,
        max_margin_pct: float | None = None,
    ) -> None:
        self.max_gross_multiple = (
            max_gross_multiple or settings.max_gross_exposure_multiple
        )
        self.max_margin_pct = max_margin_pct or settings.max_margin_usage_pct

    def check(
        self,
        equity: float,
        current_notional: float,
        new_notional: float,
        margin_used: float,
        margin_available: float,
        new_margin_required: float,
    ) -> tuple[bool, str]:
        """
        Returns (allowed, reason).
        allowed=True  → trade passes all guards.
        allowed=False → reason describes which guard was breached.
        """
        # Gross exposure cap
        post_notional = current_notional + new_notional
        exposure_cap = equity * self.max_gross_multiple
        if post_notional > exposure_cap:
            return (
                False,
                f"Gross exposure cap: post={post_notional:,.0f} > cap={exposure_cap:,.0f} "
                f"({self.max_gross_multiple}× equity)",
            )

        # Margin usage cap
        total_margin_after = margin_used + new_margin_required
        total_margin_available = margin_used + margin_available
        if total_margin_available > 0:
            pct_after = total_margin_after / total_margin_available
            if pct_after > self.max_margin_pct:
                return (
                    False,
                    f"Margin cap: post={pct_after:.1%} > cap={self.max_margin_pct:.1%}",
                )

        return True, "OK"
