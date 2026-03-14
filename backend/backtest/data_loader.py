"""Load an aligned backtest frame from bars, features, and regime labels.

Returns a flat list of bar dicts with feature and regime columns merged in at
the matching timestamp.  The backtester consumes this list directly — one dict
per bar, one iteration per bar.
"""
from __future__ import annotations

from datetime import datetime

from backend.data.repositories import MarketDataRepository
from backend.schemas.enums import Timeframe


def load_backtest_frame(
    instrument_id: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    feature_run_id: str | None = None,
    model_id: str | None = None,
) -> list[dict]:
    """Load bars and optionally join features and regime labels.

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
    model_id:
        When provided, 'regime_label' and 'state_id' columns are joined
        from regime_labels.  Missing bars are skipped silently.

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

    return sorted(frame.values(), key=lambda x: x["timestamp_utc"])
