"""
Stop-loss distance calculation functions.

Used by both strategy/rules.py (to set initial stops) and
risk/sizing.py (stop distance feeds the position sizing formula).
"""

import pandas as pd

from forex_system.features.transforms import atr


def compute_structural_stop(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    direction: pd.Series,
    lookback: int = 10,
    atr_window: int = 14,
    buffer_mult: float = 0.5,
    min_mult: float = 1.5,
    max_mult: float = 4.0,
) -> pd.Series:
    """
    CHANGE 2 (v2.4): Structural stop based on recent swing highs/lows.

    Replaces ATR(14)×2.0 which was inside H4 noise and caused whipsaw.
    The stop is anchored to a structural price level (swing low/high), with
    an ATR buffer to account for noise around that level.

    Long:  anchor = lowest low of prior `lookback` bars − buffer_mult×ATR
    Short: anchor = highest high of prior `lookback` bars + buffer_mult×ATR
    Distance = |close − anchor|, clamped to [min_mult×ATR, max_mult×ATR].

    The clamp prevents:
        - min_mult: stop too close to entry (inside spread noise)
        - max_mult: runaway risk on very wide swings (default 100.0 = effectively
          uncapped; callers apply their own business-rule cap via RegimeRouter)

    Args:
        high, low, close:  OHLCV price series (same index).
        direction:         Signal series (1=long, -1=short, 0=flat).
                           Flat bars receive a safe default of min_mult×ATR.
        lookback:          Number of prior bars to look back for swing extremes.
        atr_window:        ATR calculation period (default 14).
        buffer_mult:       ATR multiple added as a buffer beyond the swing level.
        min_mult:          Minimum stop as ATR multiple (prevents noise-stop).
        max_mult:          Maximum stop as ATR multiple (caps risk per trade).

    Returns:
        pd.Series of stop distances in price units (always positive).
    """
    raw_atr = atr(high, low, close, atr_window)

    # Swing levels from prior bars (shift(1) ensures no look-ahead on current bar)
    swing_low  = low.shift(1).rolling(lookback).min()
    swing_high = high.shift(1).rolling(lookback).max()

    # Structural distance for each direction
    long_dist  = (close - swing_low)  + buffer_mult * raw_atr
    short_dist = (swing_high - close) + buffer_mult * raw_atr

    # Select by direction; flat bars use minimum ATR multiple as safe default
    dist = pd.Series(min_mult * raw_atr, index=close.index)
    dist = dist.where(direction == 0, other=pd.Series(
        long_dist.where(direction == 1, short_dist), index=close.index
    ))

    # Clamp to [min_mult×ATR, max_mult×ATR]
    dist = dist.clip(lower=min_mult * raw_atr, upper=max_mult * raw_atr)

    return dist.rename("stop_distance")


def atr_stop_distance(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_window: int = 14,
    atr_multiplier: float = 2.0,
) -> pd.Series:
    """
    ATR-based stop distance in price units.
    stop_distance = ATR(window) × multiplier

    The stop is placed this distance from the entry price:
    - Long:  stop = entry - stop_distance
    - Short: stop = entry + stop_distance
    """
    raw_atr = atr(high, low, close, atr_window)
    return (raw_atr * atr_multiplier).rename("stop_distance")


def fixed_pip_stop(
    close: pd.Series, pips: float, pip_size: float = 0.0001
) -> pd.Series:
    """Fixed pip stop expressed in price units. Used as a minimum floor."""
    stop_val = pips * pip_size
    return pd.Series(stop_val, index=close.index, name="stop_distance_fixed")


def dynamic_stop(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_window: int = 14,
    atr_multiplier: float = 2.0,
    min_pips: float = 10.0,
    pip_size: float = 0.0001,
) -> pd.Series:
    """
    ATR-based stop with a minimum pip floor.

    This is the primary stop function used across strategies.
    The pip_size default (0.0001) is appropriate for 4-decimal pairs like
    EUR_USD. For JPY pairs (pip_size=0.01) pass explicitly.
    """
    atr_based = atr_stop_distance(high, low, close, atr_window, atr_multiplier)
    floor = min_pips * pip_size
    return atr_based.clip(lower=floor).rename("stop_distance")
