"""
Tests for the multi-timeframe backtest engine (Phase 1E).

All tests use synthetic data — no OANDA API or file I/O required.
Mocking pattern mirrors test_backtest.py (monkeypatch registry + pip_value).
"""

import numpy as np
import pandas as pd
import pytest

from forex_system.backtest.mtf_engine import (
    H1TradeManager,
    ExitEvent,
    MultiTimeframeBacktester,
    MINIMUM_HOLD_BARS,
)
from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import BacktestResult


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_registry_and_pip(monkeypatch, pip_size: float = 0.0001):
    """Set up registry + pip_value mocks (same pattern as test_backtest.py)."""
    from unittest.mock import MagicMock
    from forex_system.data import instruments as inst_mod
    from forex_system.risk import sizing as sz_mod

    mock_meta = MagicMock()
    mock_meta.pip_size = pip_size
    mock_meta.pip_location = -4
    mock_meta.min_trade_size = 1
    monkeypatch.setattr(inst_mod.registry, "get", lambda _: mock_meta)
    monkeypatch.setattr(
        sz_mod, "pip_value_per_unit_usd", lambda inst, price, acct="USD": pip_size
    )


def make_h4_signal_df(
    n: int,
    signals: list[int] | None = None,
    base_price: float = 1.1000,
    atr: float = 0.0010,
    stop_distance: float = 0.0020,
    units: int = 1_000,
    freq_h: int = 4,
) -> pd.DataFrame:
    """
    Build a synthetic H4 signal DataFrame.

    If signals is None, defaults to constant long (1) throughout.
    """
    dates = pd.date_range("2023-01-01", periods=n, freq=f"{freq_h}h", tz="UTC")
    if signals is None:
        sig = [1] * n
    else:
        sig = list(signals)
        if len(sig) < n:
            sig += [0] * (n - len(sig))

    prices = [base_price + atr * 0.05 * i for i in range(n)]
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p + atr * 0.2 for p in prices],
            "low": [p - atr * 0.2 for p in prices],
            "close": prices,
            "signal": sig,
            "stop_distance": stop_distance,
            "atr": atr,
            "units": units,
        },
        index=dates,
    )


def make_h1_df(
    h4_df: pd.DataFrame,
    price_offset_per_h1: float = 0.0,
    force_low: float | None = None,
    force_high: float | None = None,
) -> pd.DataFrame:
    """
    Build H1 OHLCV aligned to h4_df timestamps.

    For each H4 bar at time T, creates 4 H1 bars at T, T+1h, T+2h, T+3h.
    Prices follow h4_df["close"] for the parent H4 bar.
    force_low / force_high override all H1 lows or highs (used to test stop hits).
    """
    rows = []
    for h4_ts, h4_row in h4_df.iterrows():
        base = float(h4_row["close"])
        for offset_h in range(4):
            h1_ts = h4_ts + pd.Timedelta(hours=offset_h)
            h1_price = base + price_offset_per_h1 * offset_h
            rows.append(
                {
                    "time": h1_ts,
                    "open": h1_price,
                    "high": force_high if force_high is not None else h1_price + 0.0002,
                    "low": force_low if force_low is not None else h1_price - 0.0002,
                    "close": h1_price,
                    "volume": 100,
                    "complete": True,
                }
            )
    return pd.DataFrame(rows).set_index("time")


# ── H1TradeManager unit tests ──────────────────────────────────────────────────


def test_h1_manager_partial_exit_fires():
    """Partial exit fires when profit > 1.5×H4_ATR at H1 close."""
    atr = 0.0010
    entry = 1.1000
    mgr = H1TradeManager(
        entry_price=entry,
        direction=1,
        stop_price=entry - 0.0030,
        atr_h4=atr,
        units=1_000,
        partial_exit_atr_mult=1.5,
        partial_exit_fraction=0.33,
        trail_atr_mult=1.5,
    )

    # Price not yet at threshold — no exit
    t = pd.Timestamp("2023-01-01 01:00", tz="UTC")
    evt = mgr.update(t, h1_high=entry + 0.0005, h1_low=entry - 0.0001, h1_close=entry + 0.0005)
    assert evt is None

    # Price crosses +1.5×ATR at close → partial fires
    close_at_target = entry + 1.6 * atr
    evt = mgr.update(t, h1_high=close_at_target, h1_low=entry, h1_close=close_at_target)
    assert evt is not None
    assert evt.exit_reason == "partial_tp"
    assert evt.is_partial is True
    assert evt.exit_price == pytest.approx(close_at_target)
    assert evt.units == max(1, int(1_000 * 0.33))
    assert mgr.partial_done is True
    assert mgr.trail_activated is True


def test_h1_manager_stop_hit_long():
    """Stop hit fires for long when H1 low <= stop_price."""
    entry = 1.1000
    stop = entry - 0.0020
    mgr = H1TradeManager(
        entry_price=entry,
        direction=1,
        stop_price=stop,
        atr_h4=0.0010,
        units=1_000,
    )
    t = pd.Timestamp("2023-01-01 02:00", tz="UTC")
    # Low is exactly at stop
    evt = mgr.update(t, h1_high=entry, h1_low=stop, h1_close=entry - 0.0001)
    assert evt is not None
    assert evt.exit_reason == "stop_hit"
    assert evt.is_partial is False
    assert evt.exit_price == pytest.approx(stop)
    assert evt.units == 1_000


def test_h1_manager_stop_hit_short():
    """Stop hit fires for short when H1 high >= stop_price."""
    entry = 1.1000
    stop = entry + 0.0020
    mgr = H1TradeManager(
        entry_price=entry,
        direction=-1,
        stop_price=stop,
        atr_h4=0.0010,
        units=1_000,
    )
    t = pd.Timestamp("2023-01-01 03:00", tz="UTC")
    evt = mgr.update(t, h1_high=stop, h1_low=entry, h1_close=entry + 0.0001)
    assert evt is not None
    assert evt.exit_reason == "stop_hit"
    assert evt.exit_price == pytest.approx(stop)


def test_h1_manager_trail_only_tightens():
    """
    After trail activates, stop_price must only move in the trade's favour
    (only tighten — never widen).
    """
    atr = 0.0010
    entry = 1.1000
    initial_stop = entry - 0.0025
    mgr = H1TradeManager(
        entry_price=entry,
        direction=1,
        stop_price=initial_stop,
        atr_h4=atr,
        units=1_000,
        partial_exit_atr_mult=1.5,
        partial_exit_fraction=0.33,
        trail_atr_mult=1.5,
    )

    t0 = pd.Timestamp("2023-01-01 00:00", tz="UTC")

    # Fire partial exit to activate trail
    target_close = entry + 1.6 * atr
    mgr.update(t0, h1_high=target_close, h1_low=entry, h1_close=target_close)
    assert mgr.trail_activated

    # Advance price → trail tightens
    prev_stop = mgr.stop_price
    higher_close = target_close + 0.5 * atr
    mgr.update(t0, h1_high=higher_close, h1_low=entry, h1_close=higher_close)
    assert mgr.stop_price >= prev_stop, "Trail stop must only tighten (move up for long)"

    # Price pulls back → trail stop must NOT move down
    prev_stop2 = mgr.stop_price
    lower_close = target_close - 0.2 * atr
    mgr.update(t0, h1_high=lower_close, h1_low=lower_close - 0.0005, h1_close=lower_close)
    assert mgr.stop_price >= prev_stop2 - 1e-10, (
        "Trail stop must not widen when price pulls back"
    )


# ── MultiTimeframeBacktester integration tests ────────────────────────────────


def test_mtf_backtester_flat_signal_no_trades(monkeypatch):
    """Signal=0 throughout → no trades should be opened."""
    _mock_registry_and_pip(monkeypatch)

    h4_df = make_h4_signal_df(50, signals=[0] * 50)
    h1_df = make_h1_df(h4_df)

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", h4_df, h1_df)

    assert isinstance(result, BacktestResult)
    assert len(result.trades) == 0


def test_mtf_backtester_long_signal_completes(monkeypatch):
    """Constant long signal with wide stop — backtester completes without error."""
    _mock_registry_and_pip(monkeypatch)

    h4_df = make_h4_signal_df(60, signals=[1] * 60)
    h1_df = make_h1_df(h4_df)

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", h4_df, h1_df)

    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) > 0
    assert isinstance(result.metrics, dict)


def test_mtf_backtester_result_schema(monkeypatch):
    """All VectorizedBacktester metric keys must be present, plus avg_h1_bars_in_trade."""
    _mock_registry_and_pip(monkeypatch)

    h4_df = make_h4_signal_df(40, signals=[1] * 40)
    h1_df = make_h1_df(h4_df)

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", h4_df, h1_df)

    required_keys = [
        "cagr", "total_return", "sharpe", "sortino",
        "max_drawdown", "max_dd_duration_bars", "profit_factor",
        "hit_rate", "payoff_ratio", "n_periods",
        "avg_h1_bars_in_trade",  # MTF-specific diagnostic
    ]
    for k in required_keys:
        assert k in result.metrics, f"Missing metric key: {k}"

    # avg_h1_bars_in_trade should be a non-negative float
    avg = result.metrics["avg_h1_bars_in_trade"]
    assert isinstance(avg, float)
    assert avg >= 0.0


def test_mtf_bar_alignment_excludes_entry_bar_h1(monkeypatch):
    """
    H1 bars within the H4 entry bar must NOT trigger exits.

    Setup: entry at H4[1] open. H1 bars within H4[1] (timestamps H4[1] to H4[2])
    have a very low `low` that would hit the structural stop — but the bar
    alignment rule excludes them. The stop should only be checked from H4[2] onwards.

    We place a high stop (close to entry) that would definitely hit if we checked
    the entry bar's H1 bars, then verify the trade is NOT stopped out immediately.
    """
    _mock_registry_and_pip(monkeypatch)

    atr = 0.0010
    entry_price_approx = 1.1000

    # 20 H4 bars; signal=1 from bar 0, so entry at bar 1 open
    n = 20
    signals = [1] * n
    h4_df = make_h4_signal_df(n, signals=signals, base_price=entry_price_approx, atr=atr,
                               stop_distance=0.0015)

    # H1 bars within the entry bar (H4[1]) have an extremely low low that would
    # hit any reasonable stop — but should be excluded.
    # H1 bars for H4[2] onwards are normal (safe, won't hit stop).
    h4_times = list(h4_df.index)
    entry_h4_time = h4_times[1]       # this is the entry bar's open time
    next_h4_time = h4_times[2]        # first management bar starts here

    rows = []
    for i, h4_ts in enumerate(h4_times):
        base = float(h4_df["close"].iloc[i])
        for offset_h in range(4):
            h1_ts = h4_ts + pd.Timedelta(hours=offset_h)
            # Within the entry bar: force low far below any stop
            if h4_ts == entry_h4_time:
                forced_low = 0.5000   # extreme low — would definitely hit stop
            else:
                forced_low = base - 0.0001  # safe
            rows.append({
                "time": h1_ts,
                "open": base,
                "high": base + 0.0002,
                "low": forced_low,
                "close": base,
                "volume": 100,
                "complete": True,
            })

    h1_df = pd.DataFrame(rows).set_index("time")

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", h4_df, h1_df)

    # If bar alignment works, the entry bar's H1 bars don't trigger a stop.
    # The trade should run to end_of_data (not be stopped out immediately).
    stop_hits = [t for t in result.trades if t.exit_reason == "stop_hit"]
    # Any stop hits should be from H4[2]+ bars, not from entry bar H1
    # Simplest check: there should be at least one trade that lived past the entry bar
    assert len(result.trades) >= 1
    # No stop hit should fire on the very first H4 candle after entry
    if stop_hits:
        first_stop = stop_hits[0]
        # entry_time is at H4[1] open; stop at H4[1] H1 would be within entry bar
        entry_bar_end = next_h4_time
        assert first_stop.exit_time >= entry_bar_end, (
            f"Stop hit at {first_stop.exit_time} which is within the entry bar "
            f"({entry_h4_time} to {entry_bar_end}) — bar alignment violated"
        )


def test_minimum_hold_h4_bars_blocks_signal_exit(monkeypatch):
    """
    Signal flip within MINIMUM_HOLD_BARS H4 bars must not produce a signal_exit.

    Setup: signal=1 at bar 0-1, flips to 0 at bar 2, data ends at bar 5.
    bars_held_h4 never reaches MINIMUM_HOLD_BARS (5) before data ends,
    so signal_exit is always blocked. Position closes as end_of_data.

    n=6 means h4_idx loops 1..5. Entry at h4_idx=1 (bars_held=0→1).
    Signal flip detected at h4_idx=3 when bars_held=2 (blocked).
    At h4_idx=5 bars_held=4 (still < 5, still blocked). Data ends → end_of_data.
    """
    _mock_registry_and_pip(monkeypatch)
    assert MINIMUM_HOLD_BARS == 5

    # n=6: entry at H4[1], signal flips at H4[2], data ends before bars_held=5
    n = 6
    signals = [1, 1, 0, 0, 0, 0]
    h4_df = make_h4_signal_df(n, signals=signals, stop_distance=0.1000)  # very wide stop
    h1_df = make_h1_df(h4_df)

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", h4_df, h1_df)

    signal_exits = [t for t in result.trades if t.exit_reason == "signal_exit"]
    assert len(signal_exits) == 0, (
        "signal_exit must be blocked when bars_held_h4 < MINIMUM_HOLD_BARS"
    )

    end_exits = [t for t in result.trades if t.exit_reason == "end_of_data"]
    assert len(end_exits) >= 1, "Position should close as end_of_data"


def test_minimum_hold_allows_signal_exit_after_5_h4_bars(monkeypatch):
    """
    Signal flip after >= MINIMUM_HOLD_BARS H4 bars should produce a signal_exit.
    """
    _mock_registry_and_pip(monkeypatch)

    # Long for 7 bars then flat — well past the 5-bar minimum
    n = 30
    signals = [1] * 7 + [0] * (n - 7)
    h4_df = make_h4_signal_df(n, signals=signals, stop_distance=0.1000)
    h1_df = make_h1_df(h4_df)

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", h4_df, h1_df)

    signal_exits = [t for t in result.trades if t.exit_reason == "signal_exit"]
    assert len(signal_exits) >= 1, (
        "signal_exit should fire once bars_held_h4 >= MINIMUM_HOLD_BARS"
    )
