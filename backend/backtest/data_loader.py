"""Load an aligned backtest frame from bars, features, and regime labels.

Returns a flat list of bar dicts with feature and regime columns merged in at
the matching timestamp.  The backtester consumes this list directly — one dict
per bar, one iteration per bar.
"""
from __future__ import annotations

from datetime import datetime

from backend.data.repositories import MarketDataRepository, MetadataRepository
from backend.schemas.enums import Timeframe


def _resolve_signal_column_name(signal_record: dict, signal_id: str) -> str:
    """Determine the column name for a signal's primary value.

    Uses metadata["field_name"] when available, otherwise falls back to
    ``signal_{id}_value``.
    """
    metadata = signal_record.get("metadata", {}) or {}
    return metadata.get("field_name", f"signal_{signal_id}_value")


def load_backtest_frame(
    instrument_id: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    feature_run_id: str | None = None,
    model_id: str | None = None,
    metadata_repo: MetadataRepository | None = None,
    signal_ids: list[str] | None = None,
) -> list[dict]:
    """Load bars and optionally join features, regime labels, and signals.

    Parameters
    ----------
    instrument_id:
        Symbol used as the storage key (e.g. "EUR_USD").
    timeframe:
        M1 reads bars_1m; H1 / H4 / D reads bars_agg.
    start, end:
        Half-open range [start, end) — same convention as the repository.
    market_repo:
        MarketDataRepository implementation.
    feature_run_id:
        When provided, feature rows are joined (wide pivot by timestamp).
        Bars with no matching feature row will have those keys absent.
        When None but model_id is set, the loader resolves the feature_run_id
        from the model record's parameters_json (requires metadata_repo).
    model_id:
        When provided, 'regime_label' and 'state_id' columns are joined
        from regime_labels.  Bars with no matching regime label row receive
        regime_label='UNKNOWN' and state_id=-1 so the rules engine never
        encounters a missing field.
    metadata_repo:
        Required when model_id is set and feature_run_id is None, so the
        loader can look up the model record to resolve the feature_run_id.
        Also required when signal_ids is provided, to look up Signal records
        for column name resolution.
    signal_ids:
        When provided, signal feature rows are joined for each signal_id.
        Each signal_id is used as a feature_run_id to query the features
        table.  Column names are resolved from the Signal's metadata.

    Returns
    -------
    list[dict]:
        Bars sorted by timestamp_utc, with feature and regime columns
        merged in.  Empty list if no bars exist in the range.
    """
    # --- 1. Load bars ----------------------------------------------------------
    if timeframe == Timeframe.M1:
        bars = market_repo.get_bars_1m(instrument_id, start, end)
    else:
        bars = market_repo.get_bars_agg(instrument_id, timeframe, start, end)

    if not bars:
        return []

    # Index by timestamp for O(1) merge; dicts preserve all original bar fields.
    frame: dict[datetime, dict] = {b["timestamp_utc"]: dict(b) for b in bars}

    # --- 1b. Resolve feature_run_id from model record if not provided ----------
    if feature_run_id is None and model_id is not None and metadata_repo is not None:
        model_record = metadata_repo.get_model(model_id)
        if model_record is not None:
            params = model_record.get("parameters_json", {})
            # parameters_json may be a JSON string (from metadata store)
            if isinstance(params, str):
                import json
                try:
                    params = json.loads(params)
                except (json.JSONDecodeError, TypeError):
                    params = {}
            feature_run_id = params.get("feature_run_id")

    # --- 2. Merge features (wide pivot) ----------------------------------------
    if feature_run_id:
        feature_rows = market_repo.get_features(
            instrument_id, timeframe, feature_run_id, start, end
        )
        for row in feature_rows:
            ts = row["timestamp_utc"]
            if ts in frame:
                frame[ts][row["feature_name"]] = row["feature_value"]

    # --- 3. Merge regime labels -------------------------------------------------
    if model_id:
        regime_rows = market_repo.get_regime_labels(
            model_id, instrument_id, timeframe, start, end
        )
        for row in regime_rows:
            ts = row["timestamp_utc"]
            if ts in frame:
                frame[ts]["regime_label"] = row["regime_label"]
                frame[ts]["state_id"] = row["state_id"]

        # Ensure every bar has regime_label/state_id so the rules engine
        # never fails with a missing-field error.  Bars outside the model's
        # labelled range get safe defaults.
        for bar in frame.values():
            bar.setdefault("regime_label", "UNKNOWN")
            bar.setdefault("state_id", -1)

    # --- 4. Merge signal columns -----------------------------------------------
    if signal_ids and metadata_repo is not None:
        for signal_id in signal_ids:
            # Look up the Signal record for column name resolution
            signal_record = metadata_repo.get_signal(signal_id)
            if signal_record is None:
                # Unknown signal — skip silently
                continue

            col_name = _resolve_signal_column_name(signal_record, signal_id)

            # Query features table using signal_id as the feature_run_id
            signal_rows = market_repo.get_features(
                instrument_id, timeframe, signal_id, start, end
            )

            # Build a timestamp -> value lookup from signal rows
            sig_by_ts: dict[datetime, float | None] = {}
            for row in signal_rows:
                ts = row["timestamp_utc"]
                val = row["feature_value"]
                # Convert NaN to None for JSON safety
                if val is not None:
                    try:
                        import math
                        if math.isnan(val):
                            val = None
                    except (TypeError, ValueError):
                        pass
                sig_by_ts[ts] = val

            # Left-join: every bar gets the signal column; None if missing
            for ts, bar in frame.items():
                bar[col_name] = sig_by_ts.get(ts, None)

    return sorted(frame.values(), key=lambda x: x["timestamp_utc"])
