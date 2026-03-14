"""
Portfolio backtester — bar-by-bar multi-instrument simulation on a shared equity account.

Execution model (identical to VectorizedBacktester per instrument):
    - Signal generated at bar t close (prev row)
    - Entry at bar t+1 open (curr row["open"])
    - Stop checked against bar high/low on every bar
    - Signal exit at bar t+1 open when signal changes AND bars_held >= MINIMUM_HOLD_BARS
    - Units recalculated at entry time using CURRENT portfolio equity (not pre-computed)

Portfolio-level rules enforced before every entry:
    Rule 1 — USD_CLUSTER_CAP:   max 2 simultaneous positions in same USD direction
    Rule 2 — EURO_CLUSTER_CAP:  max 1 position in {EUR_USD, GBP_USD} at a time
    Rule 3 — (commodity cluster): AUD_USD only — no rule needed, single pair
    Rule 4 — PORTFOLIO_RISK_CAP: max 3 simultaneous open positions total
    Rule 5 — DRAWDOWN_THROTTLE:  risk_pct → 0.25% when DD >= 5%
           — DRAWDOWN_HALT:      no new entries when DD >= 8%; reset when DD < 3%

Processing order per bar (matches VectorizedBacktester timing):
    Phase A — process exits for all open positions (alphabetical instrument order)
    Phase B — check for new entries (alphabetical instrument order, deterministic)
    Phase C — record equity snapshot
    Phase D — increment bars_held for all surviving positions

Usage:
    from forex_system.backtest.portfolio_engine import PortfolioBacktester, PortfolioResult
    from forex_system.backtest.costs import CostModel

    bt = PortfolioBacktester(initial_equity=10_000)
    result = bt.run({"USD_JPY": jpy_signal_df, "USD_CHF": chf_signal_df})
    print(result.portfolio_metrics)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import MINIMUM_HOLD_BARS
from forex_system.backtest.metrics import full_tearsheet
from forex_system.data.instruments import registry
from forex_system.risk.sizing import calculate_units, pip_value_per_unit_usd

# ── Portfolio-level caps ──────────────────────────────────────────────────────
MAX_OPEN_POSITIONS: int = 3        # Rule 4: total simultaneous position cap
USD_CLUSTER_MAX: int = 2           # Rule 1: same USD-direction cap
EURO_CLUSTER: frozenset[str] = frozenset({"EUR_USD", "GBP_USD"})  # Rule 2

# ── Drawdown throttle thresholds ─────────────────────────────────────────────
DD_THROTTLE_PCT: float = 0.05      # 5% DD → reduce risk_pct
DD_HALT_PCT: float = 0.08          # 8% DD → halt new entries
DD_RESET_PCT: float = 0.03         # within 3% of peak → reset throttle
THROTTLED_RISK_PCT: float = 0.0025 # 0.25% when throttled
BASE_RISK_PCT: float = 0.005       # 0.5% nominal

# ── Annualization for H4 ─────────────────────────────────────────────────────
PERIODS_PER_YEAR_H4: int = 2190


# ── Helpers ──────────────────────────────────────────────────────────────────

def _usd_direction(instrument: str, signal: int) -> str | None:
    """
    Return "USD_LONG" or "USD_SHORT" for the USD leg of this position.
    Returns None if the pair has no USD leg (e.g. EUR_GBP).

    Convention:
        USD_JPY long  → buy USD, sell JPY  → USD_LONG
        GBP_USD long  → buy GBP, sell USD  → USD_SHORT
        Short signals flip the direction.
    """
    if signal == 0:
        return None
    parts = instrument.split("_")
    if len(parts) != 2:
        return None
    base, quote = parts
    if base == "USD":
        return "USD_LONG" if signal == 1 else "USD_SHORT"
    if quote == "USD":
        return "USD_SHORT" if signal == 1 else "USD_LONG"
    return None  # non-USD pair


def _calc_pnl_pips(
    direction: int,
    entry_price: float,
    exit_price: float,
    pip_size: float,
    cost_pips: float,
) -> float:
    """P&L in pips, net of round-trip cost. Mirrors VectorizedBacktester arithmetic."""
    if direction == 1:
        return (exit_price - entry_price) / pip_size - cost_pips
    else:
        return (entry_price - exit_price) / pip_size - cost_pips


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PortfolioTrade:
    """Single completed trade in the portfolio simulation."""
    instrument: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int            # 1=long, -1=short
    units: int
    entry_price: float
    exit_price: float
    stop_price: float
    pnl: float                # in USD
    pnl_pips: float
    exit_reason: str          # "stop_hit" | "signal_exit" | "end_of_data"
    bars_held: int = 0
    atr_at_entry: float = 0.0


@dataclass
class BlockedEntry:
    """Records a signal blocked by a portfolio rule — used for diagnostics."""
    time: pd.Timestamp
    instrument: str
    signal: int               # 1 or -1
    rule: str                 # "USD_CLUSTER_CAP" | "EURO_CLUSTER_CAP" |
                              # "PORTFOLIO_RISK_CAP" | "DRAWDOWN_HALT"
    open_positions_count: int
    current_dd_pct: float


@dataclass
class PortfolioResult:
    """Complete output of PortfolioBacktester.run()."""
    equity_curve: pd.Series
    trades: list[PortfolioTrade] = field(default_factory=list)
    blocked_entries: list[BlockedEntry] = field(default_factory=list)
    per_pair_trades: dict[str, list[PortfolioTrade]] = field(default_factory=dict)
    portfolio_metrics: dict = field(default_factory=dict)
    per_pair_metrics: dict[str, dict] = field(default_factory=dict)
    stage_label: str = ""

    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([t.__dict__ for t in self.trades])

    def blocked_df(self) -> pd.DataFrame:
        if not self.blocked_entries:
            return pd.DataFrame()
        return pd.DataFrame([b.__dict__ for b in self.blocked_entries])

    def pair_trades_df(self, instrument: str) -> pd.DataFrame:
        trades = self.per_pair_trades.get(instrument, [])
        if not trades:
            return pd.DataFrame()
        return pd.DataFrame([t.__dict__ for t in trades])


@dataclass
class _OpenPosition:
    """Internal mutable state for a position currently open. Not exposed in results."""
    instrument: str
    direction: int
    units: int
    entry_price: float
    stop_price: float
    entry_time: pd.Timestamp
    bars_held: int
    atr_at_entry: float
    cost_pips: float
    # Trail stop state
    trail_activated: bool = False
    trail_best_close: float = 0.0


# ── Portfolio Backtester ─────────────────────────────────────────────────────

class PortfolioBacktester:
    """
    Bar-by-bar portfolio backtest across multiple instruments on a shared equity account.

    All instruments share one equity pool. Portfolio rules are enforced at every entry.

    Args:
        initial_equity:          Starting account balance in USD.
        cost_model:              CostModel for spread + slippage per pair.
        base_risk_pct:           Nominal risk per trade (default 0.5%).
        throttled_risk_pct:      Risk when portfolio DD 5–8% (default 0.25%).
        max_open_positions:      Maximum simultaneous open positions (default 3).
        trail_enabled:           Apply trail stop (default False — frozen config).
        trail_activate_atr_mult: ATR multiple to activate trail (default 1.5).
        trail_distance_atr_mult: ATR multiple for trail distance (default 1.0).
        stage_label:             Human-readable label for this configuration.
    """

    def __init__(
        self,
        initial_equity: float = 10_000.0,
        cost_model: CostModel | None = None,
        base_risk_pct: float = BASE_RISK_PCT,
        throttled_risk_pct: float = THROTTLED_RISK_PCT,
        max_open_positions: int = MAX_OPEN_POSITIONS,
        trail_enabled: bool = False,
        trail_activate_atr_mult: float = 1.5,
        trail_distance_atr_mult: float = 1.0,
        stage_label: str = "",
    ) -> None:
        self.initial_equity = initial_equity
        self.cost_model = cost_model or CostModel()
        self.base_risk_pct = base_risk_pct
        self.throttled_risk_pct = throttled_risk_pct
        self.max_open_positions = max_open_positions
        self.trail_enabled = trail_enabled
        self.trail_activate_atr_mult = trail_activate_atr_mult
        self.trail_distance_atr_mult = trail_distance_atr_mult
        self.stage_label = stage_label

    def run(
        self,
        signal_dfs: dict[str, pd.DataFrame],
    ) -> PortfolioResult:
        """
        Run portfolio backtest across all provided instruments.

        Args:
            signal_dfs: dict of instrument → signal_df (DatetimeIndex).
                Each df must have columns: open, high, low, close, signal,
                stop_distance. Optionally: atr (used for trail stop only).

        Returns:
            PortfolioResult with combined equity curve, trades, blocked entries,
            per-pair breakdowns, and portfolio-level metrics.
        """
        if not signal_dfs:
            raise ValueError("signal_dfs must contain at least one instrument")

        instruments: list[str] = sorted(signal_dfs.keys())  # deterministic order

        # ── Step 1: Build merged timeline ────────────────────────────────────
        all_timestamps: list[pd.Timestamp] = sorted(
            set().union(*[set(df.index) for df in signal_dfs.values()])
        )
        merged_index = pd.DatetimeIndex(all_timestamps)
        n = len(merged_index)

        # Align each signal_df to the merged timeline with forward-fill
        aligned: dict[str, pd.DataFrame] = {}
        for instrument, df in signal_dfs.items():
            reindexed = df.reindex(merged_index)
            # OHLCV: forward-fill (needed for stop checks and P&L calcs)
            for col in ["open", "high", "low", "close"]:
                if col in reindexed.columns:
                    reindexed[col] = reindexed[col].ffill()
            if "atr" in reindexed.columns:
                reindexed["atr"] = reindexed["atr"].ffill()
            # Signal + stop: forward-fill so held positions persist across gaps
            reindexed["signal"] = reindexed["signal"].ffill().fillna(0).astype(int)
            reindexed["stop_distance"] = reindexed["stop_distance"].ffill().fillna(0.0)
            aligned[instrument] = reindexed

        # ── Step 2: Initialize portfolio state ───────────────────────────────
        equity: float = self.initial_equity
        peak_equity: float = self.initial_equity
        halted: bool = False

        equity_records: list[dict] = []
        all_trades: list[PortfolioTrade] = []
        blocked_entries: list[BlockedEntry] = []
        per_pair_trades: dict[str, list[PortfolioTrade]] = {i: [] for i in instruments}

        open_positions: dict[str, _OpenPosition | None] = {i: None for i in instruments}

        # ── Step 3: Bar-by-bar loop ──────────────────────────────────────────
        for i in range(1, n):
            curr_time = merged_index[i]

            # Update drawdown state (evaluated once per bar, before any pair)
            peak_equity = max(peak_equity, equity)
            current_dd = (equity / peak_equity) - 1.0  # e.g. -0.06 = 6% DD

            if current_dd <= -DD_HALT_PCT:
                halted = True
            elif halted and current_dd > -DD_THROTTLE_PCT:
                # Recovered from halt zone back to throttle zone — lift halt
                halted = False

            effective_risk_pct = (
                self.throttled_risk_pct
                if (not halted and current_dd <= -DD_THROTTLE_PCT)
                else self.base_risk_pct
            )

            # ── Phase A: Process exits ────────────────────────────────────────
            for instrument in instruments:
                pos = open_positions[instrument]
                if pos is None:
                    continue

                curr_row = aligned[instrument].iloc[i]
                prev_row = aligned[instrument].iloc[i - 1]
                meta = registry.get(instrument)
                pip_size = meta.pip_size
                curr_close = float(curr_row["close"])
                pip_val = pip_value_per_unit_usd(instrument, curr_close)

                # Trail stop update (only when trail_enabled and 'atr' column present)
                has_atr = "atr" in curr_row.index and not pd.isna(curr_row.get("atr", float("nan")))
                if self.trail_enabled and has_atr and pos.atr_at_entry > 0:
                    curr_atr = float(curr_row["atr"])
                    profit = (curr_close - pos.entry_price) * pos.direction
                    if not pos.trail_activated and profit > self.trail_activate_atr_mult * pos.atr_at_entry:
                        pos.trail_activated = True
                        pos.trail_best_close = curr_close
                    if pos.trail_activated:
                        if pos.direction == 1:
                            pos.trail_best_close = max(pos.trail_best_close, curr_close)
                            trail_stop = pos.trail_best_close - self.trail_distance_atr_mult * curr_atr
                            pos.stop_price = max(pos.stop_price, trail_stop)
                        else:
                            pos.trail_best_close = min(pos.trail_best_close, curr_close)
                            trail_stop = pos.trail_best_close + self.trail_distance_atr_mult * curr_atr
                            pos.stop_price = min(pos.stop_price, trail_stop)

                # Stop hit check
                stop_hit = False
                exit_px = 0.0
                if pos.direction == 1 and float(curr_row["low"]) <= pos.stop_price:
                    exit_px = pos.stop_price
                    stop_hit = True
                elif pos.direction == -1 and float(curr_row["high"]) >= pos.stop_price:
                    exit_px = pos.stop_price
                    stop_hit = True

                if stop_hit:
                    pnl_pips = _calc_pnl_pips(
                        pos.direction, pos.entry_price, exit_px, pip_size, pos.cost_pips
                    )
                    pnl = pnl_pips * pip_val * pos.units
                    equity += pnl
                    trade = PortfolioTrade(
                        instrument=instrument,
                        entry_time=pos.entry_time,
                        exit_time=curr_time,
                        direction=pos.direction,
                        units=pos.units,
                        entry_price=pos.entry_price,
                        exit_price=exit_px,
                        stop_price=pos.stop_price,
                        pnl=pnl,
                        pnl_pips=pnl_pips,
                        exit_reason="stop_hit",
                        bars_held=pos.bars_held,
                        atr_at_entry=pos.atr_at_entry,
                    )
                    all_trades.append(trade)
                    per_pair_trades[instrument].append(trade)
                    open_positions[instrument] = None
                    continue  # position closed; skip signal exit check

                # Signal exit check (MINIMUM_HOLD_BARS enforced)
                new_signal = int(prev_row["signal"])
                if new_signal != pos.direction and pos.bars_held >= MINIMUM_HOLD_BARS:
                    exit_px = float(curr_row["open"])
                    pnl_pips = _calc_pnl_pips(
                        pos.direction, pos.entry_price, exit_px, pip_size, pos.cost_pips
                    )
                    pnl = pnl_pips * pip_val * pos.units
                    equity += pnl
                    trade = PortfolioTrade(
                        instrument=instrument,
                        entry_time=pos.entry_time,
                        exit_time=curr_time,
                        direction=pos.direction,
                        units=pos.units,
                        entry_price=pos.entry_price,
                        exit_price=exit_px,
                        stop_price=pos.stop_price,
                        pnl=pnl,
                        pnl_pips=pnl_pips,
                        exit_reason="signal_exit",
                        bars_held=pos.bars_held,
                        atr_at_entry=pos.atr_at_entry,
                    )
                    all_trades.append(trade)
                    per_pair_trades[instrument].append(trade)
                    open_positions[instrument] = None

            # ── Phase B: Check for new entries ────────────────────────────────
            for instrument in instruments:
                if open_positions[instrument] is not None:
                    continue  # already open

                prev_row = aligned[instrument].iloc[i - 1]
                curr_row = aligned[instrument].iloc[i]
                new_signal = int(prev_row["signal"])

                if new_signal == 0:
                    continue

                n_open = sum(1 for p in open_positions.values() if p is not None)

                # Rule 4: Portfolio risk cap
                if n_open >= self.max_open_positions:
                    blocked_entries.append(BlockedEntry(
                        time=curr_time,
                        instrument=instrument,
                        signal=new_signal,
                        rule="PORTFOLIO_RISK_CAP",
                        open_positions_count=n_open,
                        current_dd_pct=current_dd,
                    ))
                    continue

                # Rule 5: Drawdown halt
                if halted:
                    blocked_entries.append(BlockedEntry(
                        time=curr_time,
                        instrument=instrument,
                        signal=new_signal,
                        rule="DRAWDOWN_HALT",
                        open_positions_count=n_open,
                        current_dd_pct=current_dd,
                    ))
                    continue

                # Rule 1: USD cluster cap
                new_usd_dir = _usd_direction(instrument, new_signal)
                if new_usd_dir is not None:
                    n_same_usd = sum(
                        1 for instr, pos in open_positions.items()
                        if pos is not None
                        and _usd_direction(instr, pos.direction) == new_usd_dir
                    )
                    if n_same_usd >= USD_CLUSTER_MAX:
                        blocked_entries.append(BlockedEntry(
                            time=curr_time,
                            instrument=instrument,
                            signal=new_signal,
                            rule="USD_CLUSTER_CAP",
                            open_positions_count=n_open,
                            current_dd_pct=current_dd,
                        ))
                        continue

                # Rule 2: Euro cluster cap
                if instrument in EURO_CLUSTER:
                    n_euro_open = sum(
                        1 for instr, pos in open_positions.items()
                        if pos is not None and instr in EURO_CLUSTER
                    )
                    if n_euro_open >= 1:
                        blocked_entries.append(BlockedEntry(
                            time=curr_time,
                            instrument=instrument,
                            signal=new_signal,
                            rule="EURO_CLUSTER_CAP",
                            open_positions_count=n_open,
                            current_dd_pct=current_dd,
                        ))
                        continue

                # All rules passed — open the position
                entry_px = float(curr_row["open"])
                stop_dist = float(prev_row["stop_distance"])
                has_atr_prev = (
                    "atr" in prev_row.index
                    and not pd.isna(prev_row.get("atr", float("nan")))
                )
                atr_entry = float(prev_row["atr"]) if has_atr_prev else 0.0

                units = calculate_units(
                    equity=equity,
                    risk_pct=effective_risk_pct,
                    stop_distance=stop_dist,
                    instrument=instrument,
                    current_price=entry_px,
                )
                if units <= 0:
                    logger.warning(
                        f"Zero units for {instrument} at {curr_time} "
                        f"(stop_dist={stop_dist:.6f}, equity={equity:.0f}) — skipping"
                    )
                    continue

                stop_px = (
                    entry_px - stop_dist if new_signal == 1 else entry_px + stop_dist
                )
                cost_pips = self.cost_model.total_cost_pips(instrument)

                open_positions[instrument] = _OpenPosition(
                    instrument=instrument,
                    direction=new_signal,
                    units=units,
                    entry_price=entry_px,
                    stop_price=stop_px,
                    entry_time=curr_time,
                    bars_held=0,
                    atr_at_entry=atr_entry,
                    cost_pips=cost_pips,
                    trail_activated=False,
                    trail_best_close=entry_px,
                )
                logger.debug(
                    f"ENTRY {instrument} {'LONG' if new_signal==1 else 'SHORT'} "
                    f"{units:,} @ {entry_px:.5f} stop={stop_px:.5f} "
                    f"equity={equity:.0f} risk={effective_risk_pct:.2%}"
                )

            # ── Phase C: Record equity snapshot ──────────────────────────────
            equity_records.append({"time": curr_time, "equity": equity})

            # ── Phase D: Increment bars_held for surviving positions ──────────
            for pos in open_positions.values():
                if pos is not None:
                    pos.bars_held += 1

        # ── Step 4: Close remaining open positions at end of data ─────────────
        if n > 0:
            last_time = merged_index[-1]
            for instrument in instruments:
                pos = open_positions[instrument]
                if pos is None:
                    continue
                last_row = aligned[instrument].iloc[-1]
                exit_px = float(last_row["close"])
                meta = registry.get(instrument)
                pip_size = meta.pip_size
                pip_val = pip_value_per_unit_usd(instrument, exit_px)
                pnl_pips = _calc_pnl_pips(
                    pos.direction, pos.entry_price, exit_px, pip_size, pos.cost_pips
                )
                pnl = pnl_pips * pip_val * pos.units
                equity += pnl
                trade = PortfolioTrade(
                    instrument=instrument,
                    entry_time=pos.entry_time,
                    exit_time=last_time,
                    direction=pos.direction,
                    units=pos.units,
                    entry_price=pos.entry_price,
                    exit_price=exit_px,
                    stop_price=pos.stop_price,
                    pnl=pnl,
                    pnl_pips=pnl_pips,
                    exit_reason="end_of_data",
                    bars_held=pos.bars_held,
                    atr_at_entry=pos.atr_at_entry,
                )
                all_trades.append(trade)
                per_pair_trades[instrument].append(trade)

        # ── Step 5: Build PortfolioResult ─────────────────────────────────────
        if not equity_records:
            eq_series = pd.Series(
                [self.initial_equity], name="equity", dtype=float
            )
        else:
            eq_df = pd.DataFrame(equity_records)
            eq_series = eq_df.set_index("time")["equity"]

        all_trades_df = (
            pd.DataFrame([t.__dict__ for t in all_trades])
            if all_trades
            else pd.DataFrame()
        )
        portfolio_metrics = full_tearsheet(
            eq_series,
            all_trades_df if not all_trades_df.empty else None,
            PERIODS_PER_YEAR_H4,
        )
        # Augment with rule block counts
        portfolio_metrics["n_blocked_usd_cluster"] = sum(
            1 for b in blocked_entries if b.rule == "USD_CLUSTER_CAP"
        )
        portfolio_metrics["n_blocked_euro_cluster"] = sum(
            1 for b in blocked_entries if b.rule == "EURO_CLUSTER_CAP"
        )
        portfolio_metrics["n_blocked_risk_cap"] = sum(
            1 for b in blocked_entries if b.rule == "PORTFOLIO_RISK_CAP"
        )
        portfolio_metrics["n_blocked_dd_halt"] = sum(
            1 for b in blocked_entries if b.rule == "DRAWDOWN_HALT"
        )
        portfolio_metrics["n_blocked_total"] = len(blocked_entries)

        # Per-pair metrics (standalone cumulative P&L curve — approximate)
        per_pair_metrics: dict[str, dict] = {}
        for instrument in instruments:
            pair_trades = per_pair_trades[instrument]
            if pair_trades:
                pair_df = pd.DataFrame([t.__dict__ for t in pair_trades])
                # Build approximate per-pair equity from cumulative P&L
                pair_eq = pd.Series(
                    pair_df["pnl"].cumsum().values + self.initial_equity,
                    name="equity",
                    dtype=float,
                )
                per_pair_metrics[instrument] = full_tearsheet(
                    pair_eq, pair_df, PERIODS_PER_YEAR_H4
                )
            else:
                per_pair_metrics[instrument] = {}

        logger.info(
            f"Portfolio [{self.stage_label or 'unlabeled'}]: "
            f"trades={len(all_trades)} | blocked={len(blocked_entries)} | "
            f"sharpe={portfolio_metrics.get('sharpe', float('nan')):.2f} | "
            f"maxDD={portfolio_metrics.get('max_drawdown', float('nan')):.1%} | "
            f"CAGR={portfolio_metrics.get('cagr', float('nan')):.1%}"
        )

        return PortfolioResult(
            equity_curve=eq_series,
            trades=all_trades,
            blocked_entries=blocked_entries,
            per_pair_trades=per_pair_trades,
            portfolio_metrics=portfolio_metrics,
            per_pair_metrics=per_pair_metrics,
            stage_label=self.stage_label,
        )
