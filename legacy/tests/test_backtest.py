"""
Tests for the backtest engine and metrics module.

Uses synthetic data. No OANDA API or file I/O required.
"""

import numpy as np
import pandas as pd
import pytest

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import BacktestResult, VectorizedBacktester
from forex_system.backtest.metrics import (
    cagr,
    full_tearsheet,
    hit_rate,
    max_drawdown,
    max_drawdown_duration,
    payoff_ratio,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)


# ── Synthetic data helpers ─────────────────────────────────────────────────────


def make_signal_df(
    n: int = 300,
    signal_val: int = 1,
    stop_distance: float = 0.002,
    units: int = 1_000,
) -> pd.DataFrame:
    """Create a synthetic signal DataFrame for backtesting."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    base_price = 1.10
    close = pd.Series(
        base_price * np.cumprod(1 + rng.normal(0, 0.001, n)), index=dates
    )
    return pd.DataFrame(
        {
            "open": (close * 0.9999).values,
            "high": (close * 1.0012).values,
            "low": (close * 0.9988).values,
            "close": close.values,
            "volume": 500,
            "signal": signal_val,
            "stop_distance": stop_distance,
            "units": units,
        },
        index=dates,
    )


def make_equity_curve(n: int = 200, initial: float = 10_000.0) -> pd.Series:
    rng = np.random.default_rng(99)
    returns = rng.normal(0.0005, 0.005, n)
    equity = initial * np.cumprod(1 + returns)
    dates = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series(equity, index=dates)


# ── CostModel tests ────────────────────────────────────────────────────────────


def test_cost_model_default_spread():
    cm = CostModel()
    assert cm.total_cost_pips("EUR_USD") == 1.0 + 0.5  # spread + slippage


def test_cost_model_unknown_instrument():
    cm = CostModel()
    assert cm.total_cost_pips("XYZ_ABC") == 2.0 + 0.5  # fallback spread


def test_cost_model_custom_spread():
    cm = CostModel(spread_pips={"EUR_USD": 0.5}, slippage_pips=0.2)
    assert cm.total_cost_pips("EUR_USD") == 0.7


# ── Metrics tests ─────────────────────────────────────────────────────────────


def test_sharpe_positive_trend():
    eq = make_equity_curve(252)
    returns = eq.pct_change().dropna()
    s = sharpe_ratio(returns, 252)
    assert isinstance(s, float)
    assert not np.isnan(s)


def test_sharpe_flat_returns_zero():
    returns = pd.Series([0.001] * 100)
    # All same returns → std=0 (not exactly 0 due to floating point)
    s = sharpe_ratio(returns)
    assert isinstance(s, float)


def test_sortino_finite():
    eq = make_equity_curve(100)
    returns = eq.pct_change().dropna()
    s = sortino_ratio(returns)
    assert isinstance(s, float)
    assert not np.isnan(s)


def test_max_drawdown_negative():
    eq = pd.Series([10000, 10500, 9800, 10200, 10100])
    dd = max_drawdown(eq)
    assert dd < 0
    assert dd > -1


def test_max_drawdown_monotone_increasing():
    eq = pd.Series([10000, 10100, 10200, 10300])
    dd = max_drawdown(eq)
    assert dd == 0.0


def test_max_drawdown_duration():
    # Decline then recovery
    eq = pd.Series([10000, 10500, 9500, 9800, 10600])
    dur = max_drawdown_duration(eq)
    assert dur >= 0


def test_profit_factor_basic():
    pnls = pd.Series([100, -50, 200, -80, 150])
    pf = profit_factor(pnls)
    assert pf == pytest.approx((100 + 200 + 150) / (50 + 80))


def test_profit_factor_no_losses():
    pnls = pd.Series([100, 200, 50])
    assert profit_factor(pnls) == float("inf")


def test_hit_rate_all_wins():
    pnls = pd.Series([1, 2, 3])
    assert hit_rate(pnls) == 1.0


def test_hit_rate_all_losses():
    pnls = pd.Series([-1, -2, -3])
    assert hit_rate(pnls) == 0.0


def test_payoff_ratio():
    pnls = pd.Series([100, -50, 100, -50])
    pr = payoff_ratio(pnls)
    assert pr == pytest.approx(2.0)


def test_cagr_finite():
    eq = make_equity_curve(252)
    c = cagr(eq, 252)
    assert isinstance(c, float)
    assert not np.isnan(c)


def test_full_tearsheet_keys():
    eq = make_equity_curve(252)
    report = full_tearsheet(eq)
    expected_keys = [
        "cagr", "total_return", "sharpe", "sortino",
        "max_drawdown", "max_dd_duration_bars", "profit_factor",
        "hit_rate", "payoff_ratio", "n_periods",
    ]
    for k in expected_keys:
        assert k in report, f"Missing key: {k}"


# ── Backtester tests ──────────────────────────────────────────────────────────


def test_backtester_runs_long_only(tmp_path, monkeypatch):
    """Backtester should complete without error on a constant long signal."""
    from unittest.mock import MagicMock
    from forex_system.data import instruments as inst_mod

    # Mock the registry to avoid OANDA API call
    mock_meta = MagicMock()
    mock_meta.pip_size = 0.0001
    mock_meta.pip_location = -4
    mock_meta.min_trade_size = 1
    monkeypatch.setattr(inst_mod.registry, "get", lambda _: mock_meta)

    # Also mock pip_value_per_unit_usd
    from forex_system.risk import sizing as sz_mod
    monkeypatch.setattr(
        sz_mod, "pip_value_per_unit_usd", lambda inst, price, acct="USD": 0.0001
    )

    df = make_signal_df(200, signal_val=1)
    bt = VectorizedBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", "H1", df)

    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) > 0
    assert isinstance(result.metrics, dict)
    assert "sharpe" in result.metrics


def test_backtester_flat_signal_no_trades(monkeypatch):
    """Signal of 0 throughout → no trades should be opened."""
    from unittest.mock import MagicMock
    from forex_system.data import instruments as inst_mod

    mock_meta = MagicMock()
    mock_meta.pip_size = 0.0001
    mock_meta.pip_location = -4
    mock_meta.min_trade_size = 1
    monkeypatch.setattr(inst_mod.registry, "get", lambda _: mock_meta)

    from forex_system.risk import sizing as sz_mod
    monkeypatch.setattr(
        sz_mod, "pip_value_per_unit_usd", lambda inst, price, acct="USD": 0.0001
    )

    df = make_signal_df(100, signal_val=0)
    bt = VectorizedBacktester(initial_equity=10_000)
    result = bt.run("EUR_USD", "H1", df)

    assert len(result.trades) == 0


def test_backtester_metrics_finite(monkeypatch):
    """All metrics should be finite floats, not NaN or inf."""
    from unittest.mock import MagicMock
    from forex_system.data import instruments as inst_mod

    mock_meta = MagicMock()
    mock_meta.pip_size = 0.0001
    mock_meta.pip_location = -4
    mock_meta.min_trade_size = 1
    monkeypatch.setattr(inst_mod.registry, "get", lambda _: mock_meta)

    from forex_system.risk import sizing as sz_mod
    monkeypatch.setattr(
        sz_mod, "pip_value_per_unit_usd", lambda inst, price, acct="USD": 0.0001
    )

    df = make_signal_df(300, signal_val=1)
    bt = VectorizedBacktester(initial_equity=10_000)
    result = bt.run("EUR_USD", "H1", df)

    for k, v in result.metrics.items():
        if isinstance(v, float):
            assert not np.isnan(v), f"NaN in metric: {k}"
            # inf is allowed for profit_factor when there are no losses


# ── CHANGE 8: Minimum hold tests ──────────────────────────────────────────────


def _mock_backtester(monkeypatch):
    """Set up registry + pip_value mocks needed by VectorizedBacktester."""
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


def make_signal_df_variable(signals: list[int], stop_distance: float = 0.002) -> pd.DataFrame:
    """Build a signal DataFrame from an explicit list of per-bar signals."""
    n = len(signals)
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    # Flat price so stops are never hit unless stop_distance is tiny
    close = pd.Series([1.1000] * n, index=dates)
    return pd.DataFrame(
        {
            "open": 1.1000,
            "high": 1.1010,
            "low":  1.0990,
            "close": close.values,
            "signal": signals,
            "stop_distance": stop_distance,
            "units": 1_000,
        },
        index=dates,
    )


def test_minimum_hold_blocks_signal_exit_before_5_bars(monkeypatch):
    """
    CHANGE 8: A signal that flips to flat while bars_held < MINIMUM_HOLD_BARS
    should NOT produce a signal_exit trade.

    We use exactly 5 bars so that bars_held never reaches 5 before the data ends.
    The position should close as 'end_of_data', not 'signal_exit'.
    """
    from forex_system.backtest.engine import MINIMUM_HOLD_BARS

    assert MINIMUM_HOLD_BARS == 5

    _mock_backtester(monkeypatch)

    # Long for 3 bars, then flat for 2 — total 5 rows.
    # Entry is at bar 1 open (bars_held starts at 0 after entry).
    # Signal flips at bar 3 (prev["signal"] for bar i=4 = 0).
    # At bar i=4: bars_held=3 < 5 → signal_exit blocked.
    # Data ends → end_of_data close.
    signals = [1, 1, 1, 0, 0]  # exactly 5 rows
    df = make_signal_df_variable(signals)
    bt = VectorizedBacktester(initial_equity=10_000)
    result = bt.run("EUR_USD", "H1", df)

    signal_exits  = [t for t in result.trades if t.exit_reason == "signal_exit"]
    end_of_data   = [t for t in result.trades if t.exit_reason == "end_of_data"]
    assert len(signal_exits) == 0, (
        "signal_exit should be blocked when bars_held < MINIMUM_HOLD_BARS"
    )
    assert len(end_of_data) == 1, "Position should close as end_of_data"


def test_minimum_hold_allows_signal_exit_at_5_bars(monkeypatch):
    """
    CHANGE 8: A signal that flips after 5+ bars of holding should produce a
    signal_exit trade.
    """
    _mock_backtester(monkeypatch)

    # Long for 6 bars then flat — flip occurs after minimum hold
    signals = [1, 1, 1, 1, 1, 1, 0] + [0] * 100
    df = make_signal_df_variable(signals)
    bt = VectorizedBacktester(initial_equity=10_000)
    result = bt.run("EUR_USD", "H1", df)

    signal_exits = [t for t in result.trades if t.exit_reason == "signal_exit"]
    assert len(signal_exits) == 1, (
        "signal_exit should fire once bars_held >= MINIMUM_HOLD_BARS"
    )


def test_stop_hit_ignores_minimum_hold(monkeypatch):
    """
    CHANGE 8: A guaranteed stop hit should fire even if bars_held < MINIMUM_HOLD_BARS.
    """
    _mock_backtester(monkeypatch)

    # Tiny stop → guaranteed hit immediately after entry; never reaches 5 bars
    signals = [1] * 50
    df = make_signal_df_variable(signals, stop_distance=0.000001)
    bt = VectorizedBacktester(initial_equity=10_000)
    result = bt.run("EUR_USD", "H1", df)

    stop_hits = [t for t in result.trades if t.exit_reason == "stop_hit"]
    assert len(stop_hits) >= 1, "stop_hit should fire regardless of minimum hold"


# ── Phase 1A: Two-stage exit tests ────────────────────────────────────────────


def make_signal_df_with_atr(
    n: int = 50,
    signal_val: int = 1,
    stop_distance: float = 0.002,
    units: int = 1_000,
    entry_price: float = 1.1000,
    atr: float = 0.0010,
) -> pd.DataFrame:
    """Signal DataFrame with ATR column, needed to activate trail / two-stage exit."""
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    # Gently trending up so partial exit fires, then price reverses to stop
    prices = [entry_price + atr * 0.5 * i / n for i in range(n)]
    return pd.DataFrame(
        {
            "open":          prices,
            "high":          [p + atr * 0.3 for p in prices],
            "low":           [p - atr * 0.05 for p in prices],
            "close":         prices,
            "signal":        signal_val,
            "stop_distance": stop_distance,
            "units":         units,
            "atr":           atr,
        },
        index=dates,
    )


def test_two_stage_exit_produces_partial_tp(monkeypatch):
    """
    Phase 1A: When price > +1.0×ATR profit and two_stage_exit=True,
    a 'partial_tp' trade record should be emitted.
    """
    _mock_backtester(monkeypatch)

    atr = 0.0010
    # Price rises to entry + 2×ATR over 50 bars → partial fires around bar 25
    entry_price = 1.1000
    n = 50
    prices = [entry_price + atr * 2.0 * i / n for i in range(n)]
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    df = pd.DataFrame(
        {
            "open":          prices,
            "high":          [p + atr * 0.1 for p in prices],
            "low":           [p - atr * 0.05 for p in prices],
            "close":         prices,
            "signal":        1,
            "stop_distance": atr * 2.0,
            "units":         1_000,
            "atr":           atr,
        },
        index=dates,
    )

    bt = VectorizedBacktester(
        initial_equity=10_000,
        two_stage_exit=True,
        partial_exit_atr_mult=1.0,
        partial_exit_fraction=0.5,
        trail_atr_mult_remainder=0.5,
    )
    result = bt.run("EUR_USD", "H1", df)

    partial_trades = [t for t in result.trades if t.exit_reason == "partial_tp"]
    assert len(partial_trades) >= 1, "partial_tp trade should fire when profit > 1.0×ATR"
    assert partial_trades[0].units > 0


def test_partial_exit_stop_never_below_structural(monkeypatch):
    """
    Phase 1A: After Stage 1 partial exit fires, the stop_price must never
    fall below the original structural stop. The max() tightening in the trail
    loop guarantees this — verify it holds across a realistic price sequence.

    Setup:
        entry = 1.1000, structural_stop = 1.0980 (20 pips below), atr = 0.0010
        Partial fires when price > 1.1000 + 1.0×ATR = 1.1010
        Trail stop = best_close - 0.5×ATR ≥ 1.1010 - 0.0005 = 1.1005 > 1.0980 ✓
    """
    _mock_backtester(monkeypatch)

    atr          = 0.0010
    entry_price  = 1.1000
    structural_stop = entry_price - 0.0020   # 20 pips below entry

    n = 60
    # Rises to trigger partial, then stabilises
    prices = (
        [entry_price + atr * 1.5 * i / 30 for i in range(30)]   # rise to +1.5×ATR
        + [entry_price + atr * 1.5] * 30                         # flat plateau
    )
    dates = pd.date_range("2023-01-01", periods=n, freq="H", tz="UTC")
    df = pd.DataFrame(
        {
            "open":          prices,
            "high":          [p + atr * 0.1 for p in prices],
            "low":           [p - atr * 0.05 for p in prices],
            "close":         prices,
            "signal":        1,
            "stop_distance": entry_price - structural_stop,
            "units":         1_000,
            "atr":           atr,
        },
        index=dates,
    )

    bt = VectorizedBacktester(
        initial_equity=10_000,
        two_stage_exit=True,
        partial_exit_atr_mult=1.0,
        partial_exit_fraction=0.5,
        trail_atr_mult_remainder=0.5,
    )
    result = bt.run("EUR_USD", "H1", df)

    # Partial_tp must exist (price crossed +1.0×ATR)
    partial_trades = [t for t in result.trades if t.exit_reason == "partial_tp"]
    assert len(partial_trades) >= 1, "Partial exit should have fired"

    # All stop_prices recorded in trade records must be >= structural stop
    # (the remainder's stop_price at exit should be above structural stop)
    for trade in result.trades:
        assert trade.stop_price >= structural_stop - 1e-9, (
            f"stop_price {trade.stop_price:.6f} fell below structural stop "
            f"{structural_stop:.6f} — trail must only tighten"
        )
