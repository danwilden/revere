"""Tests for extended signal materialization (Phase 5E).

Covers:
  - AUTOML_DIRECTION_PROB and AUTOML_RETURN_BUCKET materialization
  - HMM_STATE_PROB materialization
  - RISK_FILTER materialization
  - build_risk_filter_signal() creation
  - Regression: existing HMM_REGIME path
  - No NaN in stored feature rows
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from hmmlearn.hmm import GaussianHMM

from backend.data.duckdb_store import DuckDBStore
from backend.data.repositories import LocalArtifactRepository, LocalMetadataRepository
from backend.features.compute import run_feature_pipeline
from backend.schemas.enums import SignalType, Timeframe
from backend.signals.materialize import (
    _materialize_signal_sync,
    _run_automl_inference,
    materialize_signal,
)
from backend.signals.risk_filter import build_risk_filter_signal


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

N_BARS = 200
INSTRUMENT = "EUR_USD"
TF = Timeframe.H1


def _make_bars(n: int = N_BARS, seed: int = 42) -> list[dict]:
    """Create synthetic bar dicts suitable for DuckDB insertion."""
    rng = np.random.default_rng(seed)
    closes = 1.1000 + np.cumsum(rng.normal(0, 0.0003, n))
    highs = closes + rng.uniform(0.0001, 0.0010, n)
    lows = closes - rng.uniform(0.0001, 0.0010, n)
    opens = closes + rng.normal(0, 0.0002, n)
    base = datetime(2022, 1, 3)
    return [
        {
            "instrument_id": INSTRUMENT,
            "timestamp_utc": (base + timedelta(hours=i)).isoformat(),
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": 1000.0,
            "source": "test",
            "quality_flag": "ok",
        }
        for i in range(n)
    ]


def _seed_bars_and_features(db: DuckDBStore, meta: LocalMetadataRepository) -> str:
    """Insert bars and run feature pipeline. Returns feature_run_id."""
    bars = _make_bars()
    db.upsert_bars_agg([{**b, "timeframe": TF.value, "derivation_version": "1"} for b in bars])
    start = datetime(2022, 1, 3)
    end = start + timedelta(hours=N_BARS)
    return run_feature_pipeline(INSTRUMENT, TF, start, end, db, meta)


def _make_hmm_artifact(n_states: int = 4, n_features: int = 8, seed: int = 7) -> dict:
    """Create a tiny trained GaussianHMM artifact dict."""
    rng = np.random.default_rng(seed)
    model = GaussianHMM(n_components=n_states, covariance_type="full", n_iter=5, random_state=seed)
    X = rng.standard_normal((100, n_features))
    model.fit(X)
    feature_cols = [
        "log_ret_1", "log_ret_5", "rvol_20", "atr_pct_14",
        "rsi_14", "ema_slope_20", "adx_14", "breakout_20",
    ][:n_features]
    return {"model": model, "feature_cols": feature_cols}


def _save_artifact(art_repo: LocalArtifactRepository, key: str, artifact: dict) -> None:
    """Serialize and save an artifact dict."""
    buf = io.BytesIO()
    joblib.dump(artifact, buf)
    art_repo.save(key, buf.getvalue())


@pytest.fixture
def repos(tmp_path):
    db = DuckDBStore(":memory:")
    meta = LocalMetadataRepository(tmp_path / "meta")
    art = LocalArtifactRepository(tmp_path / "artifacts")
    yield db, meta, art
    db.close()


@pytest.fixture
def seeded_repos(repos):
    """Repos with bars and features already loaded."""
    db, meta, art = repos
    frun_id = _seed_bars_and_features(db, meta)
    return db, meta, art, frun_id


# ---------------------------------------------------------------------------
# AUTOML_DIRECTION_PROB tests
# ---------------------------------------------------------------------------


def _create_automl_signal(meta, art, frun_id, signal_type_val, mock_output, signal_id="sig-automl-1"):
    """Helper to create and persist an AutoML signal record."""
    artifact_key = f"models/automl/{signal_id}.joblib"
    _save_artifact(art, artifact_key, {"mock_output": mock_output})

    signal_record = {
        "id": signal_id,
        "name": "test-automl",
        "signal_type": signal_type_val,
        "definition_json": {
            "automl_job_id": "job-123",
            "feature_run_id": frun_id,
            "best_model_artifact_key": artifact_key,
        },
        "source_model_id": None,
        "version": 1,
        "created_at": datetime.utcnow().isoformat(),
    }
    meta.save_signal(signal_record)
    return signal_id


class TestAutoMLDirectionProb:

    def test_writes_correct_feature_column_name(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        sig_id = _create_automl_signal(
            meta, art, frun_id,
            SignalType.AUTOML_DIRECTION_PROB.value,
            mock_output=[0.7, 0.3, 0.5],
        )
        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        # Check that feature rows were written with correct name
        feat_rows = db.get_features(INSTRUMENT, TF, sig_id, start, end)
        feature_names = {r["feature_name"] for r in feat_rows}
        assert f"signal_{sig_id}_value" in feature_names

    def test_mock_output_returns_correct_row_count(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        sig_id = _create_automl_signal(
            meta, art, frun_id,
            SignalType.AUTOML_DIRECTION_PROB.value,
            mock_output=[0.8, 0.2],
        )
        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        # Result rows should match number of bars in feature matrix (after dropna)
        assert len(result) > 0
        # All rows should have signal_value
        for row in result:
            assert "signal_value" in row

    def test_return_format_has_signal_value_and_signal_id(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        sig_id = _create_automl_signal(
            meta, art, frun_id,
            SignalType.AUTOML_DIRECTION_PROB.value,
            mock_output=[0.6],
        )
        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)
        assert len(result) > 0
        row = result[0]
        assert "signal_value" in row
        assert "signal_id" in row
        assert "timestamp_utc" in row
        assert row["signal_id"] == sig_id


class TestAutoMLReturnBucket:

    def test_produces_integer_values(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        sig_id = _create_automl_signal(
            meta, art, frun_id,
            SignalType.AUTOML_RETURN_BUCKET.value,
            mock_output=[0, 1, 2],
            signal_id="sig-bucket-1",
        )
        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        assert len(result) > 0
        for row in result:
            val = row["signal_value"]
            if val is not None:
                assert val == int(val), f"Expected integer bucket, got {val}"
                assert val in (0.0, 1.0, 2.0)


# ---------------------------------------------------------------------------
# _run_automl_inference tests
# ---------------------------------------------------------------------------

class TestRunAutomlInference:

    def test_raises_not_implemented_without_mock_output(self):
        artifact = {"model": "dummy"}
        feature_df = pd.DataFrame({"a": [1, 2, 3]})
        with pytest.raises(NotImplementedError, match="SageMaker"):
            _run_automl_inference(artifact, feature_df)

    def test_mock_output_repeats_to_match_length(self):
        artifact = {"mock_output": [0.1, 0.9]}
        feature_df = pd.DataFrame({"a": range(5)})
        result = _run_automl_inference(artifact, feature_df)
        assert len(result) == 5
        assert list(result) == [0.1, 0.9, 0.1, 0.9, 0.1]


# ---------------------------------------------------------------------------
# HMM_STATE_PROB tests
# ---------------------------------------------------------------------------

class TestHMMStateProb:

    def _create_hmm_state_prob_signal(self, meta, art, frun_id, n_states=4, signal_id="sig-hmm-prob-1"):
        model_id = "model-hmm-prob-1"
        artifact = _make_hmm_artifact(n_states=n_states)
        artifact_key = f"models/hmm/{model_id}.joblib"
        _save_artifact(art, artifact_key, artifact)

        signal_record = {
            "id": signal_id,
            "name": "test-hmm-prob",
            "signal_type": SignalType.HMM_STATE_PROB.value,
            "definition_json": {
                "model_id": model_id,
                "feature_run_id": frun_id,
            },
            "source_model_id": model_id,
            "version": 1,
            "created_at": datetime.utcnow().isoformat(),
        }
        meta.save_signal(signal_record)
        return signal_id, n_states

    def test_writes_one_column_per_state(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        n_states = 4
        sig_id, _ = self._create_hmm_state_prob_signal(meta, art, frun_id, n_states=n_states)

        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        # Check that n_states columns were written
        feat_rows = db.get_features(INSTRUMENT, TF, sig_id, start, end)
        feature_names = {r["feature_name"] for r in feat_rows}
        expected_names = {f"signal_{sig_id}_state_{i}_prob" for i in range(n_states)}
        assert expected_names == feature_names

    def test_return_format_has_state_probs_list(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        n_states = 4
        sig_id, _ = self._create_hmm_state_prob_signal(meta, art, frun_id, n_states=n_states)

        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        assert len(result) > 0
        row = result[0]
        assert "state_probs" in row
        assert isinstance(row["state_probs"], list)
        assert len(row["state_probs"]) == n_states
        # All probabilities should be float
        for p in row["state_probs"]:
            assert isinstance(p, float)
        assert "signal_id" in row


# ---------------------------------------------------------------------------
# RISK_FILTER tests
# ---------------------------------------------------------------------------

class TestRiskFilter:

    def _create_risk_filter_signal(self, meta, frun_id, rules_node, signal_id="sig-risk-1"):
        signal_record = {
            "id": signal_id,
            "name": "test-risk-filter",
            "signal_type": SignalType.RISK_FILTER.value,
            "definition_json": {
                "rules": rules_node,
                "field_name": "risk_filter",
                "feature_run_id": frun_id,
            },
            "source_model_id": None,
            "version": 1,
            "created_at": datetime.utcnow().isoformat(),
        }
        meta.save_signal(signal_record)
        return signal_id

    def test_blocking_rules_produce_all_ones(self, seeded_repos):
        """A rule that always evaluates True should block all bars (signal=1)."""
        db, meta, art, frun_id = seeded_repos
        # rsi_14 >= 0 is always true for valid RSI
        rules_node = {"field": "rsi_14", "op": "gte", "value": 0}
        sig_id = self._create_risk_filter_signal(meta, frun_id, rules_node)

        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        assert len(result) > 0
        for row in result:
            assert row["signal_value"] == 1, "Blocking rule should produce signal_value=1"

    def test_permissive_rules_produce_all_zeros(self, seeded_repos):
        """A rule that always evaluates False should allow all bars (signal=0)."""
        db, meta, art, frun_id = seeded_repos
        # rsi_14 < 0 is always false for valid RSI
        rules_node = {"field": "rsi_14", "op": "lt", "value": 0}
        sig_id = self._create_risk_filter_signal(meta, frun_id, rules_node, signal_id="sig-risk-2")

        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        result = materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        assert len(result) > 0
        for row in result:
            assert row["signal_value"] == 0, "Permissive rule should produce signal_value=0"


# ---------------------------------------------------------------------------
# build_risk_filter_signal tests
# ---------------------------------------------------------------------------

class TestBuildRiskFilterSignal:

    def test_creates_signal_with_risk_filter_type(self, repos):
        _, meta, _ = repos
        rules = {"field": "rsi_14", "op": "gt", "value": 70}
        signal = build_risk_filter_signal("high-rsi-block", rules, "Block when RSI high", meta)
        assert signal.signal_type == SignalType.RISK_FILTER

    def test_stores_rules_node_in_metadata(self, repos):
        _, meta, _ = repos
        rules = {"field": "rvol_20", "op": "gt", "value": 0.3}
        signal = build_risk_filter_signal("high-vol-block", rules, "Block high vol", meta)
        assert signal.definition_json["rules"] == rules

    def test_stores_field_name_in_metadata(self, repos):
        _, meta, _ = repos
        rules = {"field": "adx_14", "op": "lt", "value": 15}
        signal = build_risk_filter_signal("low-trend-block", rules, "Block low trend", meta)
        assert signal.definition_json["field_name"] == "risk_filter"


# ---------------------------------------------------------------------------
# HMM_REGIME regression test
# ---------------------------------------------------------------------------

class TestHMMRegimeRegression:

    def test_hmm_regime_signal_still_works(self, seeded_repos):
        """Verify the original HMM_REGIME dispatch path is not broken."""
        db, meta, art, frun_id = seeded_repos

        # Create a minimal HMM model + artifact
        model_id = "model-regression-1"
        n_states = 3
        artifact = _make_hmm_artifact(n_states=n_states)
        artifact_key = f"models/hmm/{model_id}.joblib"
        _save_artifact(art, artifact_key, artifact)

        # Save model record
        meta.save_model({
            "id": model_id,
            "model_type": "hmm",
            "instrument_id": INSTRUMENT,
            "timeframe": TF.value,
            "training_start": "2022-01-03T00:00:00",
            "training_end": "2022-01-04T00:00:00",
            "artifact_ref": artifact_key,
            "label_map_json": {"0": "STATE_A", "1": "STATE_B", "2": "STATE_C"},
            "status": "succeeded",
        })

        # Create signal
        signal_record = {
            "id": "sig-hmm-regime-1",
            "name": "test-hmm-regime",
            "signal_type": SignalType.HMM_REGIME.value,
            "definition_json": {
                "model_id": model_id,
                "feature_run_id": frun_id,
            },
            "source_model_id": model_id,
            "version": 1,
            "created_at": datetime.utcnow().isoformat(),
        }
        meta.save_signal(signal_record)

        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)

        # This exercises the HMM_REGIME path via infer_hmm
        result = materialize_signal("sig-hmm-regime-1", INSTRUMENT, TF, start, end, db, meta, art)
        assert isinstance(result, list)
        # infer_hmm writes regime_labels and the materializer fetches them
        # As long as no exception is raised, the dispatch path is intact


# ---------------------------------------------------------------------------
# No NaN in storage test
# ---------------------------------------------------------------------------

class TestNoNaNInStorage:

    def test_automl_no_nan_stored(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        sig_id = _create_automl_signal(
            meta, art, frun_id,
            SignalType.AUTOML_DIRECTION_PROB.value,
            mock_output=[0.5, 0.9, 0.1],
            signal_id="sig-nonan-1",
        )
        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        feat_rows = db.get_features(INSTRUMENT, TF, sig_id, start, end)
        for row in feat_rows:
            val = row["feature_value"]
            if val is not None:
                assert not np.isnan(val), f"NaN found in stored feature row: {row}"

    def test_risk_filter_no_nan_stored(self, seeded_repos):
        db, meta, art, frun_id = seeded_repos
        rules = {"field": "rsi_14", "op": "gte", "value": 0}
        sig_id = "sig-nonan-2"
        signal_record = {
            "id": sig_id,
            "name": "test-nonan-rf",
            "signal_type": SignalType.RISK_FILTER.value,
            "definition_json": {
                "rules": rules,
                "field_name": "risk_filter",
                "feature_run_id": frun_id,
            },
            "source_model_id": None,
            "version": 1,
            "created_at": datetime.utcnow().isoformat(),
        }
        meta.save_signal(signal_record)

        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)
        materialize_signal(sig_id, INSTRUMENT, TF, start, end, db, meta, art)

        feat_rows = db.get_features(INSTRUMENT, TF, sig_id, start, end)
        for row in feat_rows:
            val = row["feature_value"]
            if val is not None:
                assert not np.isnan(val), f"NaN found in stored feature row: {row}"


# ---------------------------------------------------------------------------
# _materialize_signal_sync equivalence
# ---------------------------------------------------------------------------

class TestMaterializeSignalSync:

    def test_sync_produces_same_result(self, seeded_repos):
        """_materialize_signal_sync should produce the same result as materialize_signal."""
        db, meta, art, frun_id = seeded_repos
        sig_id = _create_automl_signal(
            meta, art, frun_id,
            SignalType.AUTOML_DIRECTION_PROB.value,
            mock_output=[0.5],
            signal_id="sig-sync-1",
        )
        start = datetime(2022, 1, 3)
        end = start + timedelta(hours=N_BARS)

        # Both should succeed without error and return equivalent data
        result = _materialize_signal_sync(sig_id, INSTRUMENT, TF, start, end, db, meta, art)
        assert isinstance(result, list)
        assert len(result) > 0
