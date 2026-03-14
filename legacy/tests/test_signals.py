"""
Tests for RegimeStateTracker (Change 8) and D1 gate modes (Change 10).

No OANDA API or file I/O required.
"""

import numpy as np
import pandas as pd
import pytest

from forex_system.strategy.signals import (
    REGIME_BREAKOUT,
    REGIME_RANGING,
    REGIME_TRENDING,
    REGIME_UNDEFINED,
    RegimeStateTracker,
    _REGIME_CONFIRM_BARS,
    _TRENDING_EXIT_THRESH,
    _RANGING_EXIT_THRESH,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_raw_and_adx(raw_list: list[str], adx_list: list[float]) -> tuple[pd.Series, pd.Series]:
    """Build aligned raw-regime and ADX series from plain lists."""
    n = len(raw_list)
    assert len(adx_list) == n
    idx = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    return pd.Series(raw_list, index=idx), pd.Series(adx_list, index=idx, dtype=float)


def apply(raw_list: list[str], adx_list: list[float]) -> list[str]:
    """Run RegimeStateTracker and return confirmed regimes as a plain list."""
    raw, adx = make_raw_and_adx(raw_list, adx_list)
    return list(RegimeStateTracker().apply(raw, adx))


# ── RegimeStateTracker tests ───────────────────────────────────────────────────


def test_regime_tracker_constants():
    """Hysteresis thresholds are as specified in CHANGE 8."""
    assert _TRENDING_EXIT_THRESH == 18.0
    assert _RANGING_EXIT_THRESH  == 23.0
    assert _REGIME_CONFIRM_BARS  == 2


def test_regime_tracker_initial_state_is_undefined():
    """Without enough confirming bars the tracker starts in UNDEFINED."""
    # Single TRENDING raw bar
    confirmed = apply([REGIME_TRENDING], [26.0])
    assert confirmed[0] == REGIME_UNDEFINED


def test_regime_tracker_enters_trending_after_confirmation():
    """Two consecutive TRENDING raw bars confirm the TRENDING regime."""
    confirmed = apply(
        [REGIME_TRENDING, REGIME_TRENDING, REGIME_TRENDING],
        [26.0,            26.0,            26.0],
    )
    # Bars 0 and 1 are the pending period; bar 2 is confirmed (count hits 2)
    assert confirmed[2] == REGIME_TRENDING


def test_regime_tracker_trending_persists_in_dead_zone():
    """
    CHANGE 8: While confirmed TRENDING, the regime should not switch when ADX
    drops into the 18-25 dead zone — the exit threshold is 18, not 25.
    """
    # Start confirmed TRENDING (2 bars at ADX 27), then ADX drops to 22
    confirmed = apply(
        [REGIME_TRENDING, REGIME_TRENDING, REGIME_UNDEFINED, REGIME_UNDEFINED],
        [27.0,            27.0,            22.0,             22.0],
    )
    # After the 2 confirming bars, confirmed[2] and confirmed[3] should still be TRENDING
    # because adx_i=22 >= _TRENDING_EXIT_THRESH=18
    assert confirmed[2] == REGIME_TRENDING
    assert confirmed[3] == REGIME_TRENDING


def test_regime_tracker_trending_exits_when_adx_below_18():
    """
    CHANGE 8: Confirmed TRENDING should exit after ADX drops below 18 for
    _REGIME_CONFIRM_BARS consecutive bars.
    """
    confirmed = apply(
        # 2 bars confirm TRENDING, then 2 bars below 18 (raw=RANGING) confirm exit
        [REGIME_TRENDING, REGIME_TRENDING, REGIME_RANGING, REGIME_RANGING, REGIME_RANGING],
        [27.0,            27.0,            16.0,           16.0,           16.0],
    )
    assert confirmed[2] == REGIME_TRENDING   # still TRENDING — only 1 confirming exit bar
    assert confirmed[4] == REGIME_RANGING    # confirmed exit after 2nd bar


def test_regime_tracker_ranging_persists_below_exit_threshold():
    """
    CHANGE 8: Confirmed RANGING should not switch when ADX rises to 22 (below
    the RANGING exit threshold of 23).
    """
    confirmed = apply(
        [REGIME_RANGING, REGIME_RANGING, REGIME_UNDEFINED, REGIME_UNDEFINED],
        [17.0,           17.0,           22.0,             22.0],
    )
    # ADX=22 < _RANGING_EXIT_THRESH=23, so we stay RANGING
    assert confirmed[2] == REGIME_RANGING
    assert confirmed[3] == REGIME_RANGING


def test_regime_tracker_ranging_exits_when_adx_above_23():
    """
    CHANGE 8: Confirmed RANGING should exit after ADX rises above 23 for
    _REGIME_CONFIRM_BARS consecutive bars.
    """
    confirmed = apply(
        [REGIME_RANGING, REGIME_RANGING, REGIME_UNDEFINED, REGIME_UNDEFINED, REGIME_UNDEFINED],
        [17.0,           17.0,           24.0,             24.0,             24.0],
    )
    assert confirmed[2] == REGIME_RANGING   # exit not yet confirmed
    assert confirmed[4] == REGIME_UNDEFINED  # confirmed exit, lands in UNDEFINED


def test_regime_tracker_breakout_is_immediate():
    """
    CHANGE 8: BREAKOUT is always confirmed immediately without waiting
    for _REGIME_CONFIRM_BARS bars.
    """
    confirmed = apply(
        [REGIME_BREAKOUT],
        [21.0],
    )
    assert confirmed[0] == REGIME_BREAKOUT


def test_regime_tracker_breakout_then_trending():
    """
    CHANGE 8: After a BREAKOUT bar, if ADX stays above trending threshold for
    2 bars, the regime transitions to TRENDING.
    """
    confirmed = apply(
        [REGIME_BREAKOUT, REGIME_TRENDING, REGIME_TRENDING, REGIME_TRENDING],
        [21.0,            26.0,            26.0,            26.0],
    )
    assert confirmed[0] == REGIME_BREAKOUT
    assert confirmed[3] == REGIME_TRENDING


def test_regime_tracker_single_bar_dip_does_not_exit_trending():
    """
    CHANGE 8: A single bar with ADX below 18 should not exit TRENDING —
    confirmation requires 2 consecutive bars.
    """
    confirmed = apply(
        # Confirm TRENDING, then one bar dip below 18, then recovery
        [REGIME_TRENDING, REGIME_TRENDING, REGIME_RANGING, REGIME_TRENDING, REGIME_TRENDING],
        [27.0,            27.0,            16.0,           27.0,            27.0],
    )
    # Bar 2 starts the exit countdown (count=1), bar 3 recovery cancels it
    assert confirmed[3] == REGIME_TRENDING
    assert confirmed[4] == REGIME_TRENDING


# ── D1 gate mode tests ─────────────────────────────────────────────────────────
#
# Tests use RegimeRouter with a minimal synthetic feature DataFrame.
# Registry and pip_value are mocked so no API calls occur.


def make_feature_df_with_d1(
    n: int,
    adx_val: float = 27.0,
    vol_ratio: float = 1.0,
    signal_list: list[int] | None = None,
    d1_gate_list: list[int] | None = None,
) -> pd.DataFrame:
    """
    Create a minimal feature DataFrame compatible with RegimeRouter.route().
    All regime features are set to a constant TRENDING environment by default.
    """
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    rng   = np.random.default_rng(42)
    close = pd.Series(1.1 * np.cumprod(1 + rng.normal(0, 0.0003, n)), index=dates)
    df = pd.DataFrame(
        {
            "open":           (close * 0.9999).values,
            "high":           (close * 1.001).values,
            "low":            (close * 0.999).values,
            "close":          close.values,
            "volume":         1000,
            "adx_14":         adx_val,
            "vol_ratio_10_60": vol_ratio,
            "atr_14":         0.0010,      # small ATR so structural stop is plausible
        },
        index=dates,
    )
    if d1_gate_list is not None:
        df["trend_regime_50d"] = d1_gate_list
    return df


def _patch_registry_and_pip(monkeypatch) -> None:
    from unittest.mock import MagicMock
    from forex_system.data import instruments as inst_mod
    from forex_system.risk import sizing as sz_mod

    mock_meta = MagicMock()
    mock_meta.pip_size = 0.0001
    mock_meta.pip_location = -4
    mock_meta.min_trade_size = 1
    monkeypatch.setattr(inst_mod.registry, "get", lambda _: mock_meta)
    monkeypatch.setattr(
        sz_mod, "pip_value_per_unit_usd", lambda inst, price, acct="USD": 0.0001
    )


def test_d1_gate_disabled_passes_all_signals(monkeypatch):
    """
    CHANGE 10: With d1_gate_mode='disabled', the D1 column is ignored
    and all TRENDING/BREAKOUT signals pass through.
    """
    _patch_registry_and_pip(monkeypatch)
    from forex_system.strategy.signals import RegimeRouter

    n = 100
    # D1 gate is always -1 (would veto long signals under "full" mode)
    df = make_feature_df_with_d1(n, adx_val=27.0, vol_ratio=1.0,
                                  d1_gate_list=[-1] * n)

    router_disabled = RegimeRouter(d1_gate_mode="disabled",   use_hysteresis=False)
    router_full     = RegimeRouter(d1_gate_mode="full",        use_hysteresis=False)

    sig_disabled = router_disabled.route(df, instrument="EUR_USD", pip_size=0.0001)
    sig_full     = router_full.route(df, instrument="EUR_USD", pip_size=0.0001)

    n_disabled = int((sig_disabled["signal"] != 0).sum())
    n_full     = int((sig_full["signal"] != 0).sum())

    # "disabled" should let more (or equal) signals through than "full"
    assert n_disabled >= n_full


def test_d1_gate_full_kills_ongoing_long(monkeypatch):
    """
    CHANGE 10 / original v2.4: With d1_gate_mode='full', a long signal on a bar
    where d1_gate=-1 should be zeroed out — even on continuation bars.
    """
    _patch_registry_and_pip(monkeypatch)
    from forex_system.strategy.signals import RegimeRouter

    n = 100
    # D1 switches to -1 at bar 50, meaning full mode kills signals from bar 50 onward
    d1 = [1] * 50 + [-1] * 50
    df = make_feature_df_with_d1(n, adx_val=27.0, vol_ratio=1.0, d1_gate_list=d1)

    router = RegimeRouter(d1_gate_mode="full", use_hysteresis=False)
    sig_df = router.route(df, instrument="EUR_USD", pip_size=0.0001)

    # Any long signal in the second half should be zeroed
    second_half_longs = (sig_df["signal"].iloc[50:] == 1).sum()
    assert second_half_longs == 0, (
        "'full' mode should veto all longs in bars where d1_gate=-1"
    )


def test_d1_gate_entry_only_does_not_kill_ongoing_signal(monkeypatch):
    """
    CHANGE 10: With d1_gate_mode='entry_only', D1 is only checked at the first bar
    of a signal.  Continuation bars (where signal stays the same direction) are NOT
    affected even if D1 flips.
    """
    _patch_registry_and_pip(monkeypatch)
    from forex_system.strategy.signals import RegimeRouter

    n = 100
    # D1 starts as +1 (long allowed), flips to -1 at bar 50 (should NOT kill open long)
    d1 = [1] * 50 + [-1] * 50
    df = make_feature_df_with_d1(n, adx_val=27.0, vol_ratio=1.0, d1_gate_list=d1)

    router_entry_only = RegimeRouter(d1_gate_mode="entry_only", use_hysteresis=False)
    router_full       = RegimeRouter(d1_gate_mode="full",        use_hysteresis=False)

    sig_entry_only = router_entry_only.route(df, instrument="EUR_USD", pip_size=0.0001)
    sig_full       = router_full.route(df, instrument="EUR_USD", pip_size=0.0001)

    # entry_only should allow more signals to survive in the second half
    n_second_entry = int((sig_entry_only["signal"].iloc[50:] != 0).sum())
    n_second_full  = int((sig_full["signal"].iloc[50:] != 0).sum())

    assert n_second_entry >= n_second_full, (
        "'entry_only' should pass at least as many continuation signals as 'full'"
    )


def test_d1_gate_entry_only_blocks_new_entry_that_contradicts_d1(monkeypatch):
    """
    CHANGE 10: 'entry_only' still blocks new entries (signal transitions) that
    contradict the D1 gate direction.
    """
    _patch_registry_and_pip(monkeypatch)
    from forex_system.strategy.signals import RegimeRouter

    n = 100
    # D1 is -1 throughout (downtrend) — new long entries should be blocked
    d1 = [-1] * n
    df = make_feature_df_with_d1(n, adx_val=27.0, vol_ratio=1.0, d1_gate_list=d1)

    router_entry_only = RegimeRouter(d1_gate_mode="entry_only", use_hysteresis=False)
    router_disabled   = RegimeRouter(d1_gate_mode="disabled",   use_hysteresis=False)

    sig_entry_only = router_entry_only.route(df, instrument="EUR_USD", pip_size=0.0001)
    sig_disabled   = router_disabled.route(df, instrument="EUR_USD", pip_size=0.0001)

    # entry_only should produce fewer long signals than disabled when D1=-1
    n_long_entry_only = int((sig_entry_only["signal"] == 1).sum())
    n_long_disabled   = int((sig_disabled["signal"] == 1).sum())

    assert n_long_entry_only <= n_long_disabled, (
        "'entry_only' should block new long entries when d1_gate=-1"
    )


def test_d1_gate_invalid_mode_raises():
    """
    CHANGE 10: Passing an invalid d1_gate_mode to RegimeRouter should raise ValueError.
    """
    with pytest.raises(ValueError, match="d1_gate_mode"):
        from forex_system.strategy.signals import RegimeRouter
        RegimeRouter(d1_gate_mode="unknown_mode")
