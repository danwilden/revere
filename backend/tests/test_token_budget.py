"""Tests for tool result truncation helpers (token budget management).

All tests are pure function tests — no I/O, no network, no DB.
"""
from __future__ import annotations

import json
import pytest

from backend.agents.tools.truncation import (
    _serialized_size,
    truncate_equity_curve,
    truncate_metrics,
    truncate_trades,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_equity_curve(n: int) -> dict:
    """Generate a fake GetEquityCurveOutput dict with n equity points."""
    from datetime import datetime, timedelta
    start = datetime(2024, 1, 1)
    curve = []
    equity = 10000.0
    drawdown = 0.0
    for i in range(n):
        equity += (1.0 if i % 3 != 0 else -2.0)
        drawdown = min(0.0, drawdown - (0.5 if i % 3 == 0 else 0.0))
        curve.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "equity": equity,
            "drawdown": drawdown,
        })
    return {"run_id": "run-001", "equity_curve": curve}


def _make_trades(n: int) -> dict:
    """Generate a fake GetBacktestTradesOutput dict with n trades."""
    trades = []
    for i in range(n):
        trades.append({
            "id": f"trade-{i}",
            "backtest_run_id": "run-001",
            "instrument_id": "EUR_USD",
            "entry_time": "2024-01-02T00:00:00",
            "exit_time": "2024-01-02T04:00:00",
            "side": "long",
            "quantity": 1000.0,
            "entry_price": 1.1000,
            "exit_price": 1.1010,
            "pnl": 1.0 if i % 2 == 0 else -0.5,
            "pnl_pct": 0.09,
            "holding_period": 4,
            "entry_reason": "rsi",
            "exit_reason": "target",
            "regime_at_entry": "TREND_BULL",
            "regime_at_exit": "TREND_BULL",
        })
    return {"run_id": "run-001", "trades": trades, "count": n}


def _make_metrics(n: int) -> dict:
    """Generate a fake GetBacktestRunOutput dict with n metrics."""
    names = [
        "trade_count", "sharpe_ratio", "net_return_pct", "max_drawdown_pct",
        "win_rate", "avg_pnl_per_trade", "profit_factor", "avg_holding_bars",
        "annualized_return_pct", "calmar_ratio", "sortino_ratio", "expectancy",
        "regime_breakdown_count", "gross_profit", "total_trades",
        "gross_loss", "net_profit", "max_consecutive_wins", "max_consecutive_losses",
        "avg_win", "avg_loss", "risk_reward_ratio", "system_quality", "kelly_pct",
        "recovery_factor",
    ]
    metrics = [
        {
            "id": f"metric-{i}",
            "backtest_run_id": "run-001",
            "metric_name": names[i % len(names)],
            "metric_value": float(i),
            "segment_type": "overall",
            "segment_key": "all",
        }
        for i in range(n)
    ]
    return {
        "run": {"id": "run-001", "instrument_id": "EUR_USD"},
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Test 1 — equity curve truncation reduces byte size >90%
# ---------------------------------------------------------------------------

def test_truncate_equity_curve_reduces_size():
    raw = _make_equity_curve(500)
    original_size = _serialized_size(raw)
    result = truncate_equity_curve(raw)

    assert result["truncated"] is True
    assert "equity_summary" in result
    assert "equity_curve" not in result

    summary = result["equity_summary"]
    assert "bar_count" in summary
    assert summary["bar_count"] == 500
    assert "initial_equity" in summary
    assert "final_equity" in summary
    assert "max_drawdown_pct" in summary

    reduced_size = _serialized_size(result)
    reduction_pct = (original_size - reduced_size) / original_size
    assert reduction_pct > 0.90, (
        f"Expected >90% size reduction, got {reduction_pct:.1%}. "
        f"Original: {original_size} bytes, reduced: {reduced_size} bytes."
    )


# ---------------------------------------------------------------------------
# Test 2 — trade truncation produces head/tail sampling
# ---------------------------------------------------------------------------

def test_truncate_trades_head_tail_sampling():
    raw = _make_trades(20)
    result = truncate_trades(raw)

    assert result["truncated"] is True
    assert result["count"] == 20
    assert "avg_pnl" in result
    assert "total_pnl" in result
    assert "win_rate" in result
    assert "avg_holding_bars" in result

    assert len(result["first_3"]) == 3
    assert len(result["last_3"]) == 3
    # first_3 and last_3 should be different (20 trades, not overlapping)
    assert result["first_3"][0]["id"] == "trade-0"
    assert result["last_3"][-1]["id"] == "trade-19"

    # Win rate: 10 wins (even indices 0,2,4,...18) out of 20 = 0.5
    assert abs(result["win_rate"] - 0.5) < 0.01

    # Trades key should be absent (not passed through)
    assert "trades" not in result


# ---------------------------------------------------------------------------
# Test 3 — metrics truncation keeps priority fields (≤15 keys)
# ---------------------------------------------------------------------------

def test_truncate_metrics_keeps_priority_fields():
    raw = _make_metrics(25)
    result = truncate_metrics(raw)

    assert result["truncated"] is True
    assert "metrics" in result
    metrics = result["metrics"]

    # Must not exceed 15 keys
    assert len(metrics) <= 15, f"Expected ≤15 metrics keys, got {len(metrics)}"

    # Priority field: trade_count must be present if it was in the input
    assert "trade_count" in metrics

    # Raw metrics list must not appear in result
    assert "run" not in result


# ---------------------------------------------------------------------------
# Test 4 — all three truncators are safe on empty/malformed input
# ---------------------------------------------------------------------------

def test_truncation_safe_on_empty_input():
    """Empty dict input to all three truncators must not raise."""
    for fn in (truncate_equity_curve, truncate_trades, truncate_metrics):
        result = fn({})
        assert isinstance(result, dict), f"{fn.__name__} did not return a dict on empty input"
        # truncated=False signals "could not truncate" — safe fallback
        assert result.get("truncated") is False


def test_truncation_safe_on_none_values():
    """Dicts with None lists must not raise."""
    assert truncate_equity_curve({"run_id": "x", "equity_curve": None})["truncated"] is False
    assert truncate_trades({"run_id": "x", "trades": None})["truncated"] is False
    assert truncate_metrics({"run": None, "metrics": None})["truncated"] is False


def test_truncate_trades_empty_trades_list():
    raw = {"run_id": "r", "trades": [], "count": 0}
    result = truncate_trades(raw)
    assert result["truncated"] is True
    assert result["count"] == 0
    assert result["first_3"] == []
    assert result["last_3"] == []
