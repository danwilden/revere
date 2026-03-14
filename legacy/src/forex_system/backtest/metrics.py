"""
Performance metrics for backtest evaluation.

All functions accept pandas Series inputs and return floats (or dicts).
No side effects. Safe to call from notebooks and Streamlit.
"""

import numpy as np
import pandas as pd


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    """
    Annualized Sharpe ratio.
    `returns` should be period returns (pct_change), not log returns.
    """
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float((returns.mean() / returns.std()) * np.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    """
    Sortino ratio using downside deviation.
    Uses only negative returns for the denominator.
    """
    downside = returns[returns < 0]
    if len(downside) < 2 or downside.std() == 0:
        return 0.0
    return float((returns.mean() / downside.std()) * np.sqrt(periods_per_year))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a negative fraction. e.g. -0.15 = -15%."""
    if len(equity_curve) == 0:
        return 0.0
    peak = equity_curve.expanding().max()
    dd = (equity_curve / peak - 1)
    return float(dd.min())


def max_drawdown_duration(equity_curve: pd.Series) -> int:
    """
    Number of bars in the longest drawdown period (peak → recovery).
    Returns 0 if the equity curve never enters drawdown.
    """
    if len(equity_curve) == 0:
        return 0
    peak = equity_curve.expanding().max()
    in_dd = equity_curve < peak
    if not in_dd.any():
        return 0
    groups = (in_dd != in_dd.shift()).cumsum()
    durations = in_dd.groupby(groups).sum()
    return int(durations.max())


def profit_factor(trade_pnls: pd.Series) -> float:
    """
    Gross profit / gross loss across all trades.
    Returns inf if there are no losing trades.
    """
    if len(trade_pnls) == 0:
        return 0.0
    gross_profit = trade_pnls[trade_pnls > 0].sum()
    gross_loss = abs(trade_pnls[trade_pnls < 0].sum())
    if gross_loss == 0:
        return float("inf")
    return float(gross_profit / gross_loss)


def hit_rate(trade_pnls: pd.Series) -> float:
    """Fraction of trades with positive P&L."""
    if len(trade_pnls) == 0:
        return 0.0
    return float((trade_pnls > 0).sum() / len(trade_pnls))


def payoff_ratio(trade_pnls: pd.Series) -> float:
    """Average win / average loss (absolute value)."""
    wins = trade_pnls[trade_pnls > 0]
    losses = trade_pnls[trade_pnls < 0]
    avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
    if avg_loss == 0:
        return float("inf")
    return avg_win / avg_loss


def cagr(equity_curve: pd.Series, periods_per_year: int = 252) -> float:
    """Compound Annual Growth Rate."""
    n = len(equity_curve)
    if n < 2 or equity_curve.iloc[0] <= 0:
        return 0.0
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0]
    n_years = n / periods_per_year
    return float(total_return ** (1.0 / n_years) - 1)


# Annualization factors (must match engine.PERIODS_PER_YEAR when passing BacktestResult)
_PERIODS_PER_YEAR = {"H1": 8760, "H4": 2190, "D": 252, "W": 52, "M": 12}


def full_tearsheet(
    equity_curve: pd.Series | object,
    trades: pd.DataFrame | None = None,
    periods_per_year: int | None = None,
) -> dict:
    """
    Compute all standard metrics in one call.

    Args:
        equity_curve: Time-indexed Series of account equity values, or a
                      BacktestResult (has .equity_curve, .trades_df(), .granularity).
        trades:       Optional DataFrame with a "pnl" column (per-trade P&L).
                      Ignored if equity_curve is a BacktestResult.
        periods_per_year: Annualization factor (252 for daily, 8760 for H1).
                         Inferred from result.granularity when equity_curve is BacktestResult.

    Returns:
        Dict of metric name → float.
    """
    # Accept BacktestResult so callers can pass result directly
    if hasattr(equity_curve, "equity_curve") and hasattr(equity_curve, "trades_df"):
        obj = equity_curve
        equity_curve = obj.equity_curve
        trades = obj.trades_df() if obj.trades else None
        periods_per_year = periods_per_year or _PERIODS_PER_YEAR.get(
            getattr(obj, "granularity", "D"), 252
        )
    if periods_per_year is None:
        periods_per_year = 252

    returns = equity_curve.pct_change().dropna()

    report: dict = {
        "cagr": cagr(equity_curve, periods_per_year),
        "total_return": float(
            equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
        ) if len(equity_curve) >= 2 else 0.0,
        "sharpe": sharpe_ratio(returns, periods_per_year),
        "sortino": sortino_ratio(returns, periods_per_year),
        "max_drawdown": max_drawdown(equity_curve),
        "max_dd_duration_bars": max_drawdown_duration(equity_curve),
        "n_periods": len(equity_curve),
    }

    pnl_series = (
        trades["pnl"]
        if trades is not None and not trades.empty and "pnl" in trades.columns
        else returns
    )

    report["profit_factor"] = profit_factor(pnl_series)
    report["hit_rate"] = hit_rate(pnl_series)
    report["payoff_ratio"] = payoff_ratio(pnl_series)

    if trades is not None and not trades.empty:
        report["n_trades"] = len(trades)

    # Extended metrics for Phase 0/1 pivot reporting
    if trades is not None and not trades.empty and "pnl_pips" in trades.columns:
        wins   = trades[trades["pnl_pips"] > 0]["pnl_pips"]
        losses = trades[trades["pnl_pips"] < 0]["pnl_pips"]
        report["avg_win_pips"]  = float(wins.mean())        if len(wins)   > 0 else 0.0
        report["avg_loss_pips"] = float(abs(losses.mean())) if len(losses) > 0 else 0.0

    if trades is not None and not trades.empty and "exit_reason" in trades.columns:
        n = len(trades)
        report["signal_exit_hr"] = float((trades["exit_reason"] == "signal_exit").sum() / n)
        report["stop_hit_hr"]    = float((trades["exit_reason"] == "stop_hit").sum() / n)

    return report
