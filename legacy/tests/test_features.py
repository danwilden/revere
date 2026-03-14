"""
Tests for feature pipeline.

These tests use synthetic OHLCV data and do not require
OANDA API access or any data files.
"""

import numpy as np
import pandas as pd
import pytest

from forex_system.features.builders import FEATURE_VERSION, FeaturePipeline
from forex_system.features.transforms import (
    atr,
    atr_zscore,
    binary_direction,
    carry_differential,
    ema_spread,
    forward_return,
    log_returns,
    realized_volatility,
    return_autocorr,
    rsi,
    session_overlap,
    POLICY_RATES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_ohlcv(n: int = 300, start: str = "2023-01-01", freq: str = "H") -> pd.DataFrame:
    """Create synthetic OHLCV DataFrame for testing."""
    rng = np.random.default_rng(42)
    dates = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = pd.Series(
        1.1 * np.cumprod(1 + rng.normal(0, 0.001, n)), index=dates
    )
    return pd.DataFrame(
        {
            "open": (close * 0.9999).values,
            "high": (close * 1.0015).values,
            "low": (close * 0.9985).values,
            "close": close.values,
            "volume": rng.integers(200, 1000, n),
            "complete": True,
        },
        index=dates,
    )


# ── Transform tests ────────────────────────────────────────────────────────────


def test_log_returns_shape():
    df = make_ohlcv(100)
    lr = log_returns(df["close"], 1)
    assert len(lr) == len(df)
    assert lr.name == "log_ret_1"
    assert pd.isna(lr.iloc[0])  # first value should be NaN


def test_log_returns_periods():
    df = make_ohlcv(100)
    lr4 = log_returns(df["close"], 4)
    assert lr4.iloc[:4].isna().all()
    assert lr4.name == "log_ret_4"


def test_realized_volatility_nonnegative():
    df = make_ohlcv(100)
    rvol = realized_volatility(df["close"], 20)
    non_nan = rvol.dropna()
    assert (non_nan >= 0).all()


def test_ema_spread_zero_on_same_period():
    """When fast == slow period, spread should always be 0."""
    df = make_ohlcv(100)
    spread = ema_spread(df["close"], fast=12, slow=12)
    assert spread.abs().max() < 1e-10


def test_rsi_bounds():
    df = make_ohlcv(200)
    rsi_val = rsi(df["close"], 14).dropna()
    assert (rsi_val >= 0).all()
    assert (rsi_val <= 100).all()


def test_atr_nonnegative():
    df = make_ohlcv(200)
    atr_val = atr(df["high"], df["low"], df["close"], 14).dropna()
    assert (atr_val >= 0).all()


def test_forward_return_look_ahead():
    """fwd_ret must be NaN for the last `horizon` rows — no look-ahead."""
    df = make_ohlcv(100)
    horizon = 3
    fwd = forward_return(df["close"], horizon)
    # Last `horizon` rows cannot have forward returns
    assert fwd.iloc[-horizon:].isna().all()
    # Earlier rows should mostly be non-NaN
    assert fwd.iloc[:-horizon].notna().sum() > 0


def test_binary_direction_values():
    df = make_ohlcv(100)
    fwd = forward_return(df["close"], 1)
    labels = binary_direction(fwd)
    non_nan = labels.dropna()
    assert set(non_nan.unique()).issubset({0, 1})


# ── Pipeline tests ─────────────────────────────────────────────────────────────


def test_feature_pipeline_all_columns_present():
    df = make_ohlcv(300)
    pipe = FeaturePipeline(horizon=1)
    out = pipe.build(df)

    expected_cols = FeaturePipeline.ML_FEATURE_COLS + ["fwd_ret", "label_direction"]
    for col in expected_cols:
        assert col in out.columns, f"Missing column: {col}"


def test_feature_pipeline_excludes_labels_when_requested():
    df = make_ohlcv(300)
    pipe = FeaturePipeline(horizon=1)
    out = pipe.build(df, include_labels=False)
    assert "fwd_ret" not in out.columns
    assert "label_direction" not in out.columns


def test_feature_pipeline_no_look_ahead():
    """
    The label column fwd_ret must be NaN for the last `horizon` rows.
    This verifies that forward_return uses shift(-horizon) correctly.
    """
    df = make_ohlcv(200)
    pipe = FeaturePipeline(horizon=1)
    out = pipe.build(df)
    assert pd.isna(out["fwd_ret"].iloc[-1])


def test_feature_pipeline_index_preserved():
    df = make_ohlcv(200)
    pipe = FeaturePipeline(horizon=1)
    out = pipe.build(df)
    assert out.index.equals(df.index)


def test_feature_hash_stable():
    pipe = FeaturePipeline(horizon=1)
    h1 = pipe.feature_hash()
    h2 = pipe.feature_hash()
    assert h1 == h2
    assert len(h1) == 8


def test_feature_hash_changes_with_horizon():
    h1 = FeaturePipeline(horizon=1).feature_hash()
    h4 = FeaturePipeline(horizon=4).feature_hash()
    assert h1 != h4


def test_feature_version_constant():
    assert isinstance(FEATURE_VERSION, str)
    assert FEATURE_VERSION.startswith("v")


def test_pipeline_filters_incomplete_bars():
    """Bars with complete=False should be excluded from feature computation."""
    df = make_ohlcv(100)
    df.loc[df.index[-5:], "complete"] = False
    pipe = FeaturePipeline(horizon=1)
    out = pipe.build(df, filter_incomplete=True)
    assert len(out) == 95


def test_pipeline_with_small_dataset():
    """Pipeline should handle small datasets without crashing."""
    df = make_ohlcv(300)  # enough bars for MAX_LOOKBACK=252
    pipe = FeaturePipeline(horizon=1)
    out = pipe.build(df)
    assert len(out) == 300


# ── New v2.1 transform tests ────────────────────────────────────────────────


def test_session_overlap_h4_bars():
    """H4 bars at 12:00 and 16:00 UTC should be flagged as London/NY overlap."""
    # Create an index with representative H4 bar start hours
    times = pd.DatetimeIndex(
        [
            "2024-01-08 00:00:00+00:00",  # 00:00 UTC → no overlap
            "2024-01-08 04:00:00+00:00",  # 04:00 UTC → no overlap
            "2024-01-08 08:00:00+00:00",  # 08:00 UTC → no overlap
            "2024-01-08 12:00:00+00:00",  # 12:00 UTC → overlap (spans 13:00)
            "2024-01-08 16:00:00+00:00",  # 16:00 UTC → overlap (tail end)
            "2024-01-08 20:00:00+00:00",  # 20:00 UTC → no overlap
        ]
    )
    result = session_overlap(times)
    assert result.name == "ny_overlap"
    assert len(result) == len(times)
    # 00:00, 04:00, 08:00 → 0; 12:00, 16:00 → 1; 20:00 → 0
    assert result.iloc[0] == 0.0
    assert result.iloc[1] == 0.0
    assert result.iloc[2] == 0.0
    assert result.iloc[3] == 1.0
    assert result.iloc[4] == 1.0
    assert result.iloc[5] == 0.0


def test_return_autocorr_shape_and_nans():
    """return_autocorr should match input length; first window+lag rows are NaN."""
    df = make_ohlcv(100)
    lag, window = 1, 20
    result = return_autocorr(df["close"], lag=lag, window=window)
    assert len(result) == len(df)
    assert result.name == f"ret_autocorr_{lag}_{window}"
    # Pearson rolling corr: NaN for first window rows (window includes lag shift)
    assert result.iloc[:window].isna().all()
    # Values beyond warm-up should be in [-1, 1]
    valid = result.iloc[window:].dropna()
    assert (valid.abs() <= 1.0 + 1e-9).all()


def test_atr_zscore_shape_and_nans():
    """atr_zscore should match input length; first ~atr_window rows are NaN."""
    df = make_ohlcv(400)
    from forex_system.features.transforms import atr as _atr
    atr_window = 14
    atr_series = _atr(df["high"], df["low"], df["close"], atr_window)
    window = 252
    result = atr_zscore(atr_series, window=window)
    assert len(result) == len(df)
    assert result.name == f"atr_zscore_{window}"
    # ATR has atr_window-1 leading NaN; atr_zscore inherits those
    assert result.iloc[:atr_window].isna().all()
    # pandas rolling uses min_periods=1, so values appear well before `window` bars
    valid = result.dropna()
    assert len(valid) > 0
    # All valid values should be finite
    assert np.isfinite(valid.values).all()


def test_carry_differential_known_pairs():
    """carry_diff should correctly compute base_rate - quote_rate."""
    # EUR_USD long: earn EUR (3.0), pay USD (4.5) → -1.5
    assert abs(carry_differential("EUR_USD") - (POLICY_RATES["EUR"] - POLICY_RATES["USD"])) < 1e-9
    # USD_JPY long: earn USD (4.5), pay JPY (0.25) → +4.25
    assert abs(carry_differential("USD_JPY") - (POLICY_RATES["USD"] - POLICY_RATES["JPY"])) < 1e-9
    # Unknown instrument → 0.0
    assert carry_differential("UNKNOWN") == 0.0
    assert carry_differential("") == 0.0
