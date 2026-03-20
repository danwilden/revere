"""Tests verifying that the backtest engine actually produces trades.

These tests use RulesStrategy + the engine directly — no Bedrock, no DB.
They document known silent-failure modes and confirm the entry mechanics work.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.backtest.costs import CostModel
from backend.backtest.engine import run_backtest
from backend.schemas.enums import Timeframe
from backend.schemas.models import BacktestRun
from backend.strategies.rules_strategy import RulesStrategy


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors pattern from test_backtest.py)
# ---------------------------------------------------------------------------

def _make_bar(bar_idx: int, ts: datetime, o, h, l, c, extra: dict | None = None) -> dict:
    bar = {
        "_bar_idx": bar_idx,
        "instrument_id": "EUR_USD",
        "timestamp_utc": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
        # Lifecycle fields always injected by engine — pre-seed here for rule tests
        "bars_in_trade": 0,
        "minutes_in_trade": 0,
        "days_in_trade": 0.0,
    }
    if extra:
        bar.update(extra)
    return bar


def _flat_bars(n: int = 10, base: float = 1.1000) -> list[dict]:
    start = datetime(2024, 1, 2, 0, 0, 0)
    return [
        _make_bar(i, start + timedelta(hours=i), base, base + 0.0010, base - 0.0010, base)
        for i in range(n)
    ]


def _make_backtest_run() -> BacktestRun:
    return BacktestRun(
        instrument_id="EUR_USD",
        timeframe=Timeframe.H1,
        test_start=datetime(2024, 1, 2),
        test_end=datetime(2024, 1, 10),
    )


def _zero_cost() -> CostModel:
    return CostModel(spread_pips=0.0, slippage_pips=0.0, commission_per_unit=0.0)


# ---------------------------------------------------------------------------
# Test 1 — always-true entry produces at least one trade
# ---------------------------------------------------------------------------

def test_always_true_entry_produces_trades():
    """Entry rule {close > 0} fires on every bar — should always produce trades."""
    definition = {
        "entry_long": {"field": "close", "op": "gt", "value": 0.0},
        "exit": {"field": "bars_in_trade", "op": "gte", "value": 3},
        "position_size_units": 1000,
        "stop_atr_multiplier": 2.0,
        "take_profit_atr_multiplier": 3.0,
    }
    strategy = RulesStrategy(definition)
    bars = _flat_bars(20)
    # Inject atr_14 so stop/target calcs don't crash
    for b in bars:
        b["atr_14"] = 0.0010

    trades, metrics, _, _ = run_backtest(
        strategy=strategy,
        bars=bars,
        backtest_run=_make_backtest_run(),
        cost_model=_zero_cost(),
    )
    assert len(trades) >= 1, (
        "Expected at least one trade with always-true entry rule, got 0. "
        "This indicates a silent failure in the engine or rules evaluation."
    )


# ---------------------------------------------------------------------------
# Test 2 — position_size_units=0 documents silent failure behaviour
# ---------------------------------------------------------------------------

def test_position_size_zero_silent_failure():
    """Documents the zero-units silent failure mode.

    When position_size_units=0 the engine processes the trade but PnL is 0
    because quantity is forced to abs(0)=0. Trade count may be non-zero
    but effectively harmless. This test documents current behaviour so any
    future change (e.g. skip-trade guard) is deliberate.
    """
    definition = {
        "entry_long": {"field": "close", "op": "gt", "value": 0.0},
        "exit": {"field": "bars_in_trade", "op": "gte", "value": 2},
        "position_size_units": 0,  # intentionally zero — documents silent-failure mode
        "stop_atr_multiplier": 2.0,
        "take_profit_atr_multiplier": 3.0,
    }
    strategy = RulesStrategy(definition)
    bars = _flat_bars(10)
    for b in bars:
        b["atr_14"] = 0.0010

    trades, metrics, _, _ = run_backtest(
        strategy=strategy,
        bars=bars,
        backtest_run=_make_backtest_run(),
        cost_model=_zero_cost(),
    )
    # All trades (if any) should have zero PnL since quantity is 0
    for t in trades:
        assert t.pnl == 0.0, "Expected zero PnL for zero-quantity trade"


# ---------------------------------------------------------------------------
# Test 3 — feature field without feature_run raises ValueError in rules engine
# ---------------------------------------------------------------------------

def test_feature_field_without_feature_run_raises():
    """Using rsi_14 in a rule with no rsi_14 in context raises ValueError.

    This documents the failure mode: the rules_engine raises ValueError when
    a field is missing from the evaluation context. The exception propagates
    through on_bar() and out of run_backtest() — it is NOT silently swallowed.
    The strategy researcher's FEATURE_AVAILABILITY guard prevents this by
    forbidding FULL feature fields when no feature_run_id is set.
    """
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 30.0},
        "exit": {"field": "bars_in_trade", "op": "gte", "value": 2},
        "position_size_units": 1000,
    }
    strategy = RulesStrategy(definition)
    # bars have no rsi_14 key — rules_engine will raise ValueError on eval
    bars = _flat_bars(10)

    with pytest.raises(ValueError, match="rsi_14"):
        run_backtest(
            strategy=strategy,
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )


# ---------------------------------------------------------------------------
# Test 4 — tight vs loose threshold trade count comparison
# ---------------------------------------------------------------------------

def test_tight_vs_loose_threshold_trade_count():
    """Demonstrates that looser RSI thresholds produce more trades.

    Uses synthetic bars with injected rsi_14 values cycling 20→80.
    rsi < 20 fires rarely; rsi < 60 fires often.
    """
    import math

    start = datetime(2024, 1, 2, 0, 0, 0)
    bars = []
    for i in range(50):
        # RSI cycles 20→80 using a sine wave
        rsi = 50.0 + 30.0 * math.sin(i * 0.3)
        bar = _make_bar(
            i,
            start + timedelta(hours=i),
            1.1000, 1.1010, 1.0990, 1.1000,
            extra={"rsi_14": rsi, "atr_14": 0.0010},
        )
        bars.append(bar)

    def _run_with_threshold(threshold: float) -> int:
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": threshold},
            "exit": {"field": "bars_in_trade", "op": "gte", "value": 3},
            "position_size_units": 1000,
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 4.0,
        }
        trades, _, _, _ = run_backtest(
            strategy=RulesStrategy(definition),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        return len(trades)

    tight_trades = _run_with_threshold(22.0)   # fires rarely (very oversold)
    loose_trades = _run_with_threshold(60.0)   # fires often (moderate threshold)

    assert loose_trades > tight_trades, (
        f"Expected loose threshold (rsi<60) to produce more trades than tight (rsi<22). "
        f"Got tight={tight_trades}, loose={loose_trades}."
    )
