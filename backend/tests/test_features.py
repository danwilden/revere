"""Tests for the feature computation pipeline.

Verifies:
  - All required feature columns are produced
  - No look-ahead leakage (future data not used at bar N)
  - NaN rows are only at the head of the series (warm-up only)
  - Feature run is persisted to metadata store and DuckDB
  - load_feature_matrix returns correct wide DataFrame
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.features.compute import (
    compute_features,
    load_feature_matrix,
    run_feature_pipeline,
)
from backend.models.hmm_regime import HMM_FEATURE_COLS as MODEL_FEATURE_COLS
from backend.schemas.enums import Timeframe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_bar_df(n: int = 300, base_price: float = 1.1000, seed: int = 42) -> pd.DataFrame:
    """Synthetic bar DataFrame with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    closes = base_price + np.cumsum(rng.normal(0, 0.0003, n))
    highs = closes + rng.uniform(0.0001, 0.0010, n)
    lows = closes - rng.uniform(0.0001, 0.0010, n)
    opens = closes + rng.normal(0, 0.0002, n)

    index = pd.date_range("2023-01-02 00:00", periods=n, freq="H", tz=None)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000.0},
        index=index,
    )


@pytest.fixture
def bar_df():
    return _make_bar_df(300)


# ---------------------------------------------------------------------------
# Unit tests: compute_features()
# ---------------------------------------------------------------------------

def test_compute_features_returns_dataframe(bar_df):
    feats = compute_features(bar_df)
    assert isinstance(feats, pd.DataFrame)


def test_compute_features_same_length(bar_df):
    feats = compute_features(bar_df)
    assert len(feats) == len(bar_df)


def test_compute_features_has_required_columns(bar_df):
    feats = compute_features(bar_df)
    required = [
        "log_ret_1", "log_ret_5", "log_ret_20",
        "rvol_20", "atr_14", "atr_pct_14",
        "rsi_14", "ema_slope_20", "ema_slope_50",
        "adx_14", "breakout_20", "session",
    ]
    for col in required:
        assert col in feats.columns, f"Missing column: {col}"


def test_compute_features_hmm_cols_all_present(bar_df):
    """All columns needed by the HMM model must be produced."""
    feats = compute_features(bar_df)
    for col in MODEL_FEATURE_COLS:
        assert col in feats.columns, f"HMM column missing: {col}"


def test_compute_features_nan_only_at_head(bar_df):
    """NaN values should only appear in warm-up rows at the head."""
    feats = compute_features(bar_df)
    for col in feats.columns:
        series = feats[col]
        first_valid = series.first_valid_index()
        if first_valid is None:
            continue
        tail = series.loc[first_valid:]
        assert not tail.isna().any(), (
            f"Column '{col}' has NaN after first valid index — possible look-ahead"
        )


def test_log_ret_1_no_lookahead(bar_df):
    """log_ret_1[i] must equal log(close[i] / close[i-1])."""
    feats = compute_features(bar_df)
    close = bar_df["close"]
    expected = np.log(close / close.shift(1))
    pd.testing.assert_series_equal(
        feats["log_ret_1"].dropna(),
        expected.dropna(),
        check_names=False,
        rtol=1e-10,
    )


def test_rsi_bounded(bar_df):
    """RSI must be in [0, 100]."""
    feats = compute_features(bar_df)
    rsi = feats["rsi_14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_atr_positive(bar_df):
    """ATR must be positive."""
    feats = compute_features(bar_df)
    atr = feats["atr_14"].dropna()
    assert (atr > 0).all()


def test_breakout_bounded(bar_df):
    """Breakout indicator must be in [0, 1]."""
    feats = compute_features(bar_df)
    bo = feats["breakout_20"].dropna()
    assert (bo >= 0).all() and (bo <= 1).all()


def test_session_values(bar_df):
    """Session indicator must be one of {0, 1, 2, 3, 4}."""
    feats = compute_features(bar_df)
    session = feats["session"].dropna()
    assert set(session.unique()).issubset({0.0, 1.0, 2.0, 3.0, 4.0})


def test_rvol_positive(bar_df):
    """Realized volatility must be positive (non-negative)."""
    feats = compute_features(bar_df)
    rvol = feats["rvol_20"].dropna()
    assert (rvol >= 0).all()


# ---------------------------------------------------------------------------
# Integration tests: run_feature_pipeline + load_feature_matrix
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_duckdb(tmp_path):
    from backend.data.duckdb_store import DuckDBStore
    db = DuckDBStore(tmp_path / "test.duckdb")
    yield db
    db.close()


@pytest.fixture
def tmp_metadata(tmp_path):
    from backend.data.repositories import LocalMetadataRepository
    return LocalMetadataRepository(tmp_path / "meta")


def _insert_bars_agg(db, instrument_id: str, timeframe: str, bars: pd.DataFrame):
    """Helper: write bar_df rows into bars_agg table."""
    rows = []
    for ts, row in bars.iterrows():
        rows.append({
            "instrument_id": instrument_id,
            "timeframe": timeframe,
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


def test_run_feature_pipeline_returns_run_id(tmp_duckdb, tmp_metadata, bar_df):
    instrument_id = "EUR_USD"
    _insert_bars_agg(tmp_duckdb, instrument_id, "H1", bar_df)

    run_id = run_feature_pipeline(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        market_repo=tmp_duckdb,
        metadata_repo=tmp_metadata,
    )
    assert isinstance(run_id, str)
    assert len(run_id) == 36  # UUID format


def test_feature_run_persisted_to_metadata(tmp_duckdb, tmp_metadata, bar_df):
    instrument_id = "EUR_USD"
    _insert_bars_agg(tmp_duckdb, instrument_id, "H1", bar_df)

    run_id = run_feature_pipeline(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        market_repo=tmp_duckdb,
        metadata_repo=tmp_metadata,
    )

    record = tmp_metadata.get_feature_run(run_id)
    assert record is not None
    assert record["feature_set_name"] == "default_v1"


def test_load_feature_matrix_wide_format(tmp_duckdb, tmp_metadata, bar_df):
    instrument_id = "EUR_USD"
    _insert_bars_agg(tmp_duckdb, instrument_id, "H1", bar_df)

    run_id = run_feature_pipeline(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        market_repo=tmp_duckdb,
        metadata_repo=tmp_metadata,
    )

    matrix = load_feature_matrix(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        feature_run_id=run_id,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        market_repo=tmp_duckdb,
        dropna=True,
    )

    assert isinstance(matrix, pd.DataFrame)
    assert not matrix.empty
    assert "log_ret_1" in matrix.columns
    assert "rsi_14" in matrix.columns
    # After dropna, no NaN should remain
    assert not matrix.isna().any().any()


def test_feature_run_raises_when_no_bars(tmp_duckdb, tmp_metadata):
    with pytest.raises(ValueError, match="No bars found"):
        run_feature_pipeline(
            instrument_id="GBP_USD",
            timeframe=Timeframe.H1,
            start=datetime(2023, 1, 1),
            end=datetime(2023, 1, 31),
            market_repo=tmp_duckdb,
            metadata_repo=tmp_metadata,
        )


# ---------------------------------------------------------------------------
# H6 — Timestamp alignment: bars_agg timestamps must match feature timestamps
#
# This is a regression guard. The backtester merges bars and features on
# timestamp_utc. A precision mismatch (e.g. microsecond truncation) would
# produce an empty DataFrame with no error — a silent correctness failure.
# ---------------------------------------------------------------------------

def test_bars_and_features_timestamps_align(tmp_duckdb, tmp_metadata, bar_df):
    """Every bar timestamp must have a matching feature row and vice versa.

    After warm-up rows are dropped (dropna=True in load_feature_matrix), the
    feature matrix timestamp set must be a subset of the bars_agg timestamp set.
    And every feature row must correspond to an existing bar.
    """
    instrument_id = "EUR_USD"
    _insert_bars_agg(tmp_duckdb, instrument_id, "H1", bar_df)

    run_id = run_feature_pipeline(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        market_repo=tmp_duckdb,
        metadata_repo=tmp_metadata,
    )

    # Load bars from DuckDB
    bars = tmp_duckdb.get_bars_agg(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
    )
    bar_timestamps = {row["timestamp_utc"] for row in bars}

    # Load feature matrix (warm-up dropped)
    matrix = load_feature_matrix(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        feature_run_id=run_id,
        start=bar_df.index[0].to_pydatetime(),
        end=bar_df.index[-1].to_pydatetime() + timedelta(hours=1),
        market_repo=tmp_duckdb,
        dropna=True,
    )
    feature_timestamps = set(matrix.index.to_pydatetime())

    # Every feature row must have a matching bar — no orphaned feature rows
    orphaned = feature_timestamps - bar_timestamps
    assert not orphaned, (
        f"Feature timestamps with no matching bar (first 5): {list(orphaned)[:5]}\n"
        "This indicates a timestamp precision mismatch between bars_agg and features tables."
    )

    # Sanity check: we have a non-trivial number of matching rows
    overlap = feature_timestamps & bar_timestamps
    assert len(overlap) > 50, (
        f"Only {len(overlap)} overlapping timestamps — expected >50 after warm-up"
    )
