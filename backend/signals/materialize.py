"""Signal materialization — produce signal values over a date range.

For HMM-derived signals:
  1. Load the signal definition (model_id, feature_run_id)
  2. Load the trained model artifact
  3. Run out-of-sample inference on [start, end)
  4. Store results in regime_labels table
  5. Return signal rows: [{timestamp_utc, signal_value, signal_value_str}]

signal_value: state_id (int cast to float) for numeric downstream use
signal_value_str: regime label string for human display and strategy rules
"""
from __future__ import annotations

from datetime import datetime

from backend.data.repositories import ArtifactRepository, MarketDataRepository, MetadataRepository
from backend.models.hmm_regime import infer_hmm
from backend.schemas.enums import Timeframe


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
        List of dicts with keys:
          timestamp_utc, state_id, regime_label, state_probabilities_json
    """
    signal_record = metadata_repo.get_signal(signal_id)
    if not signal_record:
        raise ValueError(f"Signal {signal_id} not found")

    signal_type = signal_record.get("signal_type")

    if signal_type == "hmm_regime":
        return _materialize_hmm_signal(
            signal_record, instrument, timeframe, start, end,
            market_repo, metadata_repo, artifact_repo,
        )

    raise NotImplementedError(f"Materialization for signal type '{signal_type}' not yet implemented")


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
