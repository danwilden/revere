"""Tests for signal-aware data_loader and validate_signal_fields.

Covers:
  - load_backtest_frame with signal_ids (join, column naming, None fill)
  - validate_signal_fields (composites, leaves, refs, field2)
  - automl_signal.py and bank.py metadata["field_name"] patches
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.backtest.data_loader import load_backtest_frame
from backend.data.duckdb_store import DuckDBStore
from backend.schemas.enums import SignalType, Timeframe
from backend.strategies.rules_engine import validate_signal_fields


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_bars(n: int, instrument: str = "EUR_USD", start: datetime | None = None) -> list[dict]:
    """Generate *n* minimal H1 bar dicts."""
    t0 = start or datetime(2024, 1, 1)
    bars = []
    for i in range(n):
        ts = t0 + timedelta(hours=i)
        bars.append({
            "instrument_id": instrument,
            "timeframe": "H1",
            "timestamp_utc": ts,
            "open": 1.1000 + i * 0.0001,
            "high": 1.1010 + i * 0.0001,
            "low": 1.0990 + i * 0.0001,
            "close": 1.1005 + i * 0.0001,
            "volume": 100.0,
            "source": "test",
            "derivation_version": "1",
        })
    return bars


def _make_signal_features(
    signal_id: str,
    feature_name: str,
    n: int,
    instrument: str = "EUR_USD",
    start: datetime | None = None,
    value_fn=None,
) -> list[dict]:
    """Generate feature rows that represent signal values."""
    t0 = start or datetime(2024, 1, 1)
    if value_fn is None:
        value_fn = lambda i: 0.5
    rows = []
    for i in range(n):
        ts = t0 + timedelta(hours=i)
        rows.append({
            "instrument_id": instrument,
            "timeframe": "H1",
            "timestamp_utc": ts,
            "feature_run_id": signal_id,
            "feature_name": feature_name,
            "feature_value": value_fn(i),
        })
    return rows


def _mock_metadata_repo_with_signal(signal_id: str, metadata: dict | None = None) -> MagicMock:
    """Return a MagicMock metadata_repo that returns a Signal record."""
    repo = MagicMock()
    signal_record = {
        "id": signal_id,
        "name": f"test_signal_{signal_id[:8]}",
        "signal_type": "automl_direction_prob",
        "definition_json": {},
        "metadata": metadata or {},
        "source_model_id": None,
        "version": 1,
        "created_at": datetime.utcnow().isoformat(),
    }
    repo.get_signal.return_value = signal_record
    return repo


# ---------------------------------------------------------------------------
# 1. Regression: signal_ids=None returns same result as before
# ---------------------------------------------------------------------------

class TestLoadBacktestFrameSignalRegression:
    def test_signal_ids_none_unchanged(self):
        """signal_ids=None should not change behavior."""
        store = DuckDBStore(":memory:")
        bars = _make_bars(10)
        store.upsert_bars_agg(bars)

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            signal_ids=None,
        )
        assert len(result) == 10
        # No signal columns present
        for bar in result:
            assert "automl_direction_prob" not in bar

    def test_signal_ids_empty_list_unchanged(self):
        """signal_ids=[] should behave the same as None."""
        store = DuckDBStore(":memory:")
        bars = _make_bars(5)
        store.upsert_bars_agg(bars)

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            signal_ids=[],
        )
        assert len(result) == 5


# ---------------------------------------------------------------------------
# 2. signal_ids=[id] adds signal columns to bar dicts
# ---------------------------------------------------------------------------

class TestSignalColumnsJoined:
    def test_signal_column_added(self):
        store = DuckDBStore(":memory:")
        bars = _make_bars(10)
        store.upsert_bars_agg(bars)

        sig_id = "sig_001"
        sig_features = _make_signal_features(
            sig_id, f"signal_{sig_id}_value", 10,
            value_fn=lambda i: 0.6 + i * 0.01,
        )
        store.upsert_features(sig_features)

        meta_repo = _mock_metadata_repo_with_signal(
            sig_id, {"field_name": "automl_direction_prob"}
        )

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )
        assert len(result) == 10
        for bar in result:
            assert "automl_direction_prob" in bar
            assert bar["automl_direction_prob"] is not None


# ---------------------------------------------------------------------------
# 3. Missing signal bars produce None (not NaN)
# ---------------------------------------------------------------------------

class TestMissingSignalBarsNone:
    def test_bars_without_signal_get_none(self):
        store = DuckDBStore(":memory:")
        bars = _make_bars(10)
        store.upsert_bars_agg(bars)

        sig_id = "sig_partial"
        # Only insert signal for first 5 bars
        sig_features = _make_signal_features(
            sig_id, f"signal_{sig_id}_value", 5,
            value_fn=lambda i: 0.7,
        )
        store.upsert_features(sig_features)

        meta_repo = _mock_metadata_repo_with_signal(
            sig_id, {"field_name": "direction_prob"}
        )

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )
        assert len(result) == 10
        # First 5 have values, last 5 are None
        for bar in result[:5]:
            assert bar["direction_prob"] == 0.7
        for bar in result[5:]:
            assert bar["direction_prob"] is None
            # Verify no NaN
            assert not (isinstance(bar["direction_prob"], float) and math.isnan(bar["direction_prob"]))


# ---------------------------------------------------------------------------
# 4. Column name resolution: field_name from Signal.metadata
# ---------------------------------------------------------------------------

class TestColumnNameResolution:
    def test_field_name_from_metadata(self):
        store = DuckDBStore(":memory:")
        bars = _make_bars(3)
        store.upsert_bars_agg(bars)

        sig_id = "sig_named"
        sig_features = _make_signal_features(
            sig_id, f"signal_{sig_id}_value", 3,
            value_fn=lambda i: 0.5,
        )
        store.upsert_features(sig_features)

        meta_repo = _mock_metadata_repo_with_signal(
            sig_id, {"field_name": "my_custom_signal"}
        )

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )
        # Column name should be "my_custom_signal" from metadata
        for bar in result:
            assert "my_custom_signal" in bar

    def test_fallback_column_name(self):
        """When metadata has no field_name, fall back to signal_{id}_value."""
        store = DuckDBStore(":memory:")
        bars = _make_bars(3)
        store.upsert_bars_agg(bars)

        sig_id = "sig_no_meta"
        sig_features = _make_signal_features(
            sig_id, f"signal_{sig_id}_value", 3,
            value_fn=lambda i: 0.5,
        )
        store.upsert_features(sig_features)

        meta_repo = _mock_metadata_repo_with_signal(sig_id, {})

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            metadata_repo=meta_repo,
            signal_ids=[sig_id],
        )
        expected_col = f"signal_{sig_id}_value"
        for bar in result:
            assert expected_col in bar


# ---------------------------------------------------------------------------
# 5. validate_signal_fields returns empty list when all fields present
# ---------------------------------------------------------------------------

class TestValidateSignalFields:
    def test_all_fields_present(self):
        definition = {
            "entry_long": {"field": "automl_direction_prob", "op": "gte", "value": 0.65},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
        }
        available = {"automl_direction_prob", "rsi_14"}
        result = validate_signal_fields(definition, available)
        assert result == []

    # 6. Returns unresolved field names
    def test_unresolved_fields(self):
        definition = {
            "entry_long": {"field": "missing_signal", "op": "gte", "value": 0.5},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
        }
        available = {"rsi_14"}
        result = validate_signal_fields(definition, available)
        assert "missing_signal" in result

    # 7. Handles all/any composites recursively
    def test_composite_all_any(self):
        definition = {
            "entry_long": {
                "all": [
                    {"field": "sig_a", "op": "gte", "value": 0.5},
                    {
                        "any": [
                            {"field": "sig_b", "op": "lt", "value": 0.3},
                            {"field": "sig_c", "op": "eq", "value": 1},
                        ]
                    },
                ]
            },
            "exit": {"field": "sig_d", "op": "gt", "value": 70},
        }
        available = {"sig_a", "sig_c"}
        result = validate_signal_fields(definition, available)
        assert "sig_b" in result
        assert "sig_d" in result
        assert "sig_a" not in result
        assert "sig_c" not in result

    # 8. Handles "not" nodes
    def test_not_node(self):
        definition = {
            "entry_long": {
                "not": {"field": "noise_flag", "op": "eq", "value": 1}
            },
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
        }
        available = {"rsi_14"}
        result = validate_signal_fields(definition, available)
        assert "noise_flag" in result

    # 9. Skips "ref" nodes
    def test_ref_node_skipped(self):
        definition = {
            "entry_long": {"ref": "my_condition"},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
            "named_conditions": {
                "my_condition": {"field": "known", "op": "gte", "value": 1},
            },
        }
        # "known" is in named_conditions, but ref nodes are skipped at top level
        # However named_conditions themselves ARE walked
        available = {"rsi_14"}
        result = validate_signal_fields(definition, available)
        # The ref itself is skipped, but the named_condition body is walked
        assert "known" in result
        # rsi_14 is available, so not in result
        assert "rsi_14" not in result

    def test_ref_in_entry_does_not_error(self):
        """A ref node in entry_long should not produce false positives."""
        definition = {
            "entry_long": {"ref": "some_cond"},
            "exit": {"field": "atr_14", "op": "gt", "value": 0},
        }
        available = {"atr_14"}
        # Should not raise and should not flag the ref
        result = validate_signal_fields(definition, available)
        assert result == []

    # Field2 validation
    def test_field2_checked(self):
        definition = {
            "entry_long": {"field": "close", "op": "gt", "field2": "ema_50"},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
        }
        available = {"close", "rsi_14"}
        result = validate_signal_fields(definition, available)
        assert "ema_50" in result


# ---------------------------------------------------------------------------
# 10. automl_signal.py sets metadata["field_name"]
# ---------------------------------------------------------------------------

class TestAutoMLSignalFieldName:
    def test_direction_prob_field_name(self):
        from backend.schemas.models import AutoMLJobRecord
        from backend.signals.automl_signal import create_signal_from_automl

        record = AutoMLJobRecord(
            id="rec1",
            job_id="job1",
            instrument_id="EUR_USD",
            timeframe="H1",
            feature_run_id="fr1",
            model_id="m1",
            target_type="direction",
        )
        repo = MagicMock()
        signal = create_signal_from_automl(record, repo)
        assert signal.metadata["field_name"] == "automl_direction_prob"

    def test_return_bucket_field_name(self):
        from backend.schemas.models import AutoMLJobRecord
        from backend.signals.automl_signal import create_signal_from_automl

        record = AutoMLJobRecord(
            id="rec2",
            job_id="job2",
            instrument_id="EUR_USD",
            timeframe="H1",
            feature_run_id="fr2",
            model_id="m2",
            target_type="return_bucket",
        )
        repo = MagicMock()
        signal = create_signal_from_automl(record, repo)
        assert signal.metadata["field_name"] == "automl_return_bucket"


# ---------------------------------------------------------------------------
# 11. create_signal_from_hmm sets metadata["field_name"] = "hmm_regime"
# ---------------------------------------------------------------------------

class TestHMMSignalFieldName:
    def test_hmm_regime_field_name(self):
        from backend.signals.bank import create_signal_from_hmm

        repo = MagicMock()
        repo.get_model.return_value = {
            "id": "model1",
            "instrument_id": "EUR_USD",
            "timeframe": "H1",
        }
        signal = create_signal_from_hmm(
            name="test_hmm",
            model_id="model1",
            feature_run_id="fr1",
            metadata_repo=repo,
        )
        assert signal.metadata["field_name"] == "hmm_regime"


# ---------------------------------------------------------------------------
# 12. Multiple signal_ids are all merged into bar dicts
# ---------------------------------------------------------------------------

class TestMultipleSignals:
    def test_two_signals_merged(self):
        store = DuckDBStore(":memory:")
        bars = _make_bars(5)
        store.upsert_bars_agg(bars)

        sig_a = "sig_aaa"
        sig_b = "sig_bbb"

        store.upsert_features(_make_signal_features(
            sig_a, f"signal_{sig_a}_value", 5,
            value_fn=lambda i: 0.3,
        ))
        store.upsert_features(_make_signal_features(
            sig_b, f"signal_{sig_b}_value", 5,
            value_fn=lambda i: 0.9,
        ))

        meta_repo = MagicMock()

        def _get_signal(sid):
            return {
                "id": sid,
                "name": f"signal_{sid}",
                "signal_type": "automl_direction_prob",
                "definition_json": {},
                "metadata": {"field_name": f"col_{sid}"},
                "source_model_id": None,
                "version": 1,
            }

        meta_repo.get_signal.side_effect = _get_signal

        result = load_backtest_frame(
            "EUR_USD", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
            market_repo=store,
            metadata_repo=meta_repo,
            signal_ids=[sig_a, sig_b],
        )
        assert len(result) == 5
        for bar in result:
            assert f"col_{sig_a}" in bar
            assert f"col_{sig_b}" in bar
            assert bar[f"col_{sig_a}"] == 0.3
            assert bar[f"col_{sig_b}"] == 0.9
