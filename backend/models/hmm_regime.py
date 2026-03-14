"""HMM regime model — training and inference.

Uses hmmlearn GaussianHMM to learn latent market regimes from features.
Per-pair training only (no cross-pair HMM in MVP).

Training:
  - Fit only on [train_start, train_end] window (no leakage)
  - Viterbi decode to get state sequence
  - Store per-timestamp state probabilities in regime_labels table
  - Persist trained artifact via ArtifactRepository

Inference (out-of-sample):
  - Load persisted artifact
  - predict_proba on new bars
  - Store new regime labels
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime
from typing import Any

import joblib
import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

from backend.data.repositories import ArtifactRepository, MarketDataRepository, MetadataRepository
from backend.features.compute import load_feature_matrix, run_feature_pipeline
from backend.schemas.enums import JobStatus, Timeframe

# Columns used for HMM fitting — must exist in the feature matrix.
# Order matters: covariance structure is learned per-column ordering.
HMM_FEATURE_COLS = [
    "log_ret_1",
    "log_ret_5",
    "rvol_20",
    "atr_pct_14",
    "rsi_14",
    "ema_slope_20",
    "adx_14",
    "breakout_20",
]


def _artifact_key(model_id: str) -> str:
    return f"models/hmm/{model_id}.joblib"


def train_hmm(
    instrument_id: str,
    timeframe: Timeframe,
    train_start: datetime,
    train_end: datetime,
    num_states: int,
    feature_run_id: str,
    model_id: str,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
    random_state: int = 42,
    n_iter: int = 100,
) -> dict[str, Any]:
    """Fit a GaussianHMM on the training window.

    Returns:
        dict with model_id, num_states, feature_run_id, artifact_ref,
        and per-state statistics used for labeling.
    """
    # Load feature matrix
    feature_matrix = load_feature_matrix(
        instrument_id, timeframe, feature_run_id,
        train_start, train_end, market_repo, dropna=True,
    )
    if feature_matrix.empty:
        raise ValueError(
            f"No feature data for {instrument_id} {timeframe.value} "
            f"in [{train_start}, {train_end}). Run feature pipeline first."
        )

    # Select and validate columns
    available = [c for c in HMM_FEATURE_COLS if c in feature_matrix.columns]
    if len(available) < 3:
        raise ValueError(f"Insufficient feature columns: {available}")

    X = feature_matrix[available].values  # (T, n_features)

    # Fit HMM
    model = GaussianHMM(
        n_components=num_states,
        covariance_type="full",
        n_iter=n_iter,
        random_state=random_state,
        verbose=False,
    )
    model.fit(X)

    # Decode state sequence (Viterbi)
    state_seq = model.predict(X)
    # State probabilities (forward-backward)
    state_proba = model.predict_proba(X)  # (T, num_states)

    # Compute per-state statistics for semantic labeling
    state_stats = _compute_state_stats(feature_matrix, state_seq, available, num_states)

    # Persist model artifact
    buf = io.BytesIO()
    joblib.dump({"model": model, "feature_cols": available}, buf)
    artifact_ref = artifact_repo.save(_artifact_key(model_id), buf.getvalue())

    # Persist regime labels to DuckDB
    _store_regime_labels(
        model_id, instrument_id, timeframe,
        feature_matrix.index, state_seq, state_proba,
        label_map={},  # filled in after semantic labeling
        market_repo=market_repo,
    )

    return {
        "model_id": model_id,
        "num_states": num_states,
        "feature_cols": available,
        "feature_run_id": feature_run_id,
        "artifact_ref": artifact_ref,
        "state_stats": state_stats,
        "log_likelihood": float(model.score(X)),
    }


def infer_hmm(
    model_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    infer_start: datetime,
    infer_end: datetime,
    feature_run_id: str,
    label_map: dict[str, str],
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> int:
    """Run out-of-sample inference on a loaded model.

    Loads the persisted model, predicts state sequence and probabilities
    for [infer_start, infer_end), stores results in regime_labels.

    Returns:
        Number of bars labelled.
    """
    model_record = metadata_repo.get_model(model_id)
    if not model_record:
        raise ValueError(f"Model {model_id} not found")

    artifact_ref = model_record.get("artifact_ref")
    if not artifact_ref or not artifact_repo.exists(artifact_ref):
        raise ValueError(f"Model artifact not found at {artifact_ref}")

    artifact = joblib.load(io.BytesIO(artifact_repo.load(artifact_ref)))
    model: GaussianHMM = artifact["model"]
    feature_cols: list[str] = artifact["feature_cols"]

    feature_matrix = load_feature_matrix(
        instrument_id, timeframe, feature_run_id,
        infer_start, infer_end, market_repo, dropna=True,
    )
    if feature_matrix.empty:
        return 0

    available = [c for c in feature_cols if c in feature_matrix.columns]
    X = feature_matrix[available].values

    state_seq = model.predict(X)
    state_proba = model.predict_proba(X)

    _store_regime_labels(
        model_id, instrument_id, timeframe,
        feature_matrix.index, state_seq, state_proba,
        label_map=label_map,
        market_repo=market_repo,
    )
    return len(state_seq)


def _store_regime_labels(
    model_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    index: pd.DatetimeIndex,
    state_seq: np.ndarray,
    state_proba: np.ndarray,
    label_map: dict[str, str],
    market_repo: MarketDataRepository,
) -> None:
    rows = []
    for i, ts in enumerate(index):
        state_id = int(state_seq[i])
        proba = {str(s): float(state_proba[i, s]) for s in range(state_proba.shape[1])}
        regime_label = label_map.get(str(state_id), f"state_{state_id}")
        rows.append({
            "model_id": model_id,
            "instrument_id": instrument_id,
            "timeframe": timeframe.value,
            "timestamp_utc": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "state_id": state_id,
            "regime_label": regime_label,
            "state_probabilities_json": json.dumps(proba),
        })

    BATCH = 1000
    for i in range(0, len(rows), BATCH):
        market_repo.upsert_regime_labels(rows[i : i + BATCH])


def _compute_state_stats(
    feature_matrix: pd.DataFrame,
    state_seq: np.ndarray,
    feature_cols: list[str],
    num_states: int,
) -> list[dict]:
    """Compute descriptive statistics per state for semantic labeling."""
    stats = []
    for s in range(num_states):
        mask = state_seq == s
        n_bars = int(mask.sum())
        if n_bars == 0:
            stats.append({"state_id": s, "n_bars": 0})
            continue

        state_df = feature_matrix[mask]
        stat: dict[str, Any] = {"state_id": s, "n_bars": n_bars}

        if "log_ret_1" in state_df.columns:
            stat["mean_return"] = float(state_df["log_ret_1"].mean())
            # directional persistence: fraction of same-sign consecutive returns
            rets = state_df["log_ret_1"].dropna()
            if len(rets) > 1:
                signs = np.sign(rets.values)
                stat["directional_persistence"] = float(
                    (signs[1:] == signs[:-1]).mean()
                )
            else:
                stat["directional_persistence"] = 0.5

        if "rvol_20" in state_df.columns:
            stat["mean_volatility"] = float(state_df["rvol_20"].mean())

        if "adx_14" in state_df.columns:
            stat["mean_adx"] = float(state_df["adx_14"].mean())

        if "atr_pct_14" in state_df.columns:
            stat["mean_atr_pct"] = float(state_df["atr_pct_14"].mean())

        stats.append(stat)
    return stats


def load_model_artifact(
    model_id: str,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> tuple[GaussianHMM, list[str]]:
    """Load a persisted HMM model and its feature column list."""
    record = metadata_repo.get_model(model_id)
    if not record:
        raise ValueError(f"Model {model_id} not found")
    ref = record.get("artifact_ref")
    if not ref or not artifact_repo.exists(ref):
        raise ValueError(f"Artifact not found: {ref}")
    artifact = joblib.load(io.BytesIO(artifact_repo.load(ref)))
    return artifact["model"], artifact["feature_cols"]
