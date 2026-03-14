"""Fixture-driven tests for the Phase 4 backtesting engine.

Coverage:
  - fills module: entry/exit price adjustments, stop/target detection
  - engine: entry, strategy exit, stop hit, target hit, cooldown, no-trade,
            end-of-backtest force close
  - equity curve: length alignment, profitable trade increases equity
  - metrics: all required metrics present, None not NaN, backtest_run_id FK
  - data_loader: empty range, bar loading, returns sorted frame
  - CostModel: from_dict / to_dict round-trip, round-trip spread cost
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from backend.backtest.costs import CostModel
from backend.backtest.data_loader import load_backtest_frame
from backend.backtest.engine import run_backtest
from backend.backtest.fills import check_stop_target, compute_entry_fill, compute_exit_fill
from backend.backtest.metrics import build_equity_curve, compute_metrics
from backend.schemas.enums import TradeSide, Timeframe
from backend.schemas.models import BacktestRun, Trade
from backend.strategies.base import BaseStrategy
from backend.strategies.state import StrategyState


# ---------------------------------------------------------------------------
# Minimal fixture strategies
# ---------------------------------------------------------------------------

class EnterOnBar1LongStrategy(BaseStrategy):
    """Enters long on bar with _bar_idx == 1; exits on bar with _bar_idx == 3."""

    def should_enter_long(self, bar, features, state):
        return bar.get("_bar_idx") == 1

    def should_enter_short(self, bar, features, state):
        return False

    def should_exit(self, bar, features, position, state):
        return bar.get("_bar_idx") == 3


class EnterOnBar1ShortStrategy(BaseStrategy):
    def should_enter_long(self, bar, features, state):
        return False

    def should_enter_short(self, bar, features, state):
        return bar.get("_bar_idx") == 1

    def should_exit(self, bar, features, position, state):
        return bar.get("_bar_idx") == 3


class NeverEnterStrategy(BaseStrategy):
    def should_enter_long(self, bar, features, state):
        return False

    def should_enter_short(self, bar, features, state):
        return False

    def should_exit(self, bar, features, position, state):
        return False


class NeverExitStrategy(BaseStrategy):
    """Enters long on bar 1, never signals an exit (force-close test)."""

    def should_enter_long(self, bar, features, state):
        return bar.get("_bar_idx") == 1

    def should_enter_short(self, bar, features, state):
        return False

    def should_exit(self, bar, features, position, state):
        return False


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_bar(bar_idx: int, ts: datetime, o, h, l, c) -> dict:
    return {
        "_bar_idx": bar_idx,
        "instrument_id": "EUR_USD",
        "timestamp_utc": ts,
        "open": o, "high": h, "low": l, "close": c,
        "volume": 1000.0,
    }


def _flat_bars(n: int = 6, base_price: float = 1.1000) -> list[dict]:
    """Return n hourly bars with fixed price (no trend, tight range)."""
    start = datetime(2024, 1, 2, 0, 0, 0)
    return [
        _make_bar(
            bar_idx=i,
            ts=start + timedelta(hours=i),
            o=base_price,
            h=base_price + 0.0010,
            l=base_price - 0.0010,
            c=base_price,
        )
        for i in range(n)
    ]


def _make_backtest_run() -> BacktestRun:
    return BacktestRun(
        instrument_id="EUR_USD",
        timeframe=Timeframe.H1,
        test_start=datetime(2024, 1, 2),
        test_end=datetime(2024, 1, 9),
    )


def _zero_cost() -> CostModel:
    return CostModel(spread_pips=0.0, slippage_pips=0.0, commission_per_unit=0.0)


# ===========================================================================
# CostModel
# ===========================================================================

class TestCostModel:
    def test_from_dict_to_dict_round_trip(self):
        cm = CostModel(spread_pips=2.0, slippage_pips=0.5, commission_per_unit=0.01, pip_size=0.0001)
        cm2 = CostModel.from_dict(cm.to_dict())
        assert cm == cm2

    def test_long_entry_positive_adjustment(self):
        cm = CostModel(spread_pips=2.0, slippage_pips=0.0, pip_size=0.0001)
        assert cm.entry_price_adjustment("long") > 0

    def test_short_entry_negative_adjustment(self):
        cm = CostModel(spread_pips=2.0, slippage_pips=0.0, pip_size=0.0001)
        assert cm.entry_price_adjustment("short") < 0

    def test_round_trip_spread_equals_spread_pips_times_pip_size(self):
        cm = CostModel(spread_pips=2.0, slippage_pips=0.0, pip_size=0.0001)
        # For a flat trade: cost = entry_adj(long) - exit_adj(long)
        #   = +half_spread - (-half_spread) = spread_pips * pip_size
        round_trip = cm.entry_price_adjustment("long") - cm.exit_price_adjustment("long")
        expected = cm.spread_pips * cm.pip_size
        assert abs(round_trip - expected) < 1e-12

    def test_commission_cost_doubles_per_side(self):
        cm = CostModel(commission_per_unit=0.01)
        assert cm.commission_cost(10_000) == 2 * 0.01 * 10_000


# ===========================================================================
# Fills module
# ===========================================================================

class TestComputeEntryFill:
    def test_long_entry_adds_cost(self):
        bar = {"close": 1.1000}
        cm = CostModel(spread_pips=2.0, slippage_pips=0.5, pip_size=0.0001)
        fill = compute_entry_fill(bar, "long", cm)
        # half-spread (1 pip) + slippage (0.5 pip) = 1.5 pips
        assert abs(fill - (1.1000 + 1.5 * 0.0001)) < 1e-10

    def test_short_entry_subtracts_cost(self):
        bar = {"close": 1.1000}
        cm = CostModel(spread_pips=2.0, slippage_pips=0.5, pip_size=0.0001)
        fill = compute_entry_fill(bar, "short", cm)
        assert abs(fill - (1.1000 - 1.5 * 0.0001)) < 1e-10

    def test_zero_cost_returns_close(self):
        bar = {"close": 1.2345}
        assert compute_entry_fill(bar, "long", _zero_cost()) == 1.2345


class TestComputeExitFill:
    def test_long_exit_subtracts_half_spread(self):
        bar = {"close": 1.1000}
        cm = CostModel(spread_pips=2.0, pip_size=0.0001)
        fill = compute_exit_fill(bar, "long", cm)
        assert abs(fill - (1.1000 - 1.0 * 0.0001)) < 1e-10

    def test_short_exit_adds_half_spread(self):
        bar = {"close": 1.1000}
        cm = CostModel(spread_pips=2.0, pip_size=0.0001)
        fill = compute_exit_fill(bar, "short", cm)
        assert abs(fill - (1.1000 + 1.0 * 0.0001)) < 1e-10


class TestCheckStopTarget:
    def test_long_stop_hit(self):
        bar = {"low": 1.0920, "high": 1.1050, "close": 1.1000}
        result = check_stop_target(bar, "long", stop=1.0950, target=1.1100)
        assert result.hit
        assert result.exit_reason == "stop_hit"
        assert result.exit_price == 1.0950

    def test_long_target_hit(self):
        bar = {"low": 1.0990, "high": 1.1150, "close": 1.1100}
        result = check_stop_target(bar, "long", stop=1.0800, target=1.1100)
        assert result.hit
        assert result.exit_reason == "target_hit"
        assert result.exit_price == 1.1100

    def test_long_both_hit_stop_wins(self):
        bar = {"low": 1.0800, "high": 1.1200, "close": 1.1000}
        result = check_stop_target(bar, "long", stop=1.0950, target=1.1050)
        assert result.exit_reason == "stop_hit"

    def test_short_stop_hit(self):
        bar = {"low": 1.0950, "high": 1.1100, "close": 1.1000}
        result = check_stop_target(bar, "short", stop=1.1050, target=1.0900)
        assert result.hit
        assert result.exit_reason == "stop_hit"

    def test_short_target_hit(self):
        bar = {"low": 1.0850, "high": 1.1010, "close": 1.0900}
        result = check_stop_target(bar, "short", stop=1.1100, target=1.0900)
        assert result.hit
        assert result.exit_reason == "target_hit"

    def test_no_hit_returns_false(self):
        bar = {"low": 1.0990, "high": 1.1010, "close": 1.1000}
        result = check_stop_target(bar, "long", stop=1.0950, target=1.1050)
        assert not result.hit
        assert result.exit_reason == "none"

    def test_none_stop_and_target_never_hit(self):
        bar = {"low": 1.0000, "high": 2.0000, "close": 1.1000}
        result = check_stop_target(bar, "long", stop=None, target=None)
        assert not result.hit


# ===========================================================================
# Engine — entry
# ===========================================================================

class TestEngineEntry:
    def test_long_entry_creates_one_trade(self):
        bars = _flat_bars(n=5)
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].side == TradeSide.LONG

    def test_entry_time_is_bar1_timestamp(self):
        bars = _flat_bars(n=5)
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert trades[0].entry_time == bars[1]["timestamp_utc"]

    def test_entry_price_includes_spread(self):
        bars = _flat_bars(n=5, base_price=1.1000)
        cm = CostModel(spread_pips=2.0, slippage_pips=0.0, pip_size=0.0001)
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=cm,
        )
        # Long entry = close + half-spread = 1.1000 + 0.0001
        assert abs(trades[0].entry_price - 1.1001) < 1e-9

    def test_short_entry_side(self):
        bars = _flat_bars(n=5)
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1ShortStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].side == TradeSide.SHORT

    def test_backtest_run_id_on_trade(self):
        bars = _flat_bars(n=5)
        run = _make_backtest_run()
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=run,
            cost_model=_zero_cost(),
        )
        assert trades[0].backtest_run_id == run.id


# ===========================================================================
# Engine — strategy signal exit
# ===========================================================================

class TestEngineStrategyExit:
    def test_exit_on_bar3(self):
        bars = _flat_bars(n=6)
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert trades[0].exit_time == bars[3]["timestamp_utc"]
        assert trades[0].exit_reason == "strategy_signal"

    def test_holding_period_bar1_to_bar3(self):
        bars = _flat_bars(n=6)
        trades, _, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert trades[0].holding_period == 2  # bar 3 - bar 1


# ===========================================================================
# Engine — stop loss
# ===========================================================================

class TestEngineStopLoss:
    def test_stop_hit_closes_at_stop_price(self):
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(0, start,                          1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(1, start + timedelta(hours=1),     1.1000, 1.1010, 1.0990, 1.1000),  # entry
            _make_bar(2, start + timedelta(hours=2),     1.0990, 1.1000, 1.0910, 1.0940),  # stop hit (low=1.0910 < 1.0950)
            _make_bar(3, start + timedelta(hours=3),     1.0940, 1.0960, 1.0920, 1.0950),
        ]

        class StopStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") == 1

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return False

            def stop_price(self, bar, side, params):
                return 1.0950 if bar.get("_bar_idx") == 1 else None

        trades, _, _, _ = run_backtest(
            strategy=StopStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
            params={},
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_hit"
        assert trades[0].exit_price == 1.0950
        assert trades[0].exit_time == bars[2]["timestamp_utc"]
        assert trades[0].pnl < 0  # bought at 1.1000, stopped at 1.0950 → loss

    def test_stop_hit_does_not_reenter_same_bar(self):
        """On the bar a stop hits, no new entry should occur."""
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(0, start,                      1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(1, start + timedelta(hours=1), 1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(2, start + timedelta(hours=2), 1.0990, 1.1010, 1.0910, 1.0940),  # stop hit
            _make_bar(3, start + timedelta(hours=3), 1.0940, 1.0960, 1.0920, 1.0950),
        ]

        class StopThenEnterStrategy(BaseStrategy):
            """Would re-enter on bar 2 if cooldown allows — but engine skips after stop hit."""
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") in (1, 2)  # tries to enter on bar 2 too

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return False

            def stop_price(self, bar, side, params):
                return 1.0950 if bar.get("_bar_idx") == 1 else None

        trades, _, _, _ = run_backtest(
            strategy=StopThenEnterStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        # Stop on bar 2 → engine skips on_bar for bar 2, so no re-entry on bar 2.
        # First trade = entry bar 1 / stop bar 2.
        assert trades[0].exit_reason == "stop_hit"
        assert trades[0].exit_time == bars[2]["timestamp_utc"]

    def test_short_stop_hit_closes_at_stop_price(self):
        """For a short position, stop hit should close at the stop price when high >= stop."""
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(0, start,                      1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(1, start + timedelta(hours=1), 1.1000, 1.1010, 1.0990, 1.1000),  # entry
            _make_bar(2, start + timedelta(hours=2), 1.0990, 1.1050, 1.0980, 1.1040),  # stop hit (high=1.1050 >= 1.1050)
        ]

        class ShortStopStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return False

            def should_enter_short(self, bar, features, state):
                return bar.get("_bar_idx") == 1

            def should_exit(self, bar, features, position, state):
                return False

            def stop_price(self, bar, side, params):
                return 1.1050 if bar.get("_bar_idx") == 1 else None

        trades, _, _, _ = run_backtest(
            strategy=ShortStopStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].side == TradeSide.SHORT
        assert trades[0].exit_reason == "stop_hit"
        assert trades[0].exit_price == 1.1050
        # SHORT: entry at 1.1000, stop at 1.1050 → closed at 1.1050 → loss of 50 pips
        assert trades[0].pnl < 0


# ===========================================================================
# Engine — take profit
# ===========================================================================

class TestEngineTakeProfit:
    def test_target_hit_closes_at_target_price(self):
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(0, start,                          1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(1, start + timedelta(hours=1),     1.1000, 1.1010, 1.0990, 1.1000),  # entry
            _make_bar(2, start + timedelta(hours=2),     1.1010, 1.1090, 1.1000, 1.1050),  # target hit (high=1.1090 >= 1.1050)
            _make_bar(3, start + timedelta(hours=3),     1.1050, 1.1060, 1.1040, 1.1050),
        ]

        class TargetStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") == 1

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return False

            def take_profit_price(self, bar, side, params):
                return 1.1050 if bar.get("_bar_idx") == 1 else None

        trades, _, _, _ = run_backtest(
            strategy=TargetStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == "target_hit"
        assert trades[0].exit_price == 1.1050
        assert trades[0].pnl > 0  # entered 1.1000, exited 1.1050 → profit

    def test_short_target_hit(self):
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(0, start,                          1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(1, start + timedelta(hours=1),     1.1000, 1.1010, 1.0990, 1.1000),  # entry
            _make_bar(2, start + timedelta(hours=2),     1.0990, 1.1000, 1.0890, 1.0920),  # target hit (low=1.0890 <= 1.0950)
        ]

        class ShortTargetStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return False

            def should_enter_short(self, bar, features, state):
                return bar.get("_bar_idx") == 1

            def should_exit(self, bar, features, position, state):
                return False

            def take_profit_price(self, bar, side, params):
                return 1.0950 if bar.get("_bar_idx") == 1 else None

        trades, _, _, _ = run_backtest(
            strategy=ShortTargetStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == "target_hit"
        assert trades[0].pnl > 0


# ===========================================================================
# Engine — cooldown / standoff
# ===========================================================================

class TestEngineCooldown:
    def test_cooldown_blocks_reentry(self):
        """After exit on bar 3, 4-hour cooldown blocks entry on bars 4, 5, 6.
        Strategy signals at bars 1, 5, 8.  Cooldown expires at bar 7 (4h later).
        Second entry happens on bar 8 and is force-closed at end.
        """
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(i, start + timedelta(hours=i), 1.1, 1.101, 1.099, 1.1)
            for i in range(10)
        ]

        class CooldownStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") in (1, 5, 8)

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return bar.get("_bar_idx") == 3  # exit trade on bar 3

        trades, _, _, _ = run_backtest(
            strategy=CooldownStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
            params={"cooldown_hours": 4.0},
        )
        # Trade 1: entered bar 1, exited bar 3 (strategy signal).
        # Trade 2: bar 5 is 2h after exit → still in cooldown.
        #          bar 8 is 5h after exit → cooldown expired → entry.
        assert len(trades) == 2
        assert trades[0].entry_time == bars[1]["timestamp_utc"]
        assert trades[0].exit_time == bars[3]["timestamp_utc"]
        assert trades[1].entry_time == bars[8]["timestamp_utc"]

    def test_zero_cooldown_allows_immediate_reentry(self):
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(i, start + timedelta(hours=i), 1.1, 1.101, 1.099, 1.1)
            for i in range(6)
        ]

        class ImmediateReentryStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") in (1, 3)

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return bar.get("_bar_idx") == 2

        trades, _, _, _ = run_backtest(
            strategy=ImmediateReentryStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
            params={"cooldown_hours": 0.0},
        )
        # Trade 1: bar 1 → bar 2. Trade 2: bar 3 → force-closed at bar 5.
        assert len(trades) == 2


# ===========================================================================
# Engine — no-trade case
# ===========================================================================

class TestEngineNoTrade:
    def test_empty_trades_flat_equity(self):
        bars = _flat_bars(n=5)
        trades, metrics, equity, drawdown = run_backtest(
            strategy=NeverEnterStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 0
        assert all(eq == 100_000.0 for eq in equity)
        assert all(dd == 0.0 for dd in drawdown)

    def test_empty_trades_metrics(self):
        bars = _flat_bars(n=5)
        _, metrics, _, _ = run_backtest(
            strategy=NeverEnterStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        metric_map = {m.metric_name: m.metric_value for m in metrics}
        assert metric_map["total_trades"] == 0.0
        assert metric_map["sharpe_ratio"] is None
        assert metric_map["win_rate"] is None


# ===========================================================================
# Engine — force close at end of backtest
# ===========================================================================

class TestEngineForceClose:
    def test_open_position_force_closed(self):
        bars = _flat_bars(n=4)
        trades, _, _, _ = run_backtest(
            strategy=NeverExitStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_backtest"
        assert trades[0].exit_time == bars[-1]["timestamp_utc"]

    def test_entry_on_last_bar_force_closed_immediately(self):
        """When entry happens on the very last bar, it should be force-closed on that same bar."""
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(i, start + timedelta(hours=i), 1.1, 1.101, 1.099, 1.1)
            for i in range(3)
        ]

        class EnterOnLastBarStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") == 2  # Last bar

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return False

        trades, _, _, _ = run_backtest(
            strategy=EnterOnLastBarStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(trades) == 1
        assert trades[0].entry_time == bars[2]["timestamp_utc"]
        assert trades[0].exit_time == bars[2]["timestamp_utc"]
        assert trades[0].exit_reason == "end_of_backtest"
        assert trades[0].holding_period == 0  # Opened and closed on same bar


# ===========================================================================
# Engine — equity curve
# ===========================================================================

class TestEquityCurve:
    def test_equity_length_matches_bars(self):
        bars = _flat_bars(n=6)
        _, _, equity, drawdown = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert len(equity) == len(bars)
        assert len(drawdown) == len(bars)

    def test_profitable_trade_increases_final_equity(self):
        """Long entry at 1.1000, exit at 1.1050 → 50 pips × 10k units = +$500."""
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            _make_bar(0, start,                          1.1000, 1.1010, 1.0990, 1.1000),
            _make_bar(1, start + timedelta(hours=1),     1.1000, 1.1010, 1.0990, 1.1000),  # entry close=1.1000
            _make_bar(2, start + timedelta(hours=2),     1.1040, 1.1060, 1.1030, 1.1050),  # exit close=1.1050
            _make_bar(3, start + timedelta(hours=3),     1.1050, 1.1060, 1.1040, 1.1050),
        ]

        class ExitOnBar2(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") == 1

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return bar.get("_bar_idx") == 2

        trades, _, equity, _ = run_backtest(
            strategy=ExitOnBar2(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
            initial_equity=100_000.0,
            params={},
        )
        assert trades[0].pnl > 0
        assert equity[-1] > 100_000.0

    def test_no_trades_no_drawdown(self):
        bars = _flat_bars(n=5)
        _, _, _, drawdown = run_backtest(
            strategy=NeverEnterStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        assert all(d == 0.0 for d in drawdown)


# ===========================================================================
# Performance metrics
# ===========================================================================

class TestMetrics:
    def test_all_required_metrics_present(self):
        bars = _flat_bars(n=6)
        _, metrics, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        names = {m.metric_name for m in metrics}
        for required in [
            "total_trades", "win_rate", "net_pnl", "net_return_pct",
            "max_drawdown_pct", "profit_factor", "sharpe_ratio",
            "sortino_ratio", "expectancy", "avg_win", "avg_loss",
        ]:
            assert required in names, f"Missing required metric: {required}"

    def test_no_nan_metric_values(self):
        """All metric_value fields must be None or a finite float — never NaN."""
        bars = _flat_bars(n=5)
        _, metrics, _, _ = run_backtest(
            strategy=NeverEnterStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        for m in metrics:
            if m.metric_value is not None:
                assert not math.isnan(m.metric_value), f"{m.metric_name} is NaN"

    def test_metrics_carry_correct_backtest_run_id(self):
        bars = _flat_bars(n=5)
        run = _make_backtest_run()
        _, metrics, _, _ = run_backtest(
            strategy=NeverEnterStrategy(),
            bars=bars,
            backtest_run=run,
            cost_model=_zero_cost(),
        )
        assert all(m.backtest_run_id == run.id for m in metrics)

    def test_win_count_plus_loss_count_equals_total(self):
        bars = _flat_bars(n=6)
        _, metrics, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        m = {x.metric_name: x.metric_value for x in metrics if x.segment_type == "overall"}
        total = m["total_trades"]
        assert m["win_count"] + m["loss_count"] == total

    def test_regime_breakdown_segment_present_when_regime_set(self):
        """When a bar has a regime_label, metrics should contain a regime segment."""
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            {**_make_bar(i, start + timedelta(hours=i), 1.1, 1.101, 1.099, 1.1),
             "regime_label": "TREND_BULL_LOW_VOL"}
            for i in range(6)
        ]
        _, metrics, _, _ = run_backtest(
            strategy=EnterOnBar1LongStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        regime_metrics = [m for m in metrics if m.segment_type == "regime"]
        assert len(regime_metrics) > 0

    def test_per_regime_pnl_metrics(self):
        """Per-regime metrics should track pnl and trade count for each regime."""
        start = datetime(2024, 1, 2, 0, 0)
        bars = [
            {**_make_bar(i, start + timedelta(hours=i), 1.1, 1.101, 1.099, 1.1),
             "regime_label": "TREND_BULL_LOW_VOL" if i < 3 else "CHOPPY_SIGNAL"}
            for i in range(6)
        ]

        class MultiTradeStrategy(BaseStrategy):
            def should_enter_long(self, bar, features, state):
                return bar.get("_bar_idx") in (1,)

            def should_enter_short(self, bar, features, state):
                return False

            def should_exit(self, bar, features, position, state):
                return bar.get("_bar_idx") == 3

        _, metrics, _, _ = run_backtest(
            strategy=MultiTradeStrategy(),
            bars=bars,
            backtest_run=_make_backtest_run(),
            cost_model=_zero_cost(),
        )
        regime_metrics = {
            (m.segment_key, m.metric_name): m.metric_value
            for m in metrics if m.segment_type == "regime"
        }
        # Should have metrics for the regime that had the trade
        assert ("TREND_BULL_LOW_VOL", "net_pnl") in regime_metrics
        assert ("TREND_BULL_LOW_VOL", "trade_count") in regime_metrics


# ===========================================================================
# build_equity_curve (unit)
# ===========================================================================

class TestBuildEquityCurve:
    def test_length_matches_timestamps(self):
        timestamps = [datetime(2024, 1, 2) + timedelta(hours=i) for i in range(5)]
        trades: list[Trade] = []
        equity, drawdown = build_equity_curve(trades, timestamps, initial_equity=100_000.0)
        assert len(equity) == 5
        assert len(drawdown) == 5

    def test_pnl_lands_at_exit_timestamp(self):
        exit_ts = datetime(2024, 1, 2, 2, 0)
        trade = Trade(
            backtest_run_id="test",
            instrument_id="EUR_USD",
            entry_time=datetime(2024, 1, 2, 1, 0),
            exit_time=exit_ts,
            side=TradeSide.LONG,
            quantity=10_000.0,
            entry_price=1.1000,
            exit_price=1.1050,
            pnl=500.0,
        )
        timestamps = [datetime(2024, 1, 2) + timedelta(hours=i) for i in range(4)]
        equity, _ = build_equity_curve([trade], timestamps, initial_equity=100_000.0)
        # Before exit (bar 0 and 1): equity = 100_000
        assert equity[0] == 100_000.0
        assert equity[1] == 100_000.0
        # At exit bar (bar 2): equity += 500
        assert equity[2] == 100_500.0
        assert equity[3] == 100_500.0

    def test_zero_trades_returns_flat_curves(self):
        """When no trades exist, equity and drawdown should be constant."""
        timestamps = [datetime(2024, 1, 2) + timedelta(hours=i) for i in range(5)]
        equity, drawdown = build_equity_curve([], timestamps, initial_equity=100_000.0)
        assert all(e == 100_000.0 for e in equity)
        assert all(d == 0.0 for d in drawdown)


# ===========================================================================
# data_loader (unit)
# ===========================================================================

class TestDataLoader:
    def test_empty_range_returns_empty_list(self, tmp_path):
        from backend.data.duckdb_store import DuckDBStore
        store = DuckDBStore(str(tmp_path / "test.duckdb"))
        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            store,
        )
        assert result == []

    def test_bars_loaded_and_sorted(self, tmp_path):
        from backend.data.duckdb_store import DuckDBStore
        store = DuckDBStore(str(tmp_path / "test.duckdb"))
        rows = [
            {
                "instrument_id": "EUR_USD", "timeframe": "H1",
                "timestamp_utc": datetime(2024, 1, 1, 1, 0),
                "open": 1.1, "high": 1.101, "low": 1.099, "close": 1.100,
                "volume": 100, "source": "oanda",
            },
            {
                "instrument_id": "EUR_USD", "timeframe": "H1",
                "timestamp_utc": datetime(2024, 1, 1, 0, 0),
                "open": 1.1, "high": 1.102, "low": 1.099, "close": 1.101,
                "volume": 100, "source": "oanda",
            },
        ]
        store.upsert_bars_agg(rows)
        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 2, 0),
            store,
        )
        assert len(result) == 2
        # Sorted ascending by timestamp
        assert result[0]["timestamp_utc"] < result[1]["timestamp_utc"]

    def test_features_joined_into_frame(self, tmp_path):
        from backend.data.duckdb_store import DuckDBStore
        store = DuckDBStore(str(tmp_path / "test.duckdb"))
        ts = datetime(2024, 1, 1, 0, 0)
        store.upsert_bars_agg([{
            "instrument_id": "EUR_USD", "timeframe": "H1",
            "timestamp_utc": ts, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.1,
            "volume": 100, "source": "oanda",
        }])
        store.upsert_features([{
            "instrument_id": "EUR_USD", "timeframe": "H1",
            "timestamp_utc": ts, "feature_run_id": "run1",
            "feature_name": "rsi_14", "feature_value": 55.0,
        }])
        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            store,
            feature_run_id="run1",
        )
        assert len(result) == 1
        assert result[0].get("rsi_14") == 55.0

    def test_features_missing_when_run_id_doesnt_match(self, tmp_path):
        """When feature_run_id is provided but no features exist for that run, bars should still load."""
        from backend.data.duckdb_store import DuckDBStore
        store = DuckDBStore(str(tmp_path / "test.duckdb"))
        ts = datetime(2024, 1, 1, 0, 0)
        store.upsert_bars_agg([{
            "instrument_id": "EUR_USD", "timeframe": "H1",
            "timestamp_utc": ts, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.1,
            "volume": 100, "source": "oanda",
        }])
        # Don't insert any features
        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            store,
            feature_run_id="nonexistent_run",
        )
        assert len(result) == 1
        assert "rsi_14" not in result[0]  # No features present

    def test_regime_labels_joined_into_frame(self, tmp_path):
        """Regime labels should be joined by timestamp when model_id is provided."""
        from backend.data.duckdb_store import DuckDBStore
        store = DuckDBStore(str(tmp_path / "test.duckdb"))
        ts = datetime(2024, 1, 1, 0, 0)
        store.upsert_bars_agg([{
            "instrument_id": "EUR_USD", "timeframe": "H1",
            "timestamp_utc": ts, "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.1,
            "volume": 100, "source": "oanda",
        }])
        store.upsert_regime_labels([{
            "model_id": "model1", "state_id": 0,
            "instrument_id": "EUR_USD", "timeframe": "H1",
            "timestamp_utc": ts, "regime_label": "TREND_BULL_LOW_VOL",
        }])
        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            store,
            model_id="model1",
        )
        assert len(result) == 1
        assert result[0].get("regime_label") == "TREND_BULL_LOW_VOL"
        assert result[0].get("state_id") == 0
