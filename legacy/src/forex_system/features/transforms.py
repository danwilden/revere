"""
Pure feature transform functions.

All functions:
- Accept pandas Series/DatetimeIndex inputs
- Return a named pandas Series
- Have no side effects and no internal state
- Use the `ta` library for indicator math

These are the building blocks called by builders.FeaturePipeline.
"""

import numpy as np
import pandas as pd
import ta


# ── Returns ───────────────────────────────────────────────────────────────

def log_returns(close: pd.Series, periods: int = 1) -> pd.Series:
    """Log returns over `periods` bars."""
    return np.log(close / close.shift(periods)).rename(f"log_ret_{periods}")


def realized_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling annualized volatility from log returns (√252 for daily equiv)."""
    lr = np.log(close / close.shift(1))
    return (lr.rolling(window).std() * np.sqrt(252)).rename(f"rvol_{window}")


# ── Trend ──────────────────────────────────────────────────────────────────

def ema_spread(close: pd.Series, fast: int = 12, slow: int = 26) -> pd.Series:
    """Normalized EMA spread: (EMA_fast - EMA_slow) / close."""
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    return ((fast_ema - slow_ema) / close).rename(f"ema_spread_{fast}_{slow}")


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> pd.Series:
    """Average Directional Index — trend strength (0-100)."""
    indicator = ta.trend.ADXIndicator(high, low, close, window=window)
    return indicator.adx().rename(f"adx_{window}")


def macd_signal(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.Series:
    """MACD histogram (MACD line minus Signal line)."""
    ind = ta.trend.MACD(
        close, window_fast=fast, window_slow=slow, window_sign=signal
    )
    return ind.macd_diff().rename("macd_hist")


# ── Volatility ─────────────────────────────────────────────────────────────

def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> pd.Series:
    """Average True Range in price units."""
    ind = ta.volatility.AverageTrueRange(high, low, close, window=window)
    return ind.average_true_range().rename(f"atr_{window}")


def atr_pct(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> pd.Series:
    """ATR as fraction of close — normalized across pairs with different prices."""
    raw = atr(high, low, close, window)
    return (raw / close).rename(f"atr_pct_{window}")


def bb_width(close: pd.Series, window: int = 20, std: float = 2.0) -> pd.Series:
    """Bollinger Band width (normalized by middle band)."""
    ind = ta.volatility.BollingerBands(close, window=window, window_dev=std)
    return ind.bollinger_wband().rename(f"bb_width_{window}")


def bb_position(close: pd.Series, window: int = 20, std: float = 2.0) -> pd.Series:
    """
    Position of price within Bollinger Bands.
    0 = at lower band, 1 = at upper band, 0.5 = at middle.
    """
    ind = ta.volatility.BollingerBands(close, window=window, window_dev=std)
    return ind.bollinger_pband().rename(f"bb_pos_{window}")


# ── Momentum ──────────────────────────────────────────────────────────────

def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index (0–100)."""
    return ta.momentum.RSIIndicator(close, window=window).rsi().rename(
        f"rsi_{window}"
    )


def roc(close: pd.Series, window: int = 12) -> pd.Series:
    """Rate of Change (% return over `window` bars)."""
    return ta.momentum.ROCIndicator(close, window=window).roc().rename(
        f"roc_{window}"
    )


# ── Calendar ──────────────────────────────────────────────────────────────

def day_of_week(index: pd.DatetimeIndex) -> pd.Series:
    """Day of week: 0=Monday ... 4=Friday."""
    return pd.Series(index.dayofweek, index=index, name="day_of_week")


def hour_of_day(index: pd.DatetimeIndex) -> pd.Series:
    """Hour of day in UTC (0–23). More informative on H1 than H4/D."""
    return pd.Series(index.hour, index=index, name="hour_of_day")


def session_overlap(
    index: pd.DatetimeIndex,
    open_utc: int = 13,
    close_utc: int = 17,
) -> pd.Series:
    """
    London/NY session overlap indicator.

    1 if the bar's start hour falls within [open_utc-4, close_utc) — the -4
    offset catches H4 bars whose window spans the overlap start (e.g. a bar
    opening at 12:00 UTC runs 12:00–16:00 and contains the 13:00 overlap peak).

    Default window [13,17) UTC = London/NY overlap.
    For H4: bars at 12:00 and 16:00 UTC receive 1; all others receive 0.
    """
    low = open_utc - 4
    return pd.Series(
        ((index.hour >= low) & (index.hour < close_utc)).astype(float),
        index=index,
        name="ny_overlap",
    )


def london_open(index: pd.DatetimeIndex) -> pd.Series:
    """
    London open session indicator for H4 bars.

    1 for the H4 bar starting at 08:00 UTC, which covers the London open
    (08:00–12:00 UTC) — the highest-liquidity period of the European session.
    All other bars receive 0.
    """
    return pd.Series(
        (index.hour == 8).astype(float),
        index=index,
        name="london_open",
    )


def ny_close(index: pd.DatetimeIndex) -> pd.Series:
    """
    New York close session indicator for H4 bars.

    1 for the H4 bar starting at 16:00 UTC, which spans the NY close
    (16:00–20:00 UTC, covering ~17:00 ET). This bar captures the final
    institutional activity and position squaring of the NY session.
    All other bars receive 0.
    """
    return pd.Series(
        (index.hour == 16).astype(float),
        index=index,
        name="ny_close",
    )


# ── Microstructure ─────────────────────────────────────────────────────────

def return_autocorr(
    close: pd.Series, lag: int = 1, window: int = 20
) -> pd.Series:
    """
    Rolling Pearson autocorrelation of log returns at a given lag.

    Positive = recent momentum (returns predict themselves).
    Negative = mean-reversion regime.
    Window of `window` bars, shifted by `lag`.
    NaN for the first window + lag bars.
    """
    lr = np.log(close / close.shift(1))
    return lr.rolling(window).corr(lr.shift(lag)).rename(
        f"ret_autocorr_{lag}_{window}"
    )


def vol_ratio(close: pd.Series, short_window: int = 10, long_window: int = 60) -> pd.Series:
    """
    Ratio of short-window realized vol to long-window realized vol.

    Captures vol compression/expansion:
        ratio < 0.7  → vol suppressed relative to recent history (potential breakout)
        ratio > 1.5  → vol expanding (trend/momentum mode)
        ratio ≈ 1.0  → neutral

    The √252 annualization cancels in the ratio, so this is purely
    std(short) / std(long). NaN for the first `long_window` rows.
    """
    lr = np.log(close / close.shift(1))
    std_short = lr.rolling(short_window).std()
    std_long = lr.rolling(long_window).std()
    return (std_short / std_long.replace(0, np.nan)).rename(
        f"vol_ratio_{short_window}_{long_window}"
    )


def atr_zscore(atr_series: pd.Series, window: int = 252) -> pd.Series:
    """
    ATR z-score: (ATR - rolling_mean) / rolling_std over `window` bars.

    Normalises volatility level relative to its own history — comparable
    across different volatility epochs and across pairs.
    Positive = currently elevated vol; negative = suppressed vol.

    NaN behaviour: uses pandas default min_periods=1, so values are produced
    as soon as 2+ non-NaN ATR observations exist in the window.  Leading NaN
    rows come from the upstream ATR computation (window-1 NaN rows) plus one
    extra row where std is undefined with a single observation.
    """
    m = atr_series.rolling(window).mean()
    s = atr_series.rolling(window).std()
    return ((atr_series - m) / s.replace(0, np.nan)).rename(
        f"atr_zscore_{window}"
    )


# ── Carry ──────────────────────────────────────────────────────────────────

# Approximate central bank policy rates (annualised %, Feb 2026).
# Update when major central banks change rates significantly.
POLICY_RATES: dict[str, float] = {
    "USD": 4.50,   # Fed
    "EUR": 3.00,   # ECB
    "GBP": 4.75,   # BoE
    "JPY": 0.25,   # BoJ
    "CHF": 0.25,   # SNB
    "AUD": 4.35,   # RBA
    "NZD": 5.25,   # RBNZ
    "CAD": 3.00,   # BoC
}


def carry_differential(instrument: str) -> float:
    """
    Static carry differential for a long position: base_rate - quote_rate (%).

    A long EUR_USD earns EUR rates and pays USD rates → 3.00 - 4.50 = -1.50.
    A long USD_JPY earns USD rates and pays JPY rates → 4.50 - 0.25 = +4.25.

    NOTE: This is a constant per instrument — single-pair LightGBM models
    receive zero information gain from a constant column.  Its value is
    diagnostic (confirms carry sign) and it becomes useful in cross-pair
    meta-models where the carry rank differentiates instruments.

    Returns 0.0 for unknown currencies.
    """
    parts = instrument.split("_")
    if len(parts) != 2:
        return 0.0
    base, quote = parts
    return POLICY_RATES.get(base, 0.0) - POLICY_RATES.get(quote, 0.0)


# ── Regime ─────────────────────────────────────────────────────────────────

def vol_regime(atr_series: pd.Series, lookback: int = 60) -> pd.Series:
    """
    Volatility regime: is current ATR high or low vs its rolling mean?

    Returns:
         1 = high volatility (ATR > 1.25 × rolling mean)
         0 = normal volatility
        -1 = low volatility  (ATR < 0.75 × rolling mean)
    NaN for first `lookback` rows.
    """
    rolling_mean = atr_series.rolling(lookback).mean()
    regime = pd.Series(0.0, index=atr_series.index, name=f"vol_regime_{lookback}")
    regime[atr_series > rolling_mean * 1.25] = 1.0
    regime[atr_series < rolling_mean * 0.75] = -1.0
    regime[rolling_mean.isna()] = np.nan
    return regime


def trend_regime(
    daily_close: pd.Series,
    h4_index: pd.DatetimeIndex,
    sma_window: int = 50,
) -> pd.Series:
    """
    Daily trend regime aligned to an H4 DatetimeIndex.

    Computes SMA(sma_window) on daily closes, derives +1/-1 regime, then
    forward-fills onto the H4 bar timestamps. No look-ahead: a daily bar
    is only visible to H4 bars that open after it.

    Returns:
         1 = bullish (D1 close > SMA)
        -1 = bearish (D1 close < SMA)
    NaN for first sma_window trading days.
    """
    sma = daily_close.rolling(sma_window).mean()
    daily_regime = pd.Series(
        np.where(daily_close > sma, 1.0, -1.0),
        index=daily_close.index,
        name=f"trend_regime_{sma_window}d",
        dtype=float,
    )
    daily_regime[sma.isna()] = np.nan
    return daily_regime.reindex(h4_index, method="ffill")


def trend_strength_ratio(adx_series: pd.Series, window: int = 20) -> pd.Series:
    """
    ADX relative to its own rolling mean — normalizes trend strength across regimes.
    Returns adx / rolling_mean(adx, window). NaN where mean is zero or NaN.
    """
    rolling_mean = adx_series.rolling(window).mean()
    ratio = adx_series / rolling_mean.replace(0, np.nan)
    return ratio.rename(f"adx_ratio_{window}")


# ── Labels ─────────────────────────────────────────────────────────────────

def forward_return(close: pd.Series, horizon: int = 1) -> pd.Series:
    """
    Log return `horizon` bars ahead. This is the prediction target.

    IMPORTANT: shift(-horizon) introduces look-ahead. These columns must
    only exist in training data — never in live inference inputs.
    The FeaturePipeline handles this via include_labels=False at inference time.
    """
    return np.log(close.shift(-horizon) / close).rename(f"fwd_ret_{horizon}")


def binary_direction(fwd_ret: pd.Series) -> pd.Series:
    """
    Binary classification label.
    1 = price went up over the horizon, 0 = price went down or flat.
    NaN where forward return is unknown (e.g. last row) to avoid look-ahead.
    """
    out = (fwd_ret > 0).astype(float)
    out[fwd_ret.isna()] = np.nan
    return out.rename("label_direction")


def ternary_label(fwd_ret: pd.Series, threshold: float) -> pd.Series:
    """
    Three-class label for threshold-based trading:
     1 = long  (return > +threshold)
    -1 = short (return < -threshold)
     0 = flat  (|return| <= threshold)
    NaN where forward return is unknown (e.g. last row).

    threshold is in return units (e.g. 0.0003 ≈ 3 pips on EUR_USD H1).
    """
    labels = pd.Series(np.nan, index=fwd_ret.index, dtype=float, name="label_ternary")
    labels[fwd_ret.notna()] = 0
    labels[fwd_ret > threshold] = 1
    labels[fwd_ret < -threshold] = -1
    return labels


def triple_barrier_label(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    atr_series: pd.Series,
    pt_multiplier: float = 2.0,
    sl_multiplier: float = 1.0,
    max_holding: int = 20,
) -> pd.Series:
    """
    Triple-barrier label (Lopez de Prado, Advances in Financial ML, ch.3).

    For each bar t:
      upper barrier = close[t] + pt_multiplier * atr[t]  (profit target)
      lower barrier = close[t] - sl_multiplier * atr[t]  (stop loss)

      Look forward up to max_holding bars:
        - If high[t+k] >= upper → label = 1.0  (profit target hit)
        - If low[t+k]  <= lower → label = 0.0  (stop hit)
        - If neither within max_holding → label = NaN (timeout, exclude from training)

    IMPORTANT: This is a look-ahead label. Always use include_labels=False at inference.

    Returns:
        pd.Series with values 1.0, 0.0, or NaN. Named "label_triple_barrier".
        Last max_holding rows are always NaN.
    """
    n = len(close)
    labels = pd.Series(np.nan, index=close.index, name="label_triple_barrier")

    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()
    atr_arr = atr_series.to_numpy()

    for i in range(n - 1):
        if np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        upper = close_arr[i] + pt_multiplier * atr_arr[i]
        lower = close_arr[i] - sl_multiplier * atr_arr[i]
        end = min(i + max_holding + 1, n)
        label = np.nan  # timeout → NaN → excluded from training

        for k in range(i + 1, end):
            if high_arr[k] >= upper:
                label = 1.0
                break
            if low_arr[k] <= lower:
                label = 0.0
                break
        labels.iloc[i] = label

    return labels


def label_forward_5(
    close: pd.Series,
    horizon: int = 5,
) -> pd.Series:
    """
    CHANGE 4 (v2.4): Simple forward-return sign label with no barrier bias.

    Returns +1 if close[t+horizon] > close[t], -1 if less, 0 if exactly equal.
    Last `horizon` rows are NaN (look-ahead).

    Rationale: triple_barrier with PT=2×ATR, SL=1×ATR produces 65% SL labels —
    structural bias that causes ML to learn to predict losses. This unbiased
    label directly measures whether the direction was correct over a fixed horizon,
    with no asymmetric barrier. Use as primary ML training target for IC testing.

    IMPORTANT: Look-ahead label. Always use include_labels=False at inference.
    """
    fwd_return = close.shift(-horizon) / close - 1.0
    labels = np.sign(fwd_return)
    labels = pd.Series(labels, index=close.index, name="label_forward_5")
    labels.iloc[-horizon:] = np.nan
    return labels


def label_barrier_symmetric(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    atr_series: pd.Series,
    max_holding: int = 20,
    pt_mult: float = 1.5,
    sl_mult: float = 1.5,
) -> pd.Series:
    """
    CHANGE 4 (v2.4): Symmetric triple-barrier — PT = SL = 1.5×ATR.

    Fixes v2.3 label bias (PT=2×ATR, SL=1×ATR → 65% SL outcomes). Equal
    barriers produce ~45-55% TP/SL balance, close to theoretical 50/50 for
    a random walk, so any ML lift above that reflects genuine predictive power.

    Returns: +1 (TP hit), 0 (SL hit), NaN (timeout or invalid ATR).
    Last `max_holding` rows are always NaN.

    IMPORTANT: Look-ahead label. Always use include_labels=False at inference.
    """
    n = len(close)
    labels = pd.Series(np.nan, index=close.index, name="label_barrier_sym")

    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()
    atr_arr = atr_series.to_numpy()

    for i in range(n - 1):
        if np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        upper = close_arr[i] + pt_mult * atr_arr[i]
        lower = close_arr[i] - sl_mult * atr_arr[i]
        end = min(i + max_holding + 1, n)

        label = np.nan  # timeout → NaN → excluded from training
        for k in range(i + 1, end):
            if high_arr[k] >= upper:
                label = 1.0
                break
            if low_arr[k] <= lower:
                label = 0.0
                break
        labels.iloc[i] = label

    return labels


def expected_value_label(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    atr_series: pd.Series,
    pt_multiplier: float = 2.0,
    sl_multiplier: float = 1.0,
    max_holding: int = 20,
) -> pd.Series:
    """
    Continuous EV label: realized ATR-normalized return clipped to barriers.

    For each bar t:
        upper barrier = close[t] + pt_multiplier × atr[t]
        lower barrier = close[t] - sl_multiplier × atr[t]

        Look forward up to max_holding bars:
          - Upper hit first → label = +pt_multiplier  (e.g. +2.0)
          - Lower hit first → label = -sl_multiplier  (e.g. -1.0)
          - Timeout → label = clipped((close[t+max_holding] - close[t]) / atr[t])

    Returns values in approximately [-sl_multiplier, +pt_multiplier] = [-1, +2].
    NaN where ATR is invalid. Last max_holding rows are always NaN.

    Use with LGBMRegressor/XGBRegressor as the training target (not classifier).
    A positive prediction → rule signal is expected to profit; negative → avoid.

    IMPORTANT: This is a look-ahead label. Always use include_labels=False at inference.
    """
    n = len(close)
    labels = pd.Series(np.nan, index=close.index, name="label_ev")

    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()
    atr_arr = atr_series.to_numpy()

    for i in range(n - 1):
        if np.isnan(atr_arr[i]) or atr_arr[i] <= 0:
            continue
        upper = close_arr[i] + pt_multiplier * atr_arr[i]
        lower = close_arr[i] - sl_multiplier * atr_arr[i]
        end = min(i + max_holding + 1, n)

        label = np.nan
        for k in range(i + 1, end):
            if high_arr[k] >= upper:
                label = float(pt_multiplier)
                break
            if low_arr[k] <= lower:
                label = float(-sl_multiplier)
                break
        else:
            # Timeout: use clipped actual return normalized by ATR
            if end > i + 1:
                raw_ret = (close_arr[end - 1] - close_arr[i]) / atr_arr[i]
                label = float(np.clip(raw_ret, -sl_multiplier, pt_multiplier))

        labels.iloc[i] = label

    return labels
