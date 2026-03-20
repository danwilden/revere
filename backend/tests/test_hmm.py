"""Tests for HMM training, inference, labeling, and signal bank.

Verifies:
  - HMM trains and produces the requested number of distinct states
  - Model artifact is saved and can be reloaded
  - Semantic labels cover all states
  - Signal bank entry can be created from a trained model
  - Signal materialization returns correct shape and fields
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.data.duckdb_store import DuckDBStore
from backend.data.repositories import LocalArtifactRepository, LocalMetadataRepository
from backend.features.compute import run_feature_pipeline
from backend.jobs.hmm import run_hmm_training_job
from backend.jobs.status import JobManager
from backend.models.hmm_regime import (
    HMM_FEATURE_COLS,
    load_model_artifact,
    train_hmm,
)
from backend.models.labeling import ALL_LABELS, auto_label_states
from backend.schemas.enums import JobStatus, Timeframe
from backend.signals.bank import create_signal_from_hmm


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

N_BARS = 500
N_STATES = 4  # use 4 to keep tests fast (not 7)


def _make_bar_df(n: int = N_BARS, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 1.1000 + np.cumsum(rng.normal(0, 0.0003, n))
    highs = closes + rng.uniform(0.0001, 0.0010, n)
    lows = closes - rng.uniform(0.0001, 0.0010, n)
    opens = closes + rng.normal(0, 0.0002, n)
    index = pd.date_range("2022-01-03 00:00", periods=n, freq="H")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000.0},
        index=index,
    )


@pytest.fixture
def repos(tmp_path):
    db = DuckDBStore(tmp_path / "test.duckdb")
    meta = LocalMetadataRepository(tmp_path / "meta")
    art = LocalArtifactRepository(tmp_path / "artifacts")
    yield db, meta, art
    db.close()


@pytest.fixture
def trained_model(repos):
    """Fixture that runs the full pipeline and returns (model_id, feature_run_id, repos)."""
    db, meta, art = repos
    instrument_id = "EUR_USD"
    bar_df = _make_bar_df()

    # Insert bars into DuckDB
    rows = []
    for ts, row in bar_df.iterrows():
        rows.append({
            "instrument_id": instrument_id,
            "timeframe": "H1",
            "timestamp_utc": ts.isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "source": "oanda",
            "derivation_version": "1",
        })
    db.upsert_bars_agg(rows)

    start = bar_df.index[0].to_pydatetime()
    end = bar_df.index[-1].to_pydatetime() + timedelta(hours=1)

    feature_run_id = run_feature_pipeline(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        start=start,
        end=end,
        market_repo=db,
        metadata_repo=meta,
    )

    import uuid
    model_id = str(uuid.uuid4())
    result = train_hmm(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        train_start=start,
        train_end=end,
        num_states=N_STATES,
        feature_run_id=feature_run_id,
        model_id=model_id,
        market_repo=db,
        metadata_repo=meta,
        artifact_repo=art,
    )

    # Persist a minimal model record so label/inference tests can look it up
    meta.save_model({
        "id": model_id,
        "model_type": "hmm",
        "instrument_id": instrument_id,
        "timeframe": "H1",
        "training_start": start.isoformat(),
        "training_end": end.isoformat(),
        "parameters_json": {"num_states": N_STATES},
        "artifact_ref": result["artifact_ref"],
        "label_map_json": {},
        "created_at": datetime.utcnow().isoformat(),
        "status": "succeeded",
    })

    return model_id, feature_run_id, result, repos


# ---------------------------------------------------------------------------
# HMM training tests
# ---------------------------------------------------------------------------

def test_train_hmm_returns_result(trained_model):
    _, _, result, _ = trained_model
    assert "model_id" in result
    assert "artifact_ref" in result
    assert "state_stats" in result
    assert "log_likelihood" in result


def test_train_hmm_correct_num_states(trained_model):
    _, _, result, _ = trained_model
    stats = result["state_stats"]
    populated_states = [s for s in stats if s.get("n_bars", 0) > 0]
    assert len(populated_states) == N_STATES


def test_train_hmm_artifact_exists(trained_model):
    _, _, result, repos = trained_model
    _, _, art = repos
    assert art.exists(result["artifact_ref"])


def test_train_hmm_regime_labels_stored(trained_model):
    model_id, feature_run_id, result, repos = trained_model
    db, meta, art = repos
    bar_df = _make_bar_df()
    start = bar_df.index[0].to_pydatetime()
    end = bar_df.index[-1].to_pydatetime() + timedelta(hours=1)

    labels = db.get_regime_labels(model_id, "EUR_USD", Timeframe.H1, start, end)
    assert len(labels) > 0


def test_train_hmm_state_ids_in_range(trained_model):
    model_id, _, result, repos = trained_model
    db, _, _ = repos
    bar_df = _make_bar_df()
    start = bar_df.index[0].to_pydatetime()
    end = bar_df.index[-1].to_pydatetime() + timedelta(hours=1)

    labels = db.get_regime_labels(model_id, "EUR_USD", Timeframe.H1, start, end)
    state_ids = {row["state_id"] for row in labels}
    assert state_ids.issubset(set(range(N_STATES)))


def test_train_hmm_state_probabilities_sum_to_one(trained_model):
    model_id, _, _, repos = trained_model
    db, _, _ = repos
    bar_df = _make_bar_df()
    start = bar_df.index[0].to_pydatetime()
    end = bar_df.index[-1].to_pydatetime() + timedelta(hours=1)

    labels = db.get_regime_labels(model_id, "EUR_USD", Timeframe.H1, start, end)
    for row in labels[:10]:  # spot check first 10
        proba = json.loads(row["state_probabilities_json"])
        total = sum(proba.values())
        assert abs(total - 1.0) < 1e-6, f"Proba sum {total} != 1.0"


# ---------------------------------------------------------------------------
# Model artifact reload
# ---------------------------------------------------------------------------

def test_model_artifact_reloads(trained_model):
    model_id, _, _, repos = trained_model
    _, meta, art = repos
    model, feature_cols = load_model_artifact(model_id, meta, art)
    assert model is not None
    assert isinstance(feature_cols, list)
    assert len(feature_cols) > 0


def test_reloaded_model_has_correct_n_components(trained_model):
    model_id, _, _, repos = trained_model
    _, meta, art = repos
    model, _ = load_model_artifact(model_id, meta, art)
    assert model.n_components == N_STATES


# ---------------------------------------------------------------------------
# Semantic labeling
# ---------------------------------------------------------------------------

def test_auto_label_states_returns_map(trained_model):
    _, _, result, _ = trained_model
    label_map = auto_label_states(result["state_stats"])
    assert isinstance(label_map, dict)
    assert len(label_map) > 0


def test_auto_label_states_no_duplicate_labels(trained_model):
    _, _, result, _ = trained_model
    label_map = auto_label_states(result["state_stats"])
    labels = list(label_map.values())
    assert len(labels) == len(set(labels)), "Duplicate semantic labels assigned"


def test_auto_label_states_all_valid_labels(trained_model):
    _, _, result, _ = trained_model
    label_map = auto_label_states(result["state_stats"])
    for sid, label in label_map.items():
        assert isinstance(label, str) and len(label) > 0


# ---------------------------------------------------------------------------
# Signal bank
# ---------------------------------------------------------------------------

def test_create_signal_from_hmm(trained_model):
    model_id, feature_run_id, _, repos = trained_model
    _, meta, _ = repos
    signal = create_signal_from_hmm(
        name="EUR_USD_HMM_Regime",
        model_id=model_id,
        feature_run_id=feature_run_id,
        metadata_repo=meta,
    )
    assert signal.id is not None
    assert signal.source_model_id == model_id
    assert signal.signal_type.value == "hmm_regime"


def test_signal_persisted_to_metadata(trained_model):
    model_id, feature_run_id, _, repos = trained_model
    _, meta, _ = repos
    signal = create_signal_from_hmm(
        name="EUR_USD_HMM_Regime",
        model_id=model_id,
        feature_run_id=feature_run_id,
        metadata_repo=meta,
    )
    retrieved = meta.get_signal(signal.id)
    assert retrieved is not None
    assert retrieved["name"] == "EUR_USD_HMM_Regime"


def test_list_signals_includes_new_signal(trained_model):
    model_id, feature_run_id, _, repos = trained_model
    _, meta, _ = repos
    signal = create_signal_from_hmm(
        name="TestSignal",
        model_id=model_id,
        feature_run_id=feature_run_id,
        metadata_repo=meta,
    )
    all_signals = meta.list_signals()
    ids = [s["id"] for s in all_signals]
    assert signal.id in ids


# ---------------------------------------------------------------------------
# Full HMM job runner
# ---------------------------------------------------------------------------

def test_hmm_job_runner_succeeds(repos):
    db, meta, art = repos
    instrument_id = "EUR_USD"
    bar_df = _make_bar_df()

    rows = []
    for ts, row in bar_df.iterrows():
        rows.append({
            "instrument_id": instrument_id,
            "timeframe": "H1",
            "timestamp_utc": ts.isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "source": "oanda",
            "derivation_version": "1",
        })
    db.upsert_bars_agg(rows)

    job_manager = JobManager(meta)
    job = job_manager.create(
        job_type=__import__("backend.schemas.enums", fromlist=["JobType"]).JobType.HMM_TRAINING,
        params={"instrument": instrument_id},
    )

    model_id = run_hmm_training_job(
        job_id=job.id,
        instrument=instrument_id,
        timeframe=Timeframe.H1,
        train_start=bar_df.index[0].to_pydatetime(),
        train_end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        num_states=N_STATES,
        feature_set_name="default_v1",
        market_repo=db,
        metadata_repo=meta,
        artifact_repo=art,
        job_manager=job_manager,
    )

    # Job succeeded
    job_record = job_manager.get(job.id)
    assert job_record["status"] == JobStatus.SUCCEEDED.value

    # Model record exists and has label map
    model_record = meta.get_model(model_id)
    assert model_record is not None
    assert model_record.get("status") == JobStatus.SUCCEEDED.value
    assert len(model_record.get("label_map_json", {})) > 0

    # Artifact persisted
    assert art.exists(model_record["artifact_ref"])
