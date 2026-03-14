"""
Position sizing module.

Core formula (fixed risk):
    units = (equity × risk_pct × risk_multiplier) / (stop_pips × pip_value_per_unit)

Fractional Kelly formula:
    b = pt_size / sl_size  (payoff ratio, e.g. 2.0 for 2:1 barrier)
    f = (p × b − q) / b   (full Kelly fraction; p = win_prob, q = 1 − p)
    f_fractional = f × kelly_fraction  (quarter-Kelly default = 0.25)
    units = (equity × max(f_fractional, 0)) / (sl_pips × pip_value_per_unit)

pip_value_per_unit_usd handles the three cases for the 7 major pairs:
  - USD-quote (EUR_USD, GBP_USD, AUD_USD, NZD_USD): pip_value = pip_size (already USD)
  - USD-base  (USD_JPY, USD_CHF, USD_CAD):            pip_value = pip_size / current_price
  - Cross pairs (EUR_GBP etc.): approximation via current_price (warn user)

Usage:
    from forex_system.risk.sizing import calculate_units, kelly_units

    # Fixed-risk sizing
    units = calculate_units(
        equity=10_000,
        risk_pct=0.005,        # 0.5%
        stop_distance=0.001,   # 10 pips on EUR_USD
        instrument="EUR_USD",
        current_price=1.085,
    )
    # → 50_000 units (risking $50 on a 10-pip, $0.0001/unit stop)

    # Kelly sizing
    units = kelly_units(
        equity=10_000,
        win_prob=0.60,         # model-estimated win probability
        pt_distance=0.002,     # 2× ATR profit target
        sl_distance=0.001,     # 1× ATR stop loss
        instrument="EUR_USD",
        current_price=1.085,
    )
"""

import numpy as np
import pandas as pd
from loguru import logger

from forex_system.config import settings
from forex_system.data.instruments import registry as instrument_registry


def pip_value_per_unit_usd(
    instrument: str,
    current_price: float,
    account_ccy: str = "USD",
) -> float:
    """
    Value in account currency (USD) of one pip for one unit of position.

    For a precise production system, the quote→USD conversion should use
    live mid prices. For Phase 1 research, the approximation below is
    sufficient for the 7 major pairs.

    Args:
        instrument: e.g. "EUR_USD"
        current_price: current mid price of the instrument
        account_ccy: account denomination (default "USD")

    Returns:
        Pip value in USD per unit. e.g. for EUR_USD: 0.0001
    """
    meta = instrument_registry.get(instrument)
    pip_size = meta.pip_size  # 10^pip_location

    parts = instrument.split("_")
    if len(parts) != 2:
        logger.warning(f"Unexpected instrument format: {instrument}")
        return pip_size

    base_ccy, quote_ccy = parts

    if quote_ccy == account_ccy:
        # EUR_USD, GBP_USD, AUD_USD, NZD_USD
        # 1 unit = 1 base unit; pip already in USD
        return pip_size

    elif base_ccy == account_ccy:
        # USD_JPY, USD_CHF, USD_CAD
        # 1 unit = 1 USD; pip in quote currency → convert to USD via price
        if current_price <= 0:
            return pip_size
        return pip_size / current_price

    else:
        # Cross pairs: EUR_GBP, EUR_JPY, GBP_JPY, etc.
        # Approximation — replace with live quote_ccy/USD rate for production
        logger.warning(
            f"Cross-pair pip value approximation for {instrument}. "
            "For production, add live quote_ccy/USD conversion."
        )
        return pip_size / current_price if current_price > 0 else pip_size


def calculate_units(
    equity: float,
    risk_pct: float,
    stop_distance: float,
    instrument: str,
    current_price: float,
    risk_multiplier: float = 1.0,
    account_ccy: str = "USD",
) -> int:
    """
    Compute position size in units (OANDA uses units, not lots).

    Args:
        equity:          Account equity in account_ccy.
        risk_pct:        Fraction of equity to risk (e.g. 0.005 = 0.5%).
        stop_distance:   Distance from entry to stop in price units.
        instrument:      e.g. "EUR_USD"
        current_price:   Current mid price (for pip value calculation).
        risk_multiplier: From DrawdownThrottler (0.25–1.0). Default 1.0.
        account_ccy:     Account base currency. Default "USD".

    Returns:
        Integer units ≥ min_trade_size, or 0 on invalid inputs.
        Direction (long/short) is determined by the caller via sign.
    """
    if stop_distance <= 0 or equity <= 0 or current_price <= 0:
        logger.warning(
            f"Invalid sizing inputs: equity={equity}, "
            f"stop={stop_distance}, price={current_price}"
        )
        return 0

    effective_risk_usd = equity * risk_pct * risk_multiplier
    pip_val = pip_value_per_unit_usd(instrument, current_price, account_ccy)

    if pip_val <= 0:
        return 0

    meta = instrument_registry.get(instrument)
    stop_pips = stop_distance / meta.pip_size

    units = effective_risk_usd / (stop_pips * pip_val)
    units = int(np.floor(units))
    units = max(units, int(meta.min_trade_size))

    logger.debug(
        f"Sizing {instrument}: equity={equity:.0f} risk%={risk_pct:.3%} "
        f"stop_pips={stop_pips:.1f} pip_val={pip_val:.6f} "
        f"multiplier={risk_multiplier:.2f} → {units} units"
    )
    return units


def size_signal(
    signal_df: pd.DataFrame,
    instrument: str,
    equity: float,
    risk_pct: float | None = None,
    risk_multiplier: float = 1.0,
    account_ccy: str = "USD",
) -> pd.DataFrame:
    """
    Vectorized sizing for backtesting.

    Expects signal_df to have columns: signal (int), stop_distance (float), close (float).
    Adds column: units (int, always positive — direction encoded by signal).

    Args:
        signal_df:       DataFrame from strategy.generate() or SignalAggregator.
        instrument:      e.g. "EUR_USD"
        equity:          Constant equity value for vectorized sizing.
        risk_pct:        Override default_risk_pct from settings if provided.
        risk_multiplier: From DrawdownThrottler.
        account_ccy:     Account currency.

    Returns:
        Copy of signal_df with "units" column added.
    """
    rp = risk_pct or settings.default_risk_pct
    meta = instrument_registry.get(instrument)
    pip_size = meta.pip_size
    min_size = int(meta.min_trade_size)

    result = signal_df.copy()
    result["units"] = 0

    active_mask = result["signal"] != 0
    if not active_mask.any():
        return result

    close_arr = result.loc[active_mask, "close"].to_numpy(dtype=float)
    stop_arr = result.loc[active_mask, "stop_distance"].to_numpy(dtype=float)

    # Compute pip value per unit vectorized — no per-row function calls
    parts = instrument.split("_")
    base_ccy = parts[0] if len(parts) == 2 else ""
    quote_ccy = parts[1] if len(parts) == 2 else ""

    if quote_ccy == account_ccy:
        # USD-quote pairs (EUR_USD, GBP_USD, AUD_USD, NZD_USD): pip already in USD
        pip_val = np.full(len(close_arr), pip_size)
    elif base_ccy == account_ccy:
        # USD-base pairs (USD_JPY, USD_CHF, USD_CAD): convert via price
        pip_val = np.where(close_arr > 0, pip_size / close_arr, pip_size)
    else:
        logger.warning(
            f"Cross-pair pip value approximation for {instrument}. "
            "For production, add live quote_ccy/USD conversion."
        )
        pip_val = np.where(close_arr > 0, pip_size / close_arr, pip_size)

    stop_pips = stop_arr / pip_size
    effective_risk = equity * rp * risk_multiplier

    valid = (stop_pips > 0) & (pip_val > 0)
    denom = np.where(valid, stop_pips * pip_val, 1.0)  # avoid div-by-zero
    raw_units = np.where(valid, effective_risk / denom, 0.0)
    units_arr = np.where(
        valid,
        np.maximum(np.floor(raw_units).astype(int), min_size),
        0,
    ).astype(int)

    result.loc[active_mask, "units"] = units_arr
    return result


def kelly_units(
    equity: float,
    win_prob: float,
    pt_distance: float,
    sl_distance: float,
    instrument: str,
    current_price: float,
    kelly_fraction: float = 0.25,
    risk_multiplier: float = 1.0,
    account_ccy: str = "USD",
) -> int:
    """
    Fractional Kelly position sizing.

    The full Kelly criterion: f = (p×b − q) / b
        p = win_prob
        q = 1 − win_prob
        b = pt_distance / sl_distance  (payoff ratio, e.g. 2.0 for 2:1 barrier)

    Uses quarter-Kelly by default (kelly_fraction=0.25) to account for model
    estimation error and non-stationarity. Equivalent to risking 25% of the
    theoretically optimal fraction.

    Args:
        equity:          Account equity in account_ccy.
        win_prob:        Estimated win probability from calibrated ML model (0–1).
        pt_distance:     Profit target distance from entry in price units.
        sl_distance:     Stop loss distance from entry in price units.
        instrument:      e.g. "EUR_USD"
        current_price:   Current mid price.
        kelly_fraction:  Fraction of full Kelly to use (default 0.25 = quarter-Kelly).
        risk_multiplier: From DrawdownThrottler (0.25–1.0). Default 1.0.
        account_ccy:     Account base currency. Default "USD".

    Returns:
        Integer units ≥ 0. Returns 0 if Kelly fraction is negative (negative edge).
    """
    if sl_distance <= 0 or pt_distance <= 0 or equity <= 0 or current_price <= 0:
        logger.warning(
            f"Invalid Kelly inputs: equity={equity}, pt={pt_distance}, sl={sl_distance}"
        )
        return 0

    if not (0.0 < win_prob < 1.0):
        logger.warning(f"win_prob out of range: {win_prob:.4f} — defaulting to fixed risk")
        return 0

    b = pt_distance / sl_distance
    q = 1.0 - win_prob
    full_kelly = (win_prob * b - q) / b
    fractional_kelly = full_kelly * kelly_fraction * risk_multiplier

    if fractional_kelly <= 0:
        logger.debug(
            f"Negative Kelly fraction {fractional_kelly:.4f} for p={win_prob:.3f}, "
            f"b={b:.2f} — no position"
        )
        return 0

    effective_risk_usd = equity * fractional_kelly
    pip_val = pip_value_per_unit_usd(instrument, current_price, account_ccy)

    if pip_val <= 0:
        return 0

    meta = instrument_registry.get(instrument)
    sl_pips = sl_distance / meta.pip_size

    units = effective_risk_usd / (sl_pips * pip_val)
    units = int(np.floor(units))
    units = max(units, 0)  # Kelly can return 0 (no edge); don't enforce min_trade_size

    logger.debug(
        f"Kelly sizing {instrument}: equity={equity:.0f} win_prob={win_prob:.3f} "
        f"b={b:.2f} full_kelly={full_kelly:.4f} fraction={fractional_kelly:.4f} "
        f"sl_pips={sl_pips:.1f} → {units} units"
    )
    return units


# Correlated pair groups for exposure management
_CORR_GROUPS: list[frozenset[str]] = [
    frozenset({"EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"}),  # USD-quote majors (high correlation)
    frozenset({"USD_JPY", "USD_CHF"}),                         # funding currencies
]


def correlation_multiplier(
    active_signals: dict[str, int],
    instrument: str,
    corr_threshold: int = 2,
    reduction: float = 0.60,
) -> float:
    """
    Reduce position size when correlated pairs are signaling simultaneously.

    Detects whether `instrument` belongs to a known correlation group and
    whether ≥ corr_threshold other pairs in that group also have active signals
    in the same direction. If so, returns `reduction` (e.g. 0.60) as the
    size multiplier; otherwise returns 1.0.

    Args:
        active_signals:  Dict of {instrument: signal} for currently active positions.
                         signal values: 1 (long), -1 (short), 0 (flat).
        instrument:      The instrument being sized (must also be in active_signals).
        corr_threshold:  Minimum number of co-directional correlated pairs to trigger
                         reduction. Default 2.
        reduction:       Multiplier applied when correlation cap is breached (default 0.60).

    Returns:
        1.0 (no reduction) or `reduction` (correlated exposure cap triggered).

    Example:
        EUR_USD=long, GBP_USD=long → 2 co-directional USD-quote pairs → return 0.60
        EUR_USD=long, GBP_USD=short → different directions → return 1.0
    """
    if instrument not in active_signals:
        return 1.0

    own_direction = active_signals[instrument]
    if own_direction == 0:
        return 1.0

    for group in _CORR_GROUPS:
        if instrument not in group:
            continue
        # Count co-directional pairs in the same group (excluding self)
        co_directional = sum(
            1
            for pair, sig in active_signals.items()
            if pair != instrument and pair in group and sig == own_direction
        )
        if co_directional >= corr_threshold:
            logger.debug(
                f"Correlation cap: {instrument} + {co_directional} co-directional "
                f"pairs in group → multiplier={reduction}"
            )
            return reduction

    return 1.0
