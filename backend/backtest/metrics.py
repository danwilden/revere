"""Performance metrics computation from a completed backtest.

Entry points:
  compute_metrics(trades, equity_curve, backtest_run_id) → list[PerformanceMetric]
  build_equity_curve(trades, bar_timestamps, initial_equity) → (equity, drawdown)

All undefined metrics (zero trades, division by zero) are stored as None, never
NaN.  This keeps them safely serializable to JSON and distinct from zero values.
"""
from __future__ import annotations

import math
from datetime import datetime

from backend.schemas.models import PerformanceMetric, Trade


def compute_metrics(
    trades: list[Trade],
    equity_curve: list[float],
    backtest_run_id: str,
) -> list[PerformanceMetric]:
    """Compute the full set of required performance metrics.

    Returns PerformanceMetric records ready to persist.  Includes overall
    metrics plus a per-regime breakdown segmented by Trade.regime_at_entry.
    """
    metrics: list[PerformanceMetric] = []

    def _m(
        name: str,
        value: float | None,
        segment_type: str = "overall",
        segment_key: str = "all",
    ) -> PerformanceMetric:
        return PerformanceMetric(
            backtest_run_id=backtest_run_id,
            metric_name=name,
            metric_value=value,
            segment_type=segment_type,
            segment_key=segment_key,
        )

    # --- Empty trade set -------------------------------------------------------
    if not trades:
        for name in [
            "total_trades", "win_count", "loss_count", "win_rate",
            "net_pnl", "net_return_pct", "annualized_return_pct",
            "sharpe_ratio", "sortino_ratio", "max_drawdown_pct",
            "avg_win", "avg_loss", "expectancy", "profit_factor",
            "avg_holding_bars",
        ]:
            metrics.append(_m(name, 0.0 if name == "total_trades" else None))
        return metrics

    # --- Trade aggregates ------------------------------------------------------
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / total

    net_pnl = sum(pnls)
    avg_win: float | None = sum(wins) / len(wins) if wins else None
    avg_loss: float | None = sum(losses) / len(losses) if losses else None

    if avg_win is not None and avg_loss is not None:
        expectancy: float | None = win_rate * avg_win + (1.0 - win_rate) * avg_loss
    elif avg_win is not None:
        expectancy = win_rate * avg_win
    else:
        expectancy = None

    gross_wins = sum(wins) if wins else 0.0
    gross_losses = abs(sum(losses)) if losses else 0.0
    profit_factor: float | None = gross_wins / gross_losses if gross_losses > 0 else None

    avg_holding_bars: float | None = (
        sum(t.holding_period for t in trades) / total if total > 0 else None
    )

    # --- Equity-curve stats ----------------------------------------------------
    initial_equity = equity_curve[0] if equity_curve else 100_000.0
    final_equity = equity_curve[-1] if equity_curve else initial_equity
    net_return_pct: float | None = (
        (final_equity - initial_equity) / initial_equity * 100.0
        if initial_equity != 0
        else None
    )
    max_dd_pct = _max_drawdown_pct(equity_curve)
    sharpe = _sharpe_ratio(equity_curve)
    sortino = _sortino_ratio(equity_curve)
    # Annualized return: for MVP, proxy with net_return_pct (no calendar mapping yet).
    annualized = net_return_pct

    # --- Overall metrics -------------------------------------------------------
    metrics.extend([
        _m("total_trades",          float(total)),
        _m("win_count",             float(win_count)),
        _m("loss_count",            float(loss_count)),
        _m("win_rate",              win_rate),
        _m("net_pnl",               net_pnl),
        _m("net_return_pct",        net_return_pct),
        _m("annualized_return_pct", annualized),
        _m("sharpe_ratio",          sharpe),
        _m("sortino_ratio",         sortino),
        _m("max_drawdown_pct",      max_dd_pct),
        _m("avg_win",               avg_win),
        _m("avg_loss",              avg_loss),
        _m("expectancy",            expectancy),
        _m("profit_factor",         profit_factor),
        _m("avg_holding_bars",      avg_holding_bars),
    ])

    # --- Per-regime breakdown --------------------------------------------------
    regime_pnl: dict[str, list[float]] = {}
    for t in trades:
        label = t.regime_at_entry or "unknown"
        regime_pnl.setdefault(label, []).append(t.pnl)

    for label, rpnls in regime_pnl.items():
        metrics.append(PerformanceMetric(
            backtest_run_id=backtest_run_id,
            metric_name="net_pnl",
            metric_value=sum(rpnls),
            segment_type="regime",
            segment_key=label,
        ))
        metrics.append(PerformanceMetric(
            backtest_run_id=backtest_run_id,
            metric_name="trade_count",
            metric_value=float(len(rpnls)),
            segment_type="regime",
            segment_key=label,
        ))

    return metrics


def build_equity_curve(
    trade_log: list[Trade],
    bar_timestamps: list[datetime],
    initial_equity: float = 100_000.0,
) -> tuple[list[float], list[float]]:
    """Build bar-by-bar equity and drawdown series from closed trades.

    Equity at bar T = initial_equity + sum of PnL from all trades closed at or
    before T.  Open (unrealized) PnL is excluded in the MVP.

    Returns
    -------
    equity:   list[float] aligned with bar_timestamps (length == len(bar_timestamps)).
    drawdown: list[float] — fraction in [0, 1]; 0 = at peak, 0.1 = 10% below peak.
    """
    # Accumulate PnL contributions per exit timestamp.
    pnl_by_exit: dict[datetime, float] = {}
    for trade in trade_log:
        if trade.exit_time is not None:
            ts = trade.exit_time
            pnl_by_exit[ts] = pnl_by_exit.get(ts, 0.0) + trade.pnl

    equity: list[float] = []
    drawdown: list[float] = []
    cumulative_pnl = 0.0
    peak = initial_equity

    for ts in bar_timestamps:
        cumulative_pnl += pnl_by_exit.get(ts, 0.0)
        eq = initial_equity + cumulative_pnl
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        equity.append(eq)
        drawdown.append(dd)

    return equity, drawdown


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _max_drawdown_pct(equity_curve: list[float]) -> float | None:
    if len(equity_curve) < 2:
        return None
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    return max_dd * 100.0


def _compute_returns(equity_curve: list[float]) -> list[float] | None:
    """Compute bar-by-bar arithmetic returns from an equity curve.

    Returns None if the curve is too short for meaningful stats (< 3 bars).
    Used by both _sharpe_ratio and _sortino_ratio to avoid duplicated logic.
    """
    if len(equity_curve) < 3:
        return None
    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    return returns if len(returns) >= 2 else None


def _sharpe_ratio(equity_curve: list[float], risk_free: float = 0.0) -> float | None:
    """Compute Sharpe from bar-by-bar equity returns (not annualized in MVP)."""
    returns = _compute_returns(equity_curve)
    if returns is None:
        return None
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r == 0:
        return None
    return (mean_r - risk_free) / std_r


def _sortino_ratio(equity_curve: list[float], risk_free: float = 0.0) -> float | None:
    """Compute Sortino using only downside deviation below risk_free."""
    returns = _compute_returns(equity_curve)
    if returns is None:
        return None
    mean_r = sum(returns) / len(returns)
    downside = [r for r in returns if r < risk_free]
    if not downside:
        return None
    down_var = sum((r - risk_free) ** 2 for r in downside) / len(downside)
    down_std = math.sqrt(down_var) if down_var > 0 else 0.0
    if down_std == 0:
        return None
    return (mean_r - risk_free) / down_std
