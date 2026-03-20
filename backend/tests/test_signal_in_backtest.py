"""End-to-end smoke test: signal columns flow through data_loader into the backtest engine.

Fully in-memory — uses DuckDBStore(":memory:") and a mock metadata_repo.
Verifies that:
  1. Signal columns are present in bar dicts after load_backtest_frame.
  2. A RulesStrategy can reference signal columns without KeyError.
  3. Trades are produced when signal values satisfy entry conditions.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.backtest.costs import CostModel
from backend.backtest.data_loader import load_backtest_frame
from backend.backtest.engine import run_backtest
from backend.data.duckdb_store import DuckDBStore
from backend.schemas.enums import Timeframe
from backend.schemas.models import BacktestRun
from backend.strategies.rules_strategy import RulesStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_store_with_data(n_bars: int = 50):
    """Create an in-memory DuckDB with bars, features, and signal values."""
    store = DuckDBStore(":memory:")
    instrument = "EUR_USD"
    t0 = datetime(2024, 1, 1)

    # 1. Insert bars_agg
    bars = []
    for i in range(n_bars):
        ts = t0 + timedelta(hours=i)
        bars.append({
            "instrument_id": instrument,
            "timeframe": "H1",
            "timestamp_utc": ts,
            "open": 1.1000 + i * 0.0001,
            "high": 1.1020 + i * 0.0001,
            "low": 1.0980 + i * 0.0001,
            "close": 1.1010 + i * 0.0001,
            "volume": 100.0,
            "source": "test",
            "derivation_version": "1",
        })
    store.upsert_bars_agg(bars)

    # 2. Insert features (atr_14 and rsi_14) with a feature_run_id
    feature_run_id = "feat_run_001"
    feature_rows = []
    for i in range(n_bars):
        ts = t0 + timedelta(hours=i)
        feature_rows.append({
            "instrument_id": instrument,
            "timeframe": "H1",
            "timestamp_utc": ts,
            "feature_run_id": feature_run_id,
            "feature_name": "atr_14",
            "feature_value": 0.0020,
        })
        feature_rows.append({
            "instrument_id": instrument,
            "timeframe": "H1",
            "timestamp_utc": ts,
            "feature_run_id": feature_run_id,
            "feature_name": "rsi_14",
            "feature_value": 50.0 + (i % 30),
        })
    store.upsert_features(feature_rows)

    # 3. Insert signal values for "automl_direction_prob"
    signal_id = "test_signal_id"
    signal_feature_name = f"signal_{signal_id}_value"
    signal_rows = []
    for i in range(n_bars):
        ts = t0 + timedelta(hours=i)
        # Alternate: even bars get 0.3, odd bars get 0.7
        value = 0.7 if i % 2 == 1 else 0.3
        signal_rows.append({
            "instrument_id": instrument,
            "timeframe": "H1",
            "timestamp_utc": ts,
            "feature_run_id": signal_id,
            "feature_name": signal_feature_name,
            "feature_value": value,
        })
    store.upsert_features(signal_rows)

    return store, instrument, t0, feature_run_id, signal_id


def _mock_meta_repo(signal_id: str) -> MagicMock:
    """Return a mock metadata_repo that knows about our test signal."""
    repo = MagicMock()
    repo.get_signal.return_value = {
        "id": signal_id,
        "name": "test_automl_signal",
        "signal_type": "automl_direction_prob",
        "definition_json": {},
        "metadata": {"field_name": "automl_direction_prob"},
        "source_model_id": None,
        "version": 1,
    }
    repo.get_model.return_value = None
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSignalInBacktest:
    """End-to-end: signal columns survive from data_loader through engine."""

    def test_signal_columns_present_in_frame(self):
        """load_backtest_frame with signal_ids adds the signal column."""
        store, instrument, t0, feat_id, sig_id = _build_store_with_data(50)
        meta_repo = _mock_meta_repo(sig_id)

        frame = load_backtest_frame(
            instrument, Timeframe.H1,
            t0, t0 + timedelta(hours=50),
            market_repo=store,
            feature_run_id=feat_id,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )

        assert len(frame) == 50
        for bar in frame:
            assert "automl_direction_prob" in bar, (
                f"Signal column missing from bar at {bar['timestamp_utc']}"
            )
            assert bar["automl_direction_prob"] is not None

    def test_no_keyerror_in_backtest(self):
        """Running the engine with signal-referencing rules raises no KeyError."""
        store, instrument, t0, feat_id, sig_id = _build_store_with_data(50)
        meta_repo = _mock_meta_repo(sig_id)

        frame = load_backtest_frame(
            instrument, Timeframe.H1,
            t0, t0 + timedelta(hours=50),
            market_repo=store,
            feature_run_id=feat_id,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )

        # Rules strategy that references the signal column
        definition = {
            "entry_long": {"field": "automl_direction_prob", "op": "gte", "value": 0.65},
            "exit": {"field": "automl_direction_prob", "op": "lt", "value": 0.5},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 10000,
        }
        strategy = RulesStrategy(definition)

        backtest_run = BacktestRun(
            instrument_id=instrument,
            timeframe=Timeframe.H1,
            test_start=t0,
            test_end=t0 + timedelta(hours=50),
        )
        cost_model = CostModel(spread_pips=2.0, slippage_pips=0.5, pip_size=0.0001)

        # This must not raise KeyError
        trades, metrics, equity, drawdown = run_backtest(
            strategy=strategy,
            bars=frame,
            backtest_run=backtest_run,
            cost_model=cost_model,
        )

        # Sanity: equity curve length matches bars
        assert len(equity) == 50
        assert len(drawdown) == 50

    def test_trades_produced(self):
        """The alternating signal pattern should produce at least one trade."""
        store, instrument, t0, feat_id, sig_id = _build_store_with_data(50)
        meta_repo = _mock_meta_repo(sig_id)

        frame = load_backtest_frame(
            instrument, Timeframe.H1,
            t0, t0 + timedelta(hours=50),
            market_repo=store,
            feature_run_id=feat_id,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )

        definition = {
            "entry_long": {"field": "automl_direction_prob", "op": "gte", "value": 0.65},
            "exit": {"field": "automl_direction_prob", "op": "lt", "value": 0.5},
            "position_size_units": 10000,
        }
        strategy = RulesStrategy(definition)

        backtest_run = BacktestRun(
            instrument_id=instrument,
            timeframe=Timeframe.H1,
            test_start=t0,
            test_end=t0 + timedelta(hours=50),
        )
        cost_model = CostModel(spread_pips=2.0, slippage_pips=0.5, pip_size=0.0001)

        trades, metrics, equity, drawdown = run_backtest(
            strategy=strategy,
            bars=frame,
            backtest_run=backtest_run,
            cost_model=cost_model,
        )

        assert len(trades) > 0, "Expected at least one trade from alternating signal pattern"
