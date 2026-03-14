"""
Tests for risk sizing, leverage, and stop calculation modules.

No OANDA API required — instrument registry is mocked throughout.
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from forex_system.risk.leverage import DrawdownThrottler, ExposureGuard
from forex_system.risk.stops import (
    atr_stop_distance,
    compute_structural_stop,
    dynamic_stop,
    fixed_pip_stop,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_ohlcv(n: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    close = pd.Series(1.1 * np.cumprod(1 + rng.normal(0, 0.001, n)), index=dates)
    return pd.DataFrame(
        {
            "high": (close * 1.001).values,
            "low": (close * 0.999).values,
            "close": close.values,
        },
        index=dates,
    )


def make_mock_meta(pip_location: int = -4, min_trade_size: int = 1):
    meta = MagicMock()
    meta.pip_location = pip_location
    meta.pip_size = 10.0 ** pip_location
    meta.min_trade_size = min_trade_size
    return meta


# ── Stop calculation tests ─────────────────────────────────────────────────────


def test_atr_stop_distance_nonnegative():
    df = make_ohlcv(100)
    stop = atr_stop_distance(df["high"], df["low"], df["close"], 14, 2.0).dropna()
    assert (stop >= 0).all()


def test_fixed_pip_stop():
    df = make_ohlcv(50)
    stop = fixed_pip_stop(df["close"], pips=10, pip_size=0.0001)
    assert np.allclose(stop, 0.001)


def test_dynamic_stop_floor():
    """dynamic_stop should never be below min_pips × pip_size."""
    df = make_ohlcv(100)
    min_pips = 10.0
    pip_size = 0.0001
    stop = dynamic_stop(
        df["high"], df["low"], df["close"],
        atr_window=14, atr_multiplier=2.0,
        min_pips=min_pips, pip_size=pip_size
    ).dropna()
    assert (stop >= min_pips * pip_size - 1e-10).all()


def test_dynamic_stop_jpy_pip_size():
    """JPY pairs use pip_size=0.01."""
    df = make_ohlcv(100)
    stop = dynamic_stop(
        df["high"], df["low"], df["close"],
        min_pips=10, pip_size=0.01
    ).dropna()
    assert (stop >= 0.1 - 1e-9).all()


# ── DrawdownThrottler tests ────────────────────────────────────────────────────


def test_throttler_no_drawdown():
    throttler = DrawdownThrottler(levels=[0.03, 0.05, 0.08])
    eq = pd.Series([10000, 10100, 10200, 10300])
    assert throttler.get_multiplier(eq) == 1.0


def test_throttler_small_drawdown():
    # 2% drawdown — below first level (3%)
    throttler = DrawdownThrottler(levels=[0.03, 0.05, 0.08])
    eq = pd.Series([10000, 10200, 9996])
    assert throttler.get_multiplier(eq) == 1.0


def test_throttler_medium_drawdown():
    # ~4% drawdown (between 3% and 5% level)
    throttler = DrawdownThrottler(levels=[0.03, 0.05, 0.08])
    eq = pd.Series([10000, 10200, 9792])  # 9792/10200 - 1 ≈ -4%
    m = throttler.get_multiplier(eq)
    assert m == 0.75


def test_throttler_severe_drawdown():
    # ~10% drawdown (above 8% level) → emergency tier
    throttler = DrawdownThrottler(levels=[0.03, 0.05, 0.08])
    eq = pd.Series([10000, 10200, 9180])  # 9180/10200 - 1 ≈ -10%
    m = throttler.get_multiplier(eq)
    assert m == 0.25


def test_throttler_empty_series():
    throttler = DrawdownThrottler()
    assert throttler.get_multiplier(pd.Series([])) == 1.0


def test_throttler_max_positions():
    throttler = DrawdownThrottler(levels=[0.03, 0.05, 0.08])
    # At full risk → default max_concurrent_positions
    from forex_system.config import settings
    assert throttler.get_max_positions(1.0) == settings.max_concurrent_positions
    # At emergency → 1
    assert throttler.get_max_positions(0.25) >= 1


# ── ExposureGuard tests ────────────────────────────────────────────────────────


def test_exposure_guard_passes_normal():
    guard = ExposureGuard(max_gross_multiple=4.0, max_margin_pct=0.25)
    allowed, reason = guard.check(
        equity=10_000,
        current_notional=10_000,
        new_notional=5_000,
        margin_used=200,
        margin_available=9_800,
        new_margin_required=100,
    )
    assert allowed
    assert reason == "OK"


def test_exposure_guard_blocks_notional_breach():
    guard = ExposureGuard(max_gross_multiple=3.0, max_margin_pct=0.25)
    allowed, reason = guard.check(
        equity=10_000,
        current_notional=25_000,
        new_notional=10_000,   # 35k > 3× 10k = 30k → blocked
        margin_used=500,
        margin_available=9_500,
        new_margin_required=200,
    )
    assert not allowed
    assert "Gross exposure" in reason


def test_exposure_guard_blocks_margin_breach():
    guard = ExposureGuard(max_gross_multiple=10.0, max_margin_pct=0.20)
    # Already at 19% margin, new trade pushes to >20%
    allowed, reason = guard.check(
        equity=10_000,
        current_notional=5_000,
        new_notional=1_000,
        margin_used=1_900,   # 19% of 10_000
        margin_available=8_100,
        new_margin_required=300,   # pushes to 2200 / 10000 = 22%
    )
    assert not allowed
    assert "Margin" in reason


# ── Sizing tests ───────────────────────────────────────────────────────────────


def test_calculate_units_eur_usd():
    """
    EUR_USD (quote=USD): pip_value = pip_size = 0.0001
    units = (10_000 × 0.005) / (10 × 0.0001) = 50 / 0.001 = 50_000
    """
    from forex_system.risk.sizing import calculate_units

    with patch("forex_system.risk.sizing.instrument_registry") as mock_reg:
        mock_reg.get.return_value = make_mock_meta(pip_location=-4)

        units = calculate_units(
            equity=10_000,
            risk_pct=0.005,
            stop_distance=0.001,   # 10 pips
            instrument="EUR_USD",
            current_price=1.085,
        )
        assert units == 50_000


def test_calculate_units_usd_jpy():
    """
    USD_JPY (base=USD): pip_value = pip_size / price = 0.01 / 150.0 ≈ 0.0000667
    risk = 10_000 × 0.005 = 50
    stop_pips = 0.1 / 0.01 = 10
    units = 50 / (10 × 0.0000667) ≈ 75_000
    """
    from forex_system.risk.sizing import calculate_units

    with patch("forex_system.risk.sizing.instrument_registry") as mock_reg:
        mock_reg.get.return_value = make_mock_meta(pip_location=-2)

        units = calculate_units(
            equity=10_000,
            risk_pct=0.005,
            stop_distance=0.1,   # 10 pips for JPY
            instrument="USD_JPY",
            current_price=150.0,
        )
        # pip_value = 0.01 / 150 ≈ 6.667e-5
        # units = 50 / (10 × 6.667e-5) ≈ 74_998
        assert units > 70_000


def test_calculate_units_zero_on_invalid():
    from forex_system.risk.sizing import calculate_units

    with patch("forex_system.risk.sizing.instrument_registry") as mock_reg:
        mock_reg.get.return_value = make_mock_meta()

        assert calculate_units(0, 0.005, 0.001, "EUR_USD", 1.0) == 0
        assert calculate_units(10_000, 0.005, 0, "EUR_USD", 1.0) == 0


def test_calculate_units_risk_multiplier():
    """Risk multiplier should proportionally reduce position size."""
    from forex_system.risk.sizing import calculate_units

    with patch("forex_system.risk.sizing.instrument_registry") as mock_reg:
        mock_reg.get.return_value = make_mock_meta(pip_location=-4)

        units_full = calculate_units(
            10_000, 0.005, 0.001, "EUR_USD", 1.085, risk_multiplier=1.0
        )
        units_half = calculate_units(
            10_000, 0.005, 0.001, "EUR_USD", 1.085, risk_multiplier=0.5
        )
        assert units_half == pytest.approx(units_full * 0.5, rel=0.01)


# ── CHANGE 9: compute_structural_stop max_mult tests ─────────────────────────


def make_ohlcv_wide_swing(n: int = 60) -> pd.DataFrame:
    """OHLCV with a big spike on bar 30 — structural stop will be > 4×ATR."""
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    close = pd.Series([1.1000] * n, index=dates)
    high  = close.copy() * 1.001
    low   = close.copy() * 0.999
    # Inject a large spike so rolling min of low is far below close
    low.iloc[20] = 0.95  # ~0.15 drop — structural stop will be enormous
    return pd.DataFrame({"high": high, "low": low, "close": close}, index=dates)


def test_compute_structural_stop_uncapped_by_default():
    """
    CHANGE 9: Default max_mult is now 100.0 (effectively uncapped).
    A very wide swing should produce a stop distance well above 4×ATR.
    """
    df = make_ohlcv_wide_swing(60)
    direction = pd.Series(1, index=df.index)   # all long
    stop = compute_structural_stop(df["high"], df["low"], df["close"], direction)
    stop_clean = stop.dropna()
    from forex_system.features.transforms import atr
    atr_vals = atr(df["high"], df["low"], df["close"], 14).reindex(stop_clean.index)

    # At the bars immediately after the spike, structural distance > 4×ATR
    ratio = (stop_clean / atr_vals).dropna()
    assert (ratio > 4.0).any(), (
        "With default max_mult=100.0, structural stop can exceed 4×ATR on wide swings"
    )


def test_compute_structural_stop_explicit_max_mult_still_caps():
    """
    CHANGE 9 backward compatibility: passing max_mult=4.0 explicitly still caps the stop.
    """
    df = make_ohlcv_wide_swing(60)
    direction = pd.Series(1, index=df.index)
    stop = compute_structural_stop(
        df["high"], df["low"], df["close"], direction, max_mult=4.0
    )
    stop_clean = stop.dropna()
    from forex_system.features.transforms import atr
    atr_vals = atr(df["high"], df["low"], df["close"], 14).reindex(stop_clean.index)
    ratio = (stop_clean / atr_vals).dropna()
    assert (ratio <= 4.0 + 1e-9).all(), (
        "Explicit max_mult=4.0 should still clamp stop at 4×ATR"
    )
