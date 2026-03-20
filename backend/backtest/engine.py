"""Event-driven backtesting engine.

Processing order for each bar (non-negotiable — must not change):
  1. Check stop-loss / take-profit hit using bar high/low.
     If hit: close the position at the stop/target price, start the cooldown
     timer, and skip to the next bar (do not call on_bar for this bar).
  2. Call strategy.on_bar() — which internally:
       a. Checks for a strategy-signal exit (if positioned).
       b. Enforces the cooldown standoff period.
       c. Evaluates entry conditions (if flat and outside cooldown).
  3. Execute the returned ActionDict: open a trade, close a trade, or hold.

No lookahead: only bar[t] data is visible at iteration step t.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.backtest.costs import CostModel
from backend.backtest.fills import check_stop_target, compute_entry_fill, compute_exit_fill
from backend.backtest.metrics import build_equity_curve, compute_metrics
from backend.schemas.enums import TradeSide
from backend.schemas.models import BacktestRun, PerformanceMetric, Trade
from backend.strategies.base import BaseStrategy
from backend.strategies.state import StrategyState


def run_backtest(
    strategy: BaseStrategy,
    bars: list[dict],
    backtest_run: BacktestRun,
    cost_model: CostModel,
    initial_equity: float = 100_000.0,
    params: dict[str, Any] | None = None,
) -> tuple[list[Trade], list[PerformanceMetric], list[float], list[float]]:
    """Run an event-driven backtest over an ordered list of bars.

    Parameters
    ----------
    strategy:
        Instantiated BaseStrategy (RulesStrategy, CodeStrategy, or custom).
    bars:
        Ordered list of bar dicts from data_loader.load_backtest_frame().
        Required keys: timestamp_utc, open, high, low, close.
        Optional: any feature columns and 'regime_label'.
    backtest_run:
        BacktestRun metadata record — its id is used as FK on Trade records.
    cost_model:
        CostModel with spread / slippage / commission parameters.
    initial_equity:
        Starting account equity (account currency units).
    params:
        Strategy-level parameters forwarded to on_bar():
          stop_atr_multiplier, take_profit_atr_multiplier,
          position_size_units, cooldown_hours, etc.

    Returns
    -------
    trades:    list of closed Trade records.
    metrics:   list of PerformanceMetric records.
    equity:    bar-by-bar equity curve (same length as bars).
    drawdown:  bar-by-bar drawdown curve (same length as bars, fractions).
    """
    params = params or {}

    # Fresh state — reset() clears open_position, last_exit_time, bar_count, entry_bar_idx.
    # cooldown_hours is a configuration parameter (not reset).
    state = StrategyState(cooldown_hours=params.get("cooldown_hours", 0.0))
    state.reset()

    trades: list[Trade] = []
    open_trade_record: Trade | None = None  # pending Trade; exit fields filled at close

    for bar_idx, bar in enumerate(bars):
        ts: datetime = bar["timestamp_utc"]

        # Keep state regime in sync with the merged regime label on the bar.
        if bar.get("regime_label"):
            state.current_regime = bar["regime_label"]

        features = _extract_features(bar)

        # -------------------------------------------------------------------
        # 1. Stop / take-profit check (engine-level, before strategy sees bar)
        # -------------------------------------------------------------------
        if state.open_position is not None:
            pos = state.open_position
            fill = check_stop_target(
                bar=bar,
                side=pos["side"],
                stop=pos.get("stop"),
                target=pos.get("target"),
            )
            if fill.hit:
                # Capture entry_bar_idx BEFORE close_trade() resets it to -1.
                holding_period = bar_idx - state.entry_bar_idx
                state.close_trade(exit_time=ts)
                trade = _finalize_trade(
                    record=open_trade_record,
                    exit_time=ts,
                    exit_price=fill.exit_price,
                    exit_reason=fill.exit_reason,
                    regime_at_exit=state.current_regime,
                    holding_period=holding_period,
                    cost_model=cost_model,
                )
                trades.append(trade)
                open_trade_record = None
                # Do not call on_bar on the same bar a stop/target was hit.
                continue

        # -------------------------------------------------------------------
        # Inject trade lifecycle context into bar dict for strategy/DSL consumption.
        # bars_in_trade and minutes_in_trade are available as DSL field references.
        # -------------------------------------------------------------------
        if state.open_position is not None:
            bar["bars_in_trade"] = bar_idx - state.entry_bar_idx
            bar["minutes_in_trade"] = (ts - state.open_position["entry_time"]).total_seconds() / 60.0
            bar["days_in_trade"] = bar["minutes_in_trade"] / 1440.0
        else:
            bar["bars_in_trade"] = 0
            bar["minutes_in_trade"] = 0.0
            bar["days_in_trade"] = 0.0

        # -------------------------------------------------------------------
        # 2. Strategy decision
        # -------------------------------------------------------------------
        current_equity = initial_equity + sum(t.pnl for t in trades)
        action = strategy.on_bar(
            bar=bar,
            features=features,
            state=state,
            equity=current_equity,
            params=params,
        )
        act = action.get("action")

        # -------------------------------------------------------------------
        # 3. Execute action
        # -------------------------------------------------------------------
        if act == "exit" and state.open_position is not None:
            pos = state.open_position
            exit_price = compute_exit_fill(bar, pos["side"], cost_model)
            # Capture entry_bar_idx BEFORE close_trade() resets it to -1.
            holding_period = bar_idx - state.entry_bar_idx
            state.close_trade(exit_time=ts)
            trade = _finalize_trade(
                record=open_trade_record,
                exit_time=ts,
                exit_price=exit_price,
                exit_reason=action.get("reason", "strategy_signal"),
                regime_at_exit=state.current_regime,
                holding_period=holding_period,
                cost_model=cost_model,
            )
            trades.append(trade)
            open_trade_record = None

        elif act in ("enter_long", "enter_short"):
            side = "long" if act == "enter_long" else "short"
            qty = abs(action.get("quantity", 10_000.0))
            stop = action.get("stop")
            target = action.get("target")
            entry_price = compute_entry_fill(bar, side, cost_model)

            state.open_trade(
                side=side,
                entry_time=ts,
                entry_price=entry_price,
                quantity=qty,
                stop=stop,
                target=target,
                reason=action.get("reason", ""),
                bar_idx=bar_idx,
            )
            open_trade_record = Trade(
                backtest_run_id=backtest_run.id,
                instrument_id=backtest_run.instrument_id,
                entry_time=ts,
                side=TradeSide(side),
                quantity=qty,
                entry_price=entry_price,
                stop_price=stop,
                target_price=target,
                entry_reason=action.get("reason", ""),
                regime_at_entry=state.current_regime,
            )

    # -------------------------------------------------------------------
    # 4. Force-close any position still open at the end of the backtest
    # -------------------------------------------------------------------
    if state.open_position is not None and bars:
        last_bar = bars[-1]
        ts = last_bar["timestamp_utc"]
        pos = state.open_position
        exit_price = compute_exit_fill(last_bar, pos["side"], cost_model)
        # Capture entry_bar_idx BEFORE close_trade() resets it to -1.
        holding_period = len(bars) - 1 - state.entry_bar_idx
        state.close_trade(exit_time=ts)
        trade = _finalize_trade(
            record=open_trade_record,
            exit_time=ts,
            exit_price=exit_price,
            exit_reason="end_of_backtest",
            regime_at_exit=state.current_regime,
            holding_period=holding_period,
            cost_model=cost_model,
        )
        trades.append(trade)

    # -------------------------------------------------------------------
    # 5. Build equity / drawdown curves and compute metrics
    # -------------------------------------------------------------------
    bar_timestamps = [b["timestamp_utc"] for b in bars]
    equity, drawdown = build_equity_curve(trades, bar_timestamps, initial_equity)
    metrics = compute_metrics(trades, equity, backtest_run.id)

    return trades, metrics, equity, drawdown


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_features(bar: dict) -> dict:
    """Return feature columns from a merged bar dict.

    Feature columns are any key not in the standard bar schema.  This dict
    is passed as `features` to strategy.on_bar() so strategies can read any
    computed feature without knowing what columns exist in the bar.
    """
    _bar_keys = frozenset({
        "instrument_id", "timestamp_utc", "timestamp",
        "open", "high", "low", "close", "volume",
        "source", "quality_flag", "timeframe", "derivation_version",
        "regime_label", "state_id",
        # Lifecycle markers injected by the engine — not computed features.
        "bars_in_trade", "minutes_in_trade", "days_in_trade",
    })
    return {k: v for k, v in bar.items() if k not in _bar_keys}


def _finalize_trade(
    record: Trade | None,
    exit_time: datetime,
    exit_price: float,
    exit_reason: str,
    regime_at_exit: str,
    holding_period: int,
    cost_model: CostModel,
) -> Trade:
    """Populate the exit fields on a pending Trade record and compute net PnL.

    PnL = (price delta × quantity) − round-trip commission.

    Stop/target fills pass the exact stop/target as exit_price — no additional
    spread adjustment is applied because stops/targets are treated as limit
    executions that already reflect the bid/ask level.
    """
    if record is None:
        raise RuntimeError("_finalize_trade called with no open trade record")

    side = record.side.value  # "long" or "short"
    qty = record.quantity
    entry_price = record.entry_price

    # Long: profit when exit > entry. Short: profit when entry > exit.
    if side == "long":
        raw_pnl = (exit_price - entry_price) * qty
    else:
        raw_pnl = (entry_price - exit_price) * qty

    commission = cost_model.commission_cost(qty)
    net_pnl = raw_pnl - commission
    pnl_pct = (
        (net_pnl / (entry_price * qty)) * 100.0
        if (entry_price * qty) != 0
        else 0.0
    )

    # Pydantic v2 models are mutable by default (no frozen config on Trade).
    record.exit_time = exit_time
    record.exit_price = exit_price
    record.pnl = net_pnl
    record.pnl_pct = pnl_pct
    record.exit_reason = exit_reason
    record.regime_at_exit = regime_at_exit
    record.holding_period = max(holding_period, 0)
    return record
