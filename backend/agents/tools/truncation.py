"""Tool result truncation helpers — reduce token volume for large backtest outputs.

All functions are pure: they never raise, never mutate the input, and return
the input unchanged (with truncated=False) if the expected structure is absent.
"""
from __future__ import annotations

import json
from typing import Any

_PRIORITY_METRICS = [
    "trade_count",
    "sharpe_ratio",
    "net_return_pct",
    "max_drawdown_pct",
    "win_rate",
    "avg_pnl_per_trade",
    "profit_factor",
    "avg_holding_bars",
    "annualized_return_pct",
    "calmar_ratio",
    "sortino_ratio",
    "expectancy",
    "regime_breakdown_count",
    "gross_profit",
    "total_trades",
]


def truncate_equity_curve(raw: dict[str, Any]) -> dict[str, Any]:
    """Summarise equity curve into scalar stats instead of sending all bars.

    Input shape:  {"run_id": str, "equity_curve": [{"timestamp", "equity", "drawdown"}, ...]}
    Output shape: {"run_id": str, "equity_summary": {...scalars...}, "truncated": True}

    Returns ``raw`` unchanged (with truncated=False) on malformed input.
    """
    try:
        curve = raw.get("equity_curve")
        if not isinstance(curve, list) or len(curve) == 0:
            return {**raw, "truncated": False}

        equities = [p["equity"] for p in curve if "equity" in p]
        drawdowns = [p["drawdown"] for p in curve if "drawdown" in p]

        if not equities:
            return {**raw, "truncated": False}

        summary = {
            "bar_count": len(equities),
            "initial_equity": equities[0],
            "final_equity": equities[-1],
            "min_equity": min(equities),
            "max_equity": max(equities),
            "max_drawdown_pct": min(drawdowns) if drawdowns else None,
        }
        return {
            "run_id": raw.get("run_id"),
            "equity_summary": summary,
            "truncated": True,
        }
    except Exception:
        return {**raw, "truncated": False}


def truncate_trades(raw: dict[str, Any]) -> dict[str, Any]:
    """Replace full trade list with aggregate stats + head/tail samples.

    Input shape:  {"run_id": str, "trades": [...], "count": int}
    Output shape: {"run_id": str, "count": int, "avg_pnl": float,
                   "total_pnl": float, "win_rate": float,
                   "avg_holding_bars": float,
                   "first_3": [...], "last_3": [...], "truncated": True}

    Returns ``raw`` unchanged (with truncated=False) on malformed input.
    """
    try:
        trades = raw.get("trades")
        if not isinstance(trades, list):
            return {**raw, "truncated": False}

        count = len(trades)
        if count == 0:
            return {
                "run_id": raw.get("run_id"),
                "count": 0,
                "avg_pnl": 0.0,
                "total_pnl": 0.0,
                "win_rate": 0.0,
                "avg_holding_bars": 0.0,
                "first_3": [],
                "last_3": [],
                "truncated": True,
            }

        pnls = [t.get("pnl", 0.0) for t in trades]
        holding = [t.get("holding_period", 0) for t in trades]
        wins = sum(1 for p in pnls if p > 0)

        first_3 = trades[:3]
        last_3 = trades[-3:] if count > 3 else []

        return {
            "run_id": raw.get("run_id"),
            "count": count,
            "avg_pnl": sum(pnls) / count,
            "total_pnl": sum(pnls),
            "win_rate": wins / count,
            "avg_holding_bars": sum(holding) / count,
            "first_3": first_3,
            "last_3": last_3,
            "truncated": True,
        }
    except Exception:
        return {**raw, "truncated": False}


def truncate_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten metric list into a priority-keyed dict (≤15 keys).

    Input shape:  {"run": {...}, "metrics": [{"metric_name": str, "metric_value": float|None,
                   "segment_type": str, "segment_key": str}, ...]}
    Output shape: {"run_id": str, "metrics": {name: value}, "truncated": True}

    Only overall-segment metrics are included, ordered by _PRIORITY_METRICS.
    Returns ``raw`` unchanged (with truncated=False) on malformed input.
    """
    try:
        metrics_list = raw.get("metrics")
        run = raw.get("run") or {}
        if not isinstance(metrics_list, list):
            return {**raw, "truncated": False}

        # Build overall-segment lookup
        overall: dict[str, Any] = {}
        for m in metrics_list:
            if isinstance(m, dict) and m.get("segment_type") == "overall":
                overall[m["metric_name"]] = m.get("metric_value")

        # Pick priority fields in order
        selected: dict[str, Any] = {}
        for name in _PRIORITY_METRICS:
            if name in overall:
                selected[name] = overall[name]
        # Fill remaining slots up to 15 with whatever is left
        for name, val in overall.items():
            if name not in selected and len(selected) < 15:
                selected[name] = val

        return {
            "run_id": run.get("id") if isinstance(run, dict) else None,
            "metrics": selected,
            "truncated": True,
        }
    except Exception:
        return {**raw, "truncated": False}


# Size helper for tests
def _serialized_size(obj: Any) -> int:
    """Return approximate JSON byte count of an object."""
    return len(json.dumps(obj, default=str))
