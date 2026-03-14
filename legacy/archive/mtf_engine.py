"""
Multi-Timeframe Backtester — Phase 1E.

Architecture:
    H4 bars: regime classification, entry signals, structural stop (unchanged).
    H1 bars: all intra-trade management (partial exit, trail, stop checks).

Execution model:
    - H4 signal at bar t close → entry at bar t+1 open (same as VectorizedBacktester)
    - Structural stop: set at H4 entry, acts as hard floor
    - ATR reference: H4 ATR at entry bar, fixed for entire trade
    - Trail and partial exit: recalculated on every H1 bar close
    - H1 bars within the H4 entry bar are excluded (bar alignment rule)
    - Signal exits: set exit_pending=True; H1 manager completes current H4
      candle's remaining H1 bars before enforcing the flip

MINIMUM_HOLD_BARS: counted in H4 bars (not H1). 5 H4 bars = 20 hours.

Output: identical BacktestResult schema to VectorizedBacktester, plus
        avg_h1_bars_in_trade in the metrics dict (diagnostic only).

Usage:
    from forex_system.backtest.mtf_engine import MultiTimeframeBacktester
    from forex_system.backtest.costs import CostModel

    bt = MultiTimeframeBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("USD_JPY", signal_df, h1_df)
    print(result.metrics)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import BacktestResult, Trade, PERIODS_PER_YEAR
from forex_system.backtest.metrics import full_tearsheet
from forex_system.data.instruments import registry

# Minimum H4 bars a position must be held before a signal-exit is permitted.
# Stop hits and trail fires on H1 are unaffected — they can close any time.
MINIMUM_HOLD_BARS: int = 5


@dataclass
class ExitEvent:
    """Emitted by H1TradeManager.update() when an exit condition is met."""
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str    # "partial_tp" | "stop_hit" | "signal_exit" | "end_of_data"
    units: int
    is_partial: bool    # True → trade continues with reduced units_remaining


class H1TradeManager:
    """
    Manages an open trade on H1 bars using fixed H4 ATR as the reference unit.

    Instantiated once at trade entry; update() is called for each H1 bar
    until the trade exits.

    Args:
        entry_price:           Fill price at H4 entry bar open.
        direction:             1 = long, -1 = short.
        stop_price:            Initial structural stop (hard floor; only tightens).
        atr_h4:                H4 ATR captured at entry bar — never recalculated.
        units:                 Position size in units.
        partial_exit_atr_mult: Close partial_exit_fraction when profit > this × atr_h4.
        partial_exit_fraction: Fraction of full units closed at Stage 1.
        trail_atr_mult:        Trail distance for remainder (× atr_h4, not H1 ATR).
    """

    def __init__(
        self,
        entry_price: float,
        direction: int,
        stop_price: float,
        atr_h4: float,
        units: int,
        partial_exit_atr_mult: float = 1.5,
        partial_exit_fraction: float = 0.33,
        trail_atr_mult: float = 1.5,
    ) -> None:
        self.entry_price = entry_price
        self.direction = direction
        self.stop_price = stop_price
        self.atr_h4 = atr_h4
        self.partial_exit_atr_mult = partial_exit_atr_mult
        self.partial_exit_fraction = partial_exit_fraction
        self.trail_atr_mult = trail_atr_mult

        self.units_full: int = units
        self.units_remaining: int = units
        self.partial_done: bool = False
        self.trail_activated: bool = False
        self.trail_best_close: float = entry_price
        self.h1_bars_count: int = 0

    def update(
        self,
        h1_time: pd.Timestamp,
        h1_high: float,
        h1_low: float,
        h1_close: float,
    ) -> ExitEvent | None:
        """
        Process one H1 bar. Returns ExitEvent if exit triggered, else None.

        Order of checks (matches conservative backtest convention):
            1. Intrabar stop check (low/high vs stop_price)
            2. Partial exit at H1 close (Stage 1)
            3. Trail stop update at H1 close (Stage 2)
        """
        self.h1_bars_count += 1

        # ── 1. Intrabar stop check ─────────────────────────────────────────
        if self.direction == 1 and h1_low <= self.stop_price:
            return ExitEvent(
                exit_time=h1_time,
                exit_price=self.stop_price,
                exit_reason="stop_hit",
                units=self.units_remaining,
                is_partial=False,
            )
        if self.direction == -1 and h1_high >= self.stop_price:
            return ExitEvent(
                exit_time=h1_time,
                exit_price=self.stop_price,
                exit_reason="stop_hit",
                units=self.units_remaining,
                is_partial=False,
            )

        # ── 2. Partial exit at H1 close (Stage 1) ─────────────────────────
        profit = (h1_close - self.entry_price) * self.direction
        if not self.partial_done and profit > self.partial_exit_atr_mult * self.atr_h4:
            partial_units = max(1, int(self.units_full * self.partial_exit_fraction))
            self.units_remaining = self.units_full - partial_units
            self.partial_done = True
            self.trail_activated = True
            self.trail_best_close = h1_close
            return ExitEvent(
                exit_time=h1_time,
                exit_price=h1_close,
                exit_reason="partial_tp",
                units=partial_units,
                is_partial=True,
            )

        # ── 3. Trail stop update at H1 close (Stage 2) ────────────────────
        # Uses H4 ATR fixed at entry — never H1 ATR (which would shrink in
        # low-volatility hours and clip the remainder prematurely).
        if self.trail_activated:
            if self.direction == 1:
                self.trail_best_close = max(self.trail_best_close, h1_close)
                trail_stop = self.trail_best_close - self.trail_atr_mult * self.atr_h4
                self.stop_price = max(self.stop_price, trail_stop)  # only tighten
            else:
                self.trail_best_close = min(self.trail_best_close, h1_close)
                trail_stop = self.trail_best_close + self.trail_atr_mult * self.atr_h4
                self.stop_price = min(self.stop_price, trail_stop)  # only tighten

        return None


class MultiTimeframeBacktester:
    """
    Multi-timeframe backtester: H4 signals, H1 trade management.

    H4 VectorizedBacktester is left completely unchanged — this is an
    independent implementation for comparison and promotion to paper trading.

    Args:
        initial_equity:        Starting account equity in USD.
        cost_model:            CostModel instance for spread + slippage.
        partial_exit_atr_mult: H1TradeManager param — partial at +N×H4_ATR profit.
        partial_exit_fraction: H1TradeManager param — fraction closed at Stage 1.
        trail_atr_mult:        H1TradeManager param — trail distance in H4 ATR units.
    """

    def __init__(
        self,
        initial_equity: float = 10_000.0,
        cost_model: CostModel | None = None,
        partial_exit_atr_mult: float = 1.5,
        partial_exit_fraction: float = 0.33,
        trail_atr_mult: float = 1.5,
    ) -> None:
        self.initial_equity = initial_equity
        self.cost_model = cost_model or CostModel()
        self.partial_exit_atr_mult = partial_exit_atr_mult
        self.partial_exit_fraction = partial_exit_fraction
        self.trail_atr_mult = trail_atr_mult

    def run(
        self,
        instrument: str,
        signal_df: pd.DataFrame,
        h1_df: pd.DataFrame,
    ) -> BacktestResult:
        """
        Run multi-timeframe backtest for a single instrument.

        Args:
            instrument: e.g. "USD_JPY"
            signal_df:  H4 signal DataFrame from RegimeRouter.route() with columns:
                            open, high, low, close (float)
                            signal (int: 1, -1, 0)
                            stop_distance (float, price units)
                            atr (float, H4 ATR at each bar)
                            units (int, always positive)
                        DatetimeIndex UTC (H4 bar open times).
            h1_df:      Raw H1 OHLCV DataFrame from CandleFetcher with columns:
                            open, high, low, close, volume
                        DatetimeIndex UTC (H1 bar open times). No features needed.

        Returns:
            BacktestResult with equity curve, trade list, and metrics dict.
            metrics dict contains all VectorizedBacktester keys plus
            "avg_h1_bars_in_trade" (diagnostic).
        """
        meta = registry.get(instrument)
        pip_size = meta.pip_size

        from forex_system.risk.sizing import pip_value_per_unit_usd

        # ── Prepare H4 signal data ─────────────────────────────────────────
        df = signal_df.reset_index()
        if "time" not in df.columns:
            df = df.rename(columns={df.columns[0]: "time"})

        n_h4 = len(df)
        h4_times: list[pd.Timestamp] = list(df["time"])

        # ── Pre-build H1 slice index (O(log n) per H4 bar) ────────────────
        h1_df_sorted = h1_df.sort_index()
        if "complete" in h1_df_sorted.columns:
            h1_df_sorted = h1_df_sorted[h1_df_sorted["complete"]].copy()

        h1_times_ns = h1_df_sorted.index.asi8           # nanoseconds for searchsorted
        h4_times_ns = np.array([t.value for t in h4_times], dtype=np.int64)

        # h1_slices[i] → H1 sub-DataFrame for H4 bar i
        h1_slices: list[pd.DataFrame] = []
        for i in range(n_h4):
            lo = int(np.searchsorted(h1_times_ns, h4_times_ns[i], side="left"))
            if i + 1 < n_h4:
                hi = int(np.searchsorted(h1_times_ns, h4_times_ns[i + 1], side="left"))
            else:
                hi = len(h1_df_sorted)
            h1_slices.append(h1_df_sorted.iloc[lo:hi])

        # ── State ─────────────────────────────────────────────────────────
        equity = self.initial_equity
        equity_records: list[dict] = []
        trades: list[Trade] = []
        h1_bars_per_trade: list[int] = []

        position: int = 0
        units_held: int = 0
        entry_price: float = 0.0
        entry_time: pd.Timestamp | None = None
        bars_held_h4: int = 0
        exit_pending: bool = False
        partial_done_outer: bool = False  # mirrors trade_manager.partial_done for cost calc
        trade_manager: H1TradeManager | None = None

        cost_pips = self.cost_model.total_cost_pips(instrument)

        # ── Main H4 loop ───────────────────────────────────────────────────
        for h4_idx in range(1, n_h4):
            prev = df.iloc[h4_idx - 1]
            curr = df.iloc[h4_idx]
            curr_close = float(curr["close"])
            pip_val = pip_value_per_unit_usd(instrument, curr_close)

            new_signal = int(prev["signal"])

            # ── 1. Signal flip detection (H4 boundary) ────────────────────
            # Blocked until MINIMUM_HOLD_BARS H4 bars have elapsed.
            # Stop hits and trail fires on H1 are always allowed.
            if (
                position != 0
                and new_signal != position
                and bars_held_h4 >= MINIMUM_HOLD_BARS
                and not exit_pending
            ):
                exit_pending = True

            # ── 2. H1 trade management ────────────────────────────────────
            # Bar alignment rule: skip H1 management on the entry bar itself
            # (bars_held_h4 == 0 means this is the entry bar).
            if position != 0 and bars_held_h4 >= 1 and trade_manager is not None:
                h1_slice = h1_slices[h4_idx]
                h1_highs = h1_slice["high"].to_numpy(dtype=float)
                h1_lows = h1_slice["low"].to_numpy(dtype=float)
                h1_closes = h1_slice["close"].to_numpy(dtype=float)
                h1_times_slice = h1_slice.index

                for j in range(len(h1_slice)):
                    evt = trade_manager.update(
                        h1_time=h1_times_slice[j],
                        h1_high=h1_highs[j],
                        h1_low=h1_lows[j],
                        h1_close=h1_closes[j],
                    )
                    if evt is None:
                        continue

                    if evt.is_partial:
                        # Stage 1 partial exit — trade continues with remainder
                        frac = evt.units / max(units_held, 1)
                        pip_val_p = pip_value_per_unit_usd(instrument, evt.exit_price)
                        if position == 1:
                            pnl_pips = (evt.exit_price - entry_price) / pip_size - cost_pips * frac
                        else:
                            pnl_pips = (entry_price - evt.exit_price) / pip_size - cost_pips * frac
                        pnl = pnl_pips * pip_val_p * evt.units
                        equity += pnl
                        trades.append(
                            Trade(
                                instrument=instrument,
                                entry_time=entry_time,           # type: ignore[arg-type]
                                exit_time=evt.exit_time,
                                direction=position,
                                units=evt.units,
                                entry_price=entry_price,
                                exit_price=evt.exit_price,
                                stop_price=trade_manager.stop_price,
                                pnl=pnl,
                                pnl_pips=pnl_pips,
                                exit_reason="partial_tp",
                            )
                        )
                        units_held = trade_manager.units_remaining
                        partial_done_outer = True
                        # Continue iterating H1 bars for remainder management

                    else:
                        # Full exit: stop hit
                        rem_frac = (
                            (1.0 - self.partial_exit_fraction)
                            if partial_done_outer
                            else 1.0
                        )
                        pip_val_e = pip_value_per_unit_usd(instrument, evt.exit_price)
                        if position == 1:
                            pnl_pips = (evt.exit_price - entry_price) / pip_size - cost_pips * rem_frac
                        else:
                            pnl_pips = (entry_price - evt.exit_price) / pip_size - cost_pips * rem_frac
                        pnl = pnl_pips * pip_val_e * evt.units
                        equity += pnl
                        trades.append(
                            Trade(
                                instrument=instrument,
                                entry_time=entry_time,           # type: ignore[arg-type]
                                exit_time=evt.exit_time,
                                direction=position,
                                units=evt.units,
                                entry_price=entry_price,
                                exit_price=evt.exit_price,
                                stop_price=trade_manager.stop_price,
                                pnl=pnl,
                                pnl_pips=pnl_pips,
                                exit_reason=evt.exit_reason,
                            )
                        )
                        h1_bars_per_trade.append(trade_manager.h1_bars_count)
                        position = 0
                        units_held = 0
                        bars_held_h4 = 0
                        exit_pending = False
                        partial_done_outer = False
                        trade_manager = None
                        break

                # After H1 slice: enforce exit_pending if trade still open
                if exit_pending and position != 0:
                    if len(h1_slice) > 0:
                        exit_price_sig = float(h1_closes[-1])
                        exit_time_sig = h1_times_slice[-1]
                    else:
                        # No H1 bars in this candle → fall back to H4 close
                        exit_price_sig = curr_close
                        exit_time_sig = curr["time"]

                    rem_frac = (
                        (1.0 - self.partial_exit_fraction)
                        if partial_done_outer
                        else 1.0
                    )
                    pip_val_s = pip_value_per_unit_usd(instrument, exit_price_sig)
                    if position == 1:
                        pnl_pips = (exit_price_sig - entry_price) / pip_size - cost_pips * rem_frac
                    else:
                        pnl_pips = (entry_price - exit_price_sig) / pip_size - cost_pips * rem_frac
                    pnl = pnl_pips * pip_val_s * units_held
                    equity += pnl
                    trades.append(
                        Trade(
                            instrument=instrument,
                            entry_time=entry_time,           # type: ignore[arg-type]
                            exit_time=exit_time_sig,
                            direction=position,
                            units=units_held,
                            entry_price=entry_price,
                            exit_price=exit_price_sig,
                            stop_price=trade_manager.stop_price if trade_manager else 0.0,
                            pnl=pnl,
                            pnl_pips=pnl_pips,
                            exit_reason="signal_exit",
                        )
                    )
                    if trade_manager is not None:
                        h1_bars_per_trade.append(trade_manager.h1_bars_count)
                    position = 0
                    units_held = 0
                    bars_held_h4 = 0
                    exit_pending = False
                    partial_done_outer = False
                    trade_manager = None

            # ── 3. New entry ───────────────────────────────────────────────
            if position == 0 and new_signal != 0:
                entry_price = float(curr["open"])
                position = new_signal
                units_held = int(prev["units"])
                stop_distance = float(prev["stop_distance"])
                initial_stop = (
                    entry_price - stop_distance
                    if position == 1
                    else entry_price + stop_distance
                )
                atr_h4 = float(prev.get("atr", stop_distance / 2.0))
                entry_time = curr["time"]
                bars_held_h4 = 0
                exit_pending = False
                partial_done_outer = False
                trade_manager = H1TradeManager(
                    entry_price=entry_price,
                    direction=position,
                    stop_price=initial_stop,
                    atr_h4=atr_h4,
                    units=units_held,
                    partial_exit_atr_mult=self.partial_exit_atr_mult,
                    partial_exit_fraction=self.partial_exit_fraction,
                    trail_atr_mult=self.trail_atr_mult,
                )

            # ── 4. Equity recording + H4 bar counter ───────────────────────
            equity_records.append({"time": curr["time"], "equity": equity})
            if position != 0:
                bars_held_h4 += 1

        # ── Close any open position at end of data ─────────────────────────
        if position != 0 and len(df) > 0:
            last = df.iloc[-1]
            # Prefer last available H1 bar
            last_h1_slice = h1_slices[-1]
            if len(last_h1_slice) > 0:
                exit_price_eod = float(last_h1_slice["close"].iloc[-1])
                exit_time_eod = last_h1_slice.index[-1]
            else:
                exit_price_eod = float(last["close"])
                exit_time_eod = last["time"]

            pip_val_eod = pip_value_per_unit_usd(instrument, exit_price_eod)
            rem_frac = (1.0 - self.partial_exit_fraction) if partial_done_outer else 1.0
            if position == 1:
                pnl_pips = (exit_price_eod - entry_price) / pip_size - cost_pips * rem_frac
            else:
                pnl_pips = (entry_price - exit_price_eod) / pip_size - cost_pips * rem_frac
            pnl = pnl_pips * pip_val_eod * units_held
            equity += pnl
            trades.append(
                Trade(
                    instrument=instrument,
                    entry_time=entry_time,           # type: ignore[arg-type]
                    exit_time=exit_time_eod,
                    direction=position,
                    units=units_held,
                    entry_price=entry_price,
                    exit_price=exit_price_eod,
                    stop_price=trade_manager.stop_price if trade_manager else 0.0,
                    pnl=pnl,
                    pnl_pips=pnl_pips,
                    exit_reason="end_of_data",
                )
            )
            if trade_manager is not None:
                h1_bars_per_trade.append(trade_manager.h1_bars_count)

        # ── Build equity curve and metrics ─────────────────────────────────
        if not equity_records:
            eq_series = pd.Series([self.initial_equity], name="equity", dtype=float)
        else:
            eq_df = pd.DataFrame(equity_records)
            eq_series = eq_df.set_index("time")["equity"]

        trades_df = (
            pd.DataFrame([t.__dict__ for t in trades])
            if trades
            else pd.DataFrame()
        )
        ppy = PERIODS_PER_YEAR.get("H4", 2190)
        metrics = full_tearsheet(eq_series, trades_df if not trades_df.empty else None, ppy)

        # Diagnostic: average H1 bars managed per completed trade
        metrics["avg_h1_bars_in_trade"] = (
            float(np.mean(h1_bars_per_trade)) if h1_bars_per_trade else 0.0
        )

        logger.info(
            f"MTF Backtest: {instrument} | "
            f"trades={len(trades)} | "
            f"sharpe={metrics['sharpe']:.2f} | "
            f"maxDD={metrics['max_drawdown']:.2%} | "
            f"CAGR={metrics['cagr']:.2%} | "
            f"avg_h1_bars={metrics['avg_h1_bars_in_trade']:.1f}"
        )

        return BacktestResult(
            instrument=instrument,
            granularity="H4_H1",
            equity_curve=eq_series,
            trades=trades,
            metrics=metrics,
        )
