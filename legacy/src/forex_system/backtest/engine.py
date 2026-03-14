"""
Vectorized bar-by-bar backtester.

Execution model:
    - Signal generated at bar t close
    - Entry executed at bar t+1 open  (cost applied on entry)
    - Stop checked against bar's high/low on every bar after entry
    - Exit when signal flips or stop is hit; cost applied on exit too
    - One position per instrument at a time

Usage:
    from forex_system.backtest.engine import VectorizedBacktester
    from forex_system.backtest.costs import CostModel

    bt = VectorizedBacktester(initial_equity=10_000, cost_model=CostModel())
    result = bt.run("EUR_USD", "H1", signal_df)
    print(result.metrics)
    result.equity_curve.plot()
"""

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from forex_system.backtest.costs import CostModel
from forex_system.backtest.metrics import full_tearsheet
from forex_system.data.instruments import registry

# CHANGE 8 (v2.5): Minimum bars a position must be held before a signal-exit is
# permitted.  Stop hits and trail stops are unaffected — they can close any time.
MINIMUM_HOLD_BARS: int = 5


@dataclass
class Trade:
    instrument: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int            # 1=long, -1=short
    units: int
    entry_price: float
    exit_price: float
    stop_price: float
    pnl: float                # in account currency (USD)
    pnl_pips: float
    exit_reason: str          # "stop_hit" | "signal_exit" | "profit_target" | "time_stop" | "end_of_data"
    bars_held: int = 0        # bars position was held at exit
    atr_at_entry: float = 0.0 # ATR captured at entry bar


@dataclass
class BacktestResult:
    instrument: str
    granularity: str
    equity_curve: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([t.__dict__ for t in self.trades])


# Annualization factors per granularity
PERIODS_PER_YEAR = {
    "H1": 8760,
    "H4": 2190,
    "D": 252,
    "W": 52,
    "M": 12,
}


class VectorizedBacktester:
    """
    Bar-by-bar portfolio backtest for a single instrument.

    Args:
        initial_equity: Starting account equity in USD.
        cost_model:     CostModel instance for spread + slippage.
    """

    def __init__(
        self,
        initial_equity: float = 10_000.0,
        cost_model: CostModel | None = None,
        # Phase 1A: two-stage exit (replaces old trail stop when enabled)
        two_stage_exit: bool = False,
        partial_exit_atr_mult: float = 1.0,    # take 50% at +1.0×ATR profit
        partial_exit_fraction: float = 0.5,    # fraction of position to close at Stage 1
        trail_atr_mult_remainder: float = 0.5, # trail distance for remaining position
        # Exit controls (exit-only tests; defaults preserve existing behaviour)
        trail_enabled: bool = True,            # set False to disable trail/two-stage completely
        trail_activate_atr_mult: float = 1.5,  # was hardcoded; parameterised for TEST B
        trail_distance_atr_mult: float = 1.0,  # was hardcoded; parameterised for TEST B
        profit_target_atr_mult: float | None = None,  # None = disabled (TEST A)
        time_stop_bars: int | None = None,     # None = disabled (TEST C)
        time_stop_min_profit_atr: float = 1.0, # profit ATR threshold for time stop
    ) -> None:
        self.initial_equity            = initial_equity
        self.cost_model                = cost_model or CostModel()
        self.two_stage_exit            = two_stage_exit
        self.partial_exit_atr_mult     = partial_exit_atr_mult
        self.partial_exit_fraction     = partial_exit_fraction
        self.trail_atr_mult_remainder  = trail_atr_mult_remainder
        self.trail_enabled             = trail_enabled
        self.trail_activate_atr_mult   = trail_activate_atr_mult
        self.trail_distance_atr_mult   = trail_distance_atr_mult
        self.profit_target_atr_mult    = profit_target_atr_mult
        self.time_stop_bars            = time_stop_bars
        self.time_stop_min_profit_atr  = time_stop_min_profit_atr

    def run(
        self,
        instrument: str,
        granularity: str,
        signal_df: pd.DataFrame,
    ) -> BacktestResult:
        """
        Run backtest for a single instrument.

        Args:
            instrument:  e.g. "EUR_USD"
            granularity: e.g. "H1", "H4", "D"
            signal_df:   DataFrame (DatetimeIndex) with columns:
                            open, high, low, close (float)
                            signal (int: 1, -1, 0)
                            stop_distance (float, price units)
                            units (int, always positive)

        Returns:
            BacktestResult with equity curve, trade list, and metrics dict.
        """
        meta = registry.get(instrument)
        pip_size = meta.pip_size

        # pip_value per unit (used for P&L in USD)
        from forex_system.risk.sizing import pip_value_per_unit_usd

        df = signal_df.reset_index()  # preserve time as a column
        if "time" not in df.columns:
            df = df.rename(columns={df.columns[0]: "time"})

        n = len(df)
        equity = self.initial_equity
        equity_records: list[dict] = []
        trades: list[Trade] = []

        # Position state
        position: int = 0
        units_held: int = 0
        entry_price: float = 0.0
        stop_price: float = 0.0
        entry_time: pd.Timestamp | None = None
        bars_held: int = 0  # CHANGE 8: bars since position entry
        cost_pips = self.cost_model.total_cost_pips(instrument)

        # CHANGE 2 (v2.4): Trail stop state.
        # Activated when unrealised profit > trail_activate_atr_mult×ATR at entry.
        # Once active, the stop trails trail_distance_atr_mult×ATR behind the most
        # favourable close, locking in partial profit without cutting winners prematurely.
        # Trail stop is only enabled when signal_df contains an "atr" column
        # (added by RegimeRouter). Existing tests without "atr" are unaffected.
        # trail_enabled=False disables all trail/two-stage logic (used for clean baselines).
        has_atr_col: bool = "atr" in df.columns
        atr_at_entry: float = 0.0
        trail_activated: bool = False
        trail_best_close: float = 0.0

        # Phase 1A: two-stage exit state (only used when self.two_stage_exit=True)
        # When enabled, replaces the old trail logic with a two-stage exit:
        #   Stage 1: close partial_exit_fraction at +partial_exit_atr_mult×ATR
        #   Stage 2: trail remaining position with trail_atr_mult_remainder×ATR immediately
        partial_exit_done: bool = False
        full_units: int = 0   # original units at entry; used to compute partial and remainder

        # Exit-test state
        max_profit_atr_multiple: float = 0.0  # peak unrealised profit in ATR units (time stop)

        for i in range(1, n):
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            curr_close = float(curr["close"])

            pip_val = pip_value_per_unit_usd(instrument, curr_close)

            # ── Track peak profit for time-stop ─────────────────────────────
            if position != 0 and has_atr_col and atr_at_entry > 0:
                _unrealized = (curr_close - entry_price) * position
                _profit_atr = _unrealized / atr_at_entry
                if _profit_atr > max_profit_atr_multiple:
                    max_profit_atr_multiple = _profit_atr

            # ── Trail / two-stage exit logic ────────────────────────────────
            if position != 0 and has_atr_col and self.trail_enabled:
                curr_atr = float(curr["atr"])
                profit = (curr_close - entry_price) * position

                if self.two_stage_exit:
                    # ── Phase 1A: Two-stage exit ────────────────────────────
                    # Stage 1: partial exit at +partial_exit_atr_mult×ATR
                    # NOTE: fills at same-bar close — optimistic vs real (next-bar open).
                    # This is flagged in the research notebook; paper trading will incur
                    # ~0.5×ATR additional slippage per partial exit.
                    if not partial_exit_done and profit > self.partial_exit_atr_mult * atr_at_entry:
                        partial_units = max(1, int(full_units * self.partial_exit_fraction))
                        rem_units     = full_units - partial_units
                        frac          = partial_units / max(full_units, 1)
                        exit_px       = curr_close   # fill at bar close (optimistic)
                        if position == 1:
                            partial_pnl_pips = (exit_px - entry_price) / pip_size - cost_pips * frac
                        else:
                            partial_pnl_pips = (entry_price - exit_px) / pip_size - cost_pips * frac
                        partial_pnl = partial_pnl_pips * pip_val * partial_units
                        equity += partial_pnl
                        trades.append(
                            Trade(
                                instrument=instrument,
                                entry_time=entry_time,   # type: ignore[arg-type]
                                exit_time=curr["time"],
                                direction=position,
                                units=partial_units,
                                entry_price=entry_price,
                                exit_price=exit_px,
                                stop_price=stop_price,
                                pnl=partial_pnl,
                                pnl_pips=partial_pnl_pips,
                                exit_reason="partial_tp",
                                bars_held=bars_held,
                                atr_at_entry=atr_at_entry,
                            )
                        )
                        units_held        = rem_units
                        partial_exit_done = True
                        # Trail starts immediately from current close.
                        # trail_best_close ≥ entry + 1.0×ATR here (profit threshold met),
                        # so trail_stop will be above structural stop — max() keeps floor.
                        trail_best_close  = curr_close
                        trail_activated   = True

                    # Stage 2: trail remainder with trail_atr_mult_remainder×ATR
                    if trail_activated:
                        if position == 1:
                            trail_best_close = max(trail_best_close, curr_close)
                            trail_stop = trail_best_close - self.trail_atr_mult_remainder * curr_atr
                            stop_price = max(stop_price, trail_stop)   # only tighten
                        else:
                            trail_best_close = min(trail_best_close, curr_close)
                            trail_stop = trail_best_close + self.trail_atr_mult_remainder * curr_atr
                            stop_price = min(stop_price, trail_stop)   # only tighten

                else:
                    # ── CHANGE 2 (v2.4): Original trail stop ────────────────
                    # Activate trail once we're +trail_activate_atr_mult×ATR in profit
                    if not trail_activated and profit > self.trail_activate_atr_mult * atr_at_entry:
                        trail_activated = True
                        trail_best_close = curr_close

                    if trail_activated:
                        # Advance best-close in the trade's favour
                        if position == 1:
                            trail_best_close = max(trail_best_close, curr_close)
                            trail_stop = trail_best_close - self.trail_distance_atr_mult * curr_atr
                            stop_price = max(stop_price, trail_stop)   # only tighten
                        else:
                            trail_best_close = min(trail_best_close, curr_close)
                            trail_stop = trail_best_close + self.trail_distance_atr_mult * curr_atr
                            stop_price = min(stop_price, trail_stop)   # only tighten

            # ── Check stop hit on the current bar ──────────────────────────
            if position != 0:
                stop_hit = False
                exit_px: float = 0.0

                if position == 1 and float(curr["low"]) <= stop_price:
                    exit_px = stop_price
                    stop_hit = True

                elif position == -1 and float(curr["high"]) >= stop_price:
                    exit_px = stop_price
                    stop_hit = True

                if stop_hit:
                    # When two_stage_exit is active and Stage 1 already closed, apply
                    # only the remaining cost fraction so total cost = 1× cost_pips per entry.
                    rem_frac = (
                        (1.0 - self.partial_exit_fraction)
                        if (self.two_stage_exit and partial_exit_done)
                        else 1.0
                    )
                    if position == 1:
                        pnl_pips = (exit_px - entry_price) / pip_size - cost_pips * rem_frac
                    else:
                        pnl_pips = (entry_price - exit_px) / pip_size - cost_pips * rem_frac
                    pnl = pnl_pips * pip_val * units_held
                    equity += pnl
                    trades.append(
                        Trade(
                            instrument=instrument,
                            entry_time=entry_time,  # type: ignore[arg-type]
                            exit_time=curr["time"],
                            direction=position,
                            units=units_held,
                            entry_price=entry_price,
                            exit_price=exit_px,
                            stop_price=stop_price,
                            pnl=pnl,
                            pnl_pips=pnl_pips,
                            exit_reason="stop_hit",
                            bars_held=bars_held,
                            atr_at_entry=atr_at_entry,
                        )
                    )
                    position = 0
                    units_held = 0
                    bars_held = 0  # CHANGE 8
                    # Reset trail + two-stage + exit-test state on stop hit
                    trail_activated        = False
                    trail_best_close       = 0.0
                    atr_at_entry           = 0.0
                    partial_exit_done      = False
                    full_units             = 0
                    max_profit_atr_multiple = 0.0

            # ── Profit target check ─────────────────────────────────────────
            if (position != 0 and self.profit_target_atr_mult is not None
                    and has_atr_col and atr_at_entry > 0 and not stop_hit):
                target_px = entry_price + position * self.profit_target_atr_mult * atr_at_entry
                pt_hit = (
                    (position == 1 and float(curr["high"]) >= target_px) or
                    (position == -1 and float(curr["low"]) <= target_px)
                )
                if pt_hit:
                    exit_px = target_px
                    if position == 1:
                        pnl_pips = (exit_px - entry_price) / pip_size - cost_pips
                    else:
                        pnl_pips = (entry_price - exit_px) / pip_size - cost_pips
                    pnl = pnl_pips * pip_val * units_held
                    equity += pnl
                    trades.append(
                        Trade(
                            instrument=instrument,
                            entry_time=entry_time,  # type: ignore[arg-type]
                            exit_time=curr["time"],
                            direction=position,
                            units=units_held,
                            entry_price=entry_price,
                            exit_price=exit_px,
                            stop_price=stop_price,
                            pnl=pnl,
                            pnl_pips=pnl_pips,
                            exit_reason="profit_target",
                            bars_held=bars_held,
                            atr_at_entry=atr_at_entry,
                        )
                    )
                    position = 0
                    units_held = 0
                    bars_held = 0
                    trail_activated        = False
                    trail_best_close       = 0.0
                    atr_at_entry           = 0.0
                    partial_exit_done      = False
                    full_units             = 0
                    max_profit_atr_multiple = 0.0

            # ── Time stop check ─────────────────────────────────────────────
            if (position != 0 and self.time_stop_bars is not None and not stop_hit
                    and bars_held >= self.time_stop_bars
                    and max_profit_atr_multiple < self.time_stop_min_profit_atr):
                exit_px = float(curr["close"])
                if position == 1:
                    pnl_pips = (exit_px - entry_price) / pip_size - cost_pips
                else:
                    pnl_pips = (entry_price - exit_px) / pip_size - cost_pips
                pnl = pnl_pips * pip_val * units_held
                equity += pnl
                trades.append(
                    Trade(
                        instrument=instrument,
                        entry_time=entry_time,  # type: ignore[arg-type]
                        exit_time=curr["time"],
                        direction=position,
                        units=units_held,
                        entry_price=entry_price,
                        exit_price=exit_px,
                        stop_price=stop_price,
                        pnl=pnl,
                        pnl_pips=pnl_pips,
                        exit_reason="time_stop",
                        bars_held=bars_held,
                        atr_at_entry=atr_at_entry,
                    )
                )
                position = 0
                units_held = 0
                bars_held = 0
                trail_activated        = False
                trail_best_close       = 0.0
                atr_at_entry           = 0.0
                partial_exit_done      = False
                full_units             = 0
                max_profit_atr_multiple = 0.0

            # ── Process signal from previous bar; enter at current open ────
            new_signal = int(prev["signal"])

            # Exit existing position if signal flipped
            # CHANGE 8: signal exits are blocked until MINIMUM_HOLD_BARS have elapsed
            if position != 0 and new_signal != position and bars_held >= MINIMUM_HOLD_BARS:
                exit_px = float(curr["open"])
                rem_frac = (
                    (1.0 - self.partial_exit_fraction)
                    if (self.two_stage_exit and partial_exit_done)
                    else 1.0
                )
                if position == 1:
                    pnl_pips = (exit_px - entry_price) / pip_size - cost_pips * rem_frac
                else:
                    pnl_pips = (entry_price - exit_px) / pip_size - cost_pips * rem_frac
                pnl = pnl_pips * pip_val * units_held
                equity += pnl
                trades.append(
                    Trade(
                        instrument=instrument,
                        entry_time=entry_time,  # type: ignore[arg-type]
                        exit_time=curr["time"],
                        direction=position,
                        units=units_held,
                        entry_price=entry_price,
                        exit_price=exit_px,
                        stop_price=stop_price,
                        pnl=pnl,
                        pnl_pips=pnl_pips,
                        exit_reason="signal_exit",
                        bars_held=bars_held,
                        atr_at_entry=atr_at_entry,
                    )
                )
                position = 0
                units_held = 0
                bars_held = 0  # CHANGE 8
                # Reset trail + two-stage + exit-test state on signal exit
                trail_activated        = False
                trail_best_close       = 0.0
                atr_at_entry           = 0.0
                partial_exit_done      = False
                full_units             = 0
                max_profit_atr_multiple = 0.0

            # Enter new position
            if position == 0 and new_signal != 0:
                entry_price = float(curr["open"])
                position = new_signal
                units_held = int(prev["units"])
                full_units = units_held   # Phase 1A: capture for partial exit calculation
                stop_distance = float(prev["stop_distance"])
                stop_price = (
                    entry_price - stop_distance
                    if position == 1
                    else entry_price + stop_distance
                )
                entry_time = curr["time"]
                bars_held = 0  # CHANGE 8: reset on entry; incremented at end of bar
                # Reset trail + two-stage + exit-test state on entry
                partial_exit_done      = False
                max_profit_atr_multiple = 0.0
                if has_atr_col:
                    atr_at_entry = float(prev.get("atr", stop_distance / 2.0))
                    trail_activated  = False
                    trail_best_close = entry_price

            equity_records.append({"time": curr["time"], "equity": equity})
            if position != 0:
                bars_held += 1  # CHANGE 8: count bars held (stop/trail exits already reset to 0)

        # ── Close any open position at end of data ─────────────────────────
        if position != 0 and len(df) > 0:
            last = df.iloc[-1]
            exit_px = float(last["close"])
            pip_val = pip_value_per_unit_usd(instrument, exit_px)
            rem_frac = (
                (1.0 - self.partial_exit_fraction)
                if (self.two_stage_exit and partial_exit_done)
                else 1.0
            )
            if position == 1:
                pnl_pips = (exit_px - entry_price) / pip_size - cost_pips * rem_frac
            else:
                pnl_pips = (entry_price - exit_px) / pip_size - cost_pips * rem_frac
            pnl = pnl_pips * pip_val * units_held
            equity += pnl
            trades.append(
                Trade(
                    instrument=instrument,
                    entry_time=entry_time,  # type: ignore[arg-type]
                    exit_time=last["time"],
                    direction=position,
                    units=units_held,
                    entry_price=entry_price,
                    exit_price=exit_px,
                    stop_price=stop_price,
                    pnl=pnl,
                    pnl_pips=pnl_pips,
                    exit_reason="end_of_data",
                    bars_held=bars_held,
                    atr_at_entry=atr_at_entry,
                )
            )

        if not equity_records:
            eq_series = pd.Series(
                [self.initial_equity], name="equity", dtype=float
            )
        else:
            eq_df = pd.DataFrame(equity_records)
            eq_series = eq_df.set_index("time")["equity"]

        trades_df = (
            pd.DataFrame([t.__dict__ for t in trades])
            if trades
            else pd.DataFrame()
        )
        ppy = PERIODS_PER_YEAR.get(granularity, 252)
        metrics = full_tearsheet(eq_series, trades_df if not trades_df.empty else None, ppy)

        logger.info(
            f"Backtest: {instrument} {granularity} | "
            f"trades={len(trades)} | "
            f"sharpe={metrics['sharpe']:.2f} | "
            f"maxDD={metrics['max_drawdown']:.2%} | "
            f"CAGR={metrics['cagr']:.2%}"
        )

        return BacktestResult(
            instrument=instrument,
            granularity=granularity,
            equity_curve=eq_series,
            trades=trades,
            metrics=metrics,
        )
