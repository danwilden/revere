"""Signal materialization — produce signal values over a date range.

For HMM-derived signals:
  1. Load the signal definition (model_id, feature_run_id)
  2. Load the trained model artifact
  3. Run out-of-sample inference on [start, end)
  4. Store results in regime_labels table
  5. Return signal rows: [{timestamp_utc, signal_value, signal_value_str}]

signal_value: state_id (int cast to float) for numeric downstream use
signal_value_str: regime label string for human display and strategy rules

Additional signal types (Phase 5E):
  - AUTOML_DIRECTION_PROB / AUTOML_RETURN_BUCKET: AutoML model inference
  - HMM_STATE_PROB: per-state probability from HMM model
  - RISK_FILTER: rules DSL evaluation producing 0 (allow) or 1 (block)
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import joblib
import numpy as np
import pandas as pd

from backend.data.repositories import ArtifactRepository, MarketDataRepository, MetadataRepository
from backend.features.compute import load_feature_matrix
from backend.models.hmm_regime import infer_hmm
from backend.schemas.enums import SignalType, Timeframe
from backend.strategies.rules_engine import evaluate


def materialize_signal(
    signal_id: str,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> list[dict]:
    """Produce signal values for a given signal bank entry and date range.

    Returns:
        List of dicts whose keys depend on the signal type.
    """
    return _materialize_signal_sync(
        signal_id=signal_id,
        instrument=instrument,
        timeframe=timeframe,
        start=start,
        end=end,
        market_repo=market_repo,
        metadata_repo=metadata_repo,
        artifact_repo=artifact_repo,
    )


def _materialize_signal_sync(
    signal_id: str,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> list[dict]:
    """Core materialization logic. Identical to materialize_signal().

    Exposed as a separate function so background jobs (Team 3) can call it
    directly without going through the public wrapper.
    """
    signal_record = metadata_repo.get_signal(signal_id)
    if not signal_record:
        raise ValueError(f"Signal {signal_id} not found")

    signal_type = signal_record.get("signal_type")

    if signal_type == SignalType.HMM_REGIME.value or signal_type == SignalType.HMM_REGIME:
        return _materialize_hmm_signal(
            signal_record, instrument, timeframe, start, end,
            market_repo, metadata_repo, artifact_repo,
        )

    if signal_type in (
        SignalType.AUTOML_DIRECTION_PROB.value, SignalType.AUTOML_DIRECTION_PROB,
        SignalType.AUTOML_RETURN_BUCKET.value, SignalType.AUTOML_RETURN_BUCKET,
    ):
        return _materialize_automl_signal(
            signal_id, signal_record, instrument, timeframe, start, end,
            market_repo, metadata_repo, artifact_repo,
        )

    if signal_type in (SignalType.HMM_STATE_PROB.value, SignalType.HMM_STATE_PROB):
        return _materialize_hmm_state_prob(
            signal_id, signal_record, instrument, timeframe, start, end,
            market_repo, metadata_repo, artifact_repo,
        )

    if signal_type in (SignalType.RISK_FILTER.value, SignalType.RISK_FILTER):
        return _materialize_risk_filter(
            signal_id, signal_record, instrument, timeframe, start, end,
            market_repo, metadata_repo,
        )

    raise NotImplementedError(f"Materialization for signal type '{signal_type}' not yet implemented")


# ---------------------------------------------------------------------------
# HMM_REGIME (original path)
# ---------------------------------------------------------------------------

def _materialize_hmm_signal(
    signal_record: dict,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> list[dict]:
    defn = signal_record.get("definition_json", {})
    model_id = defn.get("model_id")
    feature_run_id = defn.get("feature_run_id")

    if not model_id or not feature_run_id:
        raise ValueError("HMM signal definition missing model_id or feature_run_id")

    model_record = metadata_repo.get_model(model_id)
    if not model_record:
        raise ValueError(f"Model {model_id} not found")

    label_map: dict[str, str] = model_record.get("label_map_json", {})

    n = infer_hmm(
        model_id=model_id,
        instrument_id=instrument,
        timeframe=timeframe,
        infer_start=start,
        infer_end=end,
        feature_run_id=feature_run_id,
        label_map=label_map,
        market_repo=market_repo,
        metadata_repo=metadata_repo,
        artifact_repo=artifact_repo,
    )

    # Fetch the newly written regime labels and return them
    rows = market_repo.get_regime_labels(model_id, instrument, timeframe, start, end)
    return rows


# ---------------------------------------------------------------------------
# AUTOML_DIRECTION_PROB / AUTOML_RETURN_BUCKET
# ---------------------------------------------------------------------------

def _run_automl_inference(artifact: dict, feature_df: pd.DataFrame) -> pd.Series:
    """Run inference using an AutoML artifact.

    If the artifact has a "mock_output" key, return that repeated/truncated to
    match the feature_df length. Otherwise raise NotImplementedError.

    Args:
        artifact: loaded model artifact dict.
        feature_df: wide feature DataFrame aligned to bar timestamps.

    Returns:
        pd.Series of predictions aligned to feature_df index.
    """
    if "mock_output" in artifact:
        mock = artifact["mock_output"]
        n = len(feature_df)
        if isinstance(mock, list):
            # Repeat or truncate to match length
            repeated = (mock * ((n // len(mock)) + 1))[:n]
        else:
            repeated = [mock] * n
        return pd.Series(repeated, index=feature_df.index)

    raise NotImplementedError("Real SageMaker inference not yet implemented")


def _materialize_automl_signal(
    signal_id: str,
    signal_record: dict,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> list[dict]:
    """Materialize an AUTOML_DIRECTION_PROB or AUTOML_RETURN_BUCKET signal."""
    defn = signal_record.get("definition_json", {})
    automl_job_id = defn.get("automl_job_id")
    if not automl_job_id:
        raise ValueError("AutoML signal definition missing automl_job_id")

    feature_run_id = defn.get("feature_run_id")
    if not feature_run_id:
        raise ValueError("AutoML signal definition missing feature_run_id")

    # Load best model artifact
    artifact_key = defn.get("best_model_artifact_key")
    if not artifact_key:
        raise ValueError("AutoML signal definition missing best_model_artifact_key")

    artifact_bytes = artifact_repo.load(artifact_key)
    artifact = joblib.load(io.BytesIO(artifact_bytes))

    # Load feature matrix
    feature_df = load_feature_matrix(
        instrument_id=instrument,
        timeframe=timeframe,
        feature_run_id=feature_run_id,
        start=start,
        end=end,
        market_repo=market_repo,
        dropna=True,
    )

    if feature_df.empty:
        return []

    predictions = _run_automl_inference(artifact, feature_df)

    # Determine value type based on signal type
    signal_type = signal_record.get("signal_type")
    is_bucket = signal_type in (SignalType.AUTOML_RETURN_BUCKET.value, SignalType.AUTOML_RETURN_BUCKET)

    # Write to DuckDB features table
    feature_name = f"signal_{signal_id}_value"
    feature_rows: list[dict] = []
    result_rows: list[dict] = []

    for ts, val in predictions.items():
        # Convert to safe Python type (no NaN)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            safe_val = None
        elif is_bucket:
            safe_val = float(int(val))
        else:
            safe_val = float(val)

        feature_rows.append({
            "instrument_id": instrument,
            "timeframe": timeframe.value,
            "timestamp_utc": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "feature_run_id": signal_id,
            "feature_name": feature_name,
            "feature_value": safe_val,
        })

        result_rows.append({
            "timestamp_utc": ts,
            "signal_value": safe_val,
            "signal_id": signal_id,
        })

    # Batch upsert
    BATCH = 2000
    for i in range(0, len(feature_rows), BATCH):
        market_repo.upsert_features(feature_rows[i:i + BATCH])

    return result_rows


# ---------------------------------------------------------------------------
# HMM_STATE_PROB
# ---------------------------------------------------------------------------

def _materialize_hmm_state_prob(
    signal_id: str,
    signal_record: dict,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    artifact_repo: ArtifactRepository,
) -> list[dict]:
    """Materialize an HMM_STATE_PROB signal: per-state probabilities."""
    defn = signal_record.get("definition_json", {})
    model_id = defn.get("model_id")
    feature_run_id = defn.get("feature_run_id")

    if not model_id or not feature_run_id:
        raise ValueError("HMM_STATE_PROB signal definition missing model_id or feature_run_id")

    # Load HMM artifact
    artifact_key = f"models/hmm/{model_id}.joblib"
    if not artifact_repo.exists(artifact_key):
        raise ValueError(f"HMM artifact not found: {artifact_key}")
    artifact = joblib.load(io.BytesIO(artifact_repo.load(artifact_key)))
    hmm_model = artifact["model"]
    feature_cols = artifact["feature_cols"]

    # Load feature matrix
    feature_df = load_feature_matrix(
        instrument_id=instrument,
        timeframe=timeframe,
        feature_run_id=feature_run_id,
        start=start,
        end=end,
        market_repo=market_repo,
        dropna=True,
    )

    if feature_df.empty:
        return []

    # Select HMM feature columns only
    missing = [c for c in feature_cols if c not in feature_df.columns]
    if missing:
        raise ValueError(f"Feature matrix missing HMM columns: {missing}")

    X = feature_df[feature_cols].values
    state_probs = hmm_model.predict_proba(X)  # (n_bars, n_states)
    n_states = state_probs.shape[1]

    # Write one column per state to DuckDB features table
    feature_rows: list[dict] = []
    result_rows: list[dict] = []

    for bar_idx, ts in enumerate(feature_df.index):
        probs = state_probs[bar_idx]
        prob_list: list[float] = []

        for state_idx in range(n_states):
            val = float(probs[state_idx])
            if np.isnan(val):
                val = None  # type: ignore[assignment]

            feature_rows.append({
                "instrument_id": instrument,
                "timeframe": timeframe.value,
                "timestamp_utc": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "feature_run_id": signal_id,
                "feature_name": f"signal_{signal_id}_state_{state_idx}_prob",
                "feature_value": val,
            })
            prob_list.append(val if val is not None else 0.0)

        result_rows.append({
            "timestamp_utc": ts,
            "state_probs": prob_list,
            "signal_id": signal_id,
        })

    # Batch upsert
    BATCH = 2000
    for i in range(0, len(feature_rows), BATCH):
        market_repo.upsert_features(feature_rows[i:i + BATCH])

    return result_rows


# ---------------------------------------------------------------------------
# RISK_FILTER
# ---------------------------------------------------------------------------

def _materialize_risk_filter(
    signal_id: str,
    signal_record: dict,
    instrument: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
) -> list[dict]:
    """Materialize a RISK_FILTER signal using rules DSL evaluation.

    Output: 0 if rules pass (trade allowed), 1 if rules block (do not trade).
    """
    defn = signal_record.get("definition_json", {})
    rules_node = defn.get("rules")
    if rules_node is None:
        raise ValueError("RISK_FILTER signal definition missing 'rules'")

    feature_run_id = defn.get("feature_run_id")
    if not feature_run_id:
        raise ValueError("RISK_FILTER signal definition missing feature_run_id")

    # Load feature matrix
    feature_df = load_feature_matrix(
        instrument_id=instrument,
        timeframe=timeframe,
        feature_run_id=feature_run_id,
        start=start,
        end=end,
        market_repo=market_repo,
        dropna=True,
    )

    if feature_df.empty:
        return []

    feature_name = f"signal_{signal_id}_value"
    feature_rows: list[dict] = []
    result_rows: list[dict] = []

    for ts, row in feature_df.iterrows():
        context = row.to_dict()
        # Handle NaN in context — replace with None
        for k, v in context.items():
            if isinstance(v, float) and np.isnan(v):
                context[k] = None

        try:
            blocked = evaluate(rules_node, context)
        except (ValueError, KeyError, TypeError):
            # If evaluation fails (e.g. missing field), default to blocking
            blocked = True

        signal_value = 1 if blocked else 0

        feature_rows.append({
            "instrument_id": instrument,
            "timeframe": timeframe.value,
            "timestamp_utc": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "feature_run_id": signal_id,
            "feature_name": feature_name,
            "feature_value": float(signal_value),
        })

        result_rows.append({
            "timestamp_utc": ts,
            "signal_value": signal_value,
            "signal_id": signal_id,
        })

    # Batch upsert
    BATCH = 2000
    for i in range(0, len(feature_rows), BATCH):
        market_repo.upsert_features(feature_rows[i:i + BATCH])

    return result_rows
