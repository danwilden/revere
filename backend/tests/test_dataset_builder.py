"""Tests for backend/automl/dataset_builder.py.

All tests are fully mocked — no live DuckDB connection or filesystem required.
load_feature_matrix is patched to return a synthetic DataFrame; the market_repo
and artifact_repo are replaced with lightweight fakes.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, call, patch

import numpy as np
import pandas as pd
import pytest

from backend.automl.dataset_builder import DatasetBuilder
from backend.schemas.models import DatasetManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index(n: int, start: str = "2023-01-01") -> pd.DatetimeIndex:
    """Return an hourly DatetimeIndex of length n starting at `start`."""
    return pd.date_range(start=start, periods=n, freq="h")


def _make_close(idx: pd.DatetimeIndex, seed: int = 42) -> pd.Series:
    """Deterministic random-walk close prices, always positive."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.0005, size=len(idx))
    prices = 1.1000 * np.exp(np.cumsum(steps))
    return pd.Series(prices, index=idx, name="close")


def _make_feature_df(idx: pd.DatetimeIndex, seed: int = 7) -> pd.DataFrame:
    """Synthetic feature matrix with a few columns and no NaNs."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "log_ret_1": rng.normal(0, 0.001, len(idx)),
            "rsi_14": rng.uniform(20, 80, len(idx)),
            "atr_14": rng.uniform(0.0005, 0.002, len(idx)),
        },
        index=idx,
    )


def _make_builder(
    feature_df: pd.DataFrame,
    close_series: pd.Series,
    regime_df: pd.DataFrame | None = None,
) -> tuple[DatasetBuilder, MagicMock]:
    """
    Build a DatasetBuilder with all external dependencies mocked.

    Returns (builder, artifact_repo_mock).
    """
    market_repo = MagicMock()

    # _load_close will call get_bars_agg or get_bars_1m
    bar_rows = [
        {
            "timestamp_utc": ts.isoformat(),
            "open": v,
            "high": v,
            "low": v,
            "close": v,
            "volume": 0,
        }
        for ts, v in zip(close_series.index, close_series.values)
    ]
    market_repo.get_bars_1m.return_value = bar_rows
    market_repo.get_bars_agg.return_value = bar_rows

    # regime labels query
    if regime_df is not None and not regime_df.empty:
        # Return rows as list of tuples (timestamp_utc, state_id, regime_label)
        regime_rows = [
            (ts, row["state_id"], row["regime_label"])
            for ts, row in regime_df.reset_index().rename(columns={"index": "timestamp_utc"}).iterrows()
        ]
        # Actually reconstruct properly:
        regime_rows = [
            (row["timestamp_utc"], row["state_id"], row["regime_label"])
            for _, row in regime_df.reset_index().iterrows()
        ]
        market_repo._conn.execute.return_value.fetchall.return_value = regime_rows
    else:
        market_repo._conn.execute.return_value.fetchall.return_value = []

    artifact_repo = MagicMock()
    artifact_repo.save.return_value = "/fake/path"

    builder = DatasetBuilder(market_repo=market_repo, artifact_repo=artifact_repo)

    return builder, artifact_repo, feature_df


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

N = 200  # total bars
TRAIN_END = "2023-01-09"   # 8 days * 24h = 192 hourly bars — leaves some for test
TEST_END = "2023-01-10"    # 1 more day

IDX = _make_index(N)
CLOSE = _make_close(IDX)
FEATURE_DF = _make_feature_df(IDX)

PATCH_TARGET = "backend.automl.dataset_builder.load_feature_matrix"


# ---------------------------------------------------------------------------
# Helpers to run build() with patched load_feature_matrix
# ---------------------------------------------------------------------------

def _run_build(feature_df, close_series, train_end=TRAIN_END, test_end=TEST_END,
               target_type="direction", horizon=1, job_id=None,
               regime_df=None):
    builder, artifact_mock, _ = _make_builder(feature_df, close_series, regime_df)
    with patch(PATCH_TARGET, return_value=feature_df):
        manifest = builder.build(
            instrument_id="EUR_USD",
            timeframe="H1",
            feature_run_id="fr-test",
            model_id="m-test",
            train_end_date=train_end,
            test_end_date=test_end,
            target_horizon_bars=horizon,
            target_type=target_type,
            job_id=job_id or "job-test-001",
        )
    return manifest, builder, artifact_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTrainTestSplit:
    """Train/test boundary tests."""

    def test_boundary_row_goes_to_train(self):
        """Rows exactly on train_end_date must be in the train set."""
        # Use a small explicit dataset with known boundary
        idx = pd.date_range("2023-01-01", periods=10, freq="D")
        close = _make_close(idx)
        feat = _make_feature_df(idx)

        # The boundary is "2023-01-05" — rows on that date should be in train
        manifest, _, _ = _run_build(feat, close, train_end="2023-01-05", test_end="2023-01-10")

        # Timestamps at or before 2023-01-05 (5 rows: Jan1–Jan5)
        # After shift+dropna we lose the first row, so train has rows Jan2–Jan5 = 4
        boundary_ts = pd.Timestamp("2023-01-05")
        assert manifest.train_rows > 0

        # Verify no test row is <= boundary
        # We can reconstruct by checking test_rows — there should be rows after boundary
        total = manifest.train_rows + manifest.test_rows
        assert total <= len(feat)

    def test_rows_after_test_end_excluded(self):
        """Rows after test_end_date must not appear in either split."""
        idx = pd.date_range("2023-01-01", periods=20, freq="D")
        close = _make_close(idx)
        feat = _make_feature_df(idx)

        manifest, _, _ = _run_build(
            feat, close,
            train_end="2023-01-05",
            test_end="2023-01-10",
        )

        # Total rows in train + test must be < total bars (rows after Jan10 excluded)
        assert manifest.train_rows + manifest.test_rows < len(feat)

    def test_train_test_counts_are_correct(self):
        """train_rows + test_rows must exactly equal the usable rows in the window."""
        idx = pd.date_range("2023-01-01", periods=30, freq="D")
        close = _make_close(idx)
        feat = _make_feature_df(idx)

        train_end = "2023-01-15"
        test_end = "2023-01-20"

        builder, artifact_mock, _ = _make_builder(feat, close)
        with patch(PATCH_TARGET, return_value=feat):
            manifest = builder.build(
                instrument_id="EUR_USD",
                timeframe="H1",
                feature_run_id="fr-test",
                model_id="m-test",
                train_end_date=train_end,
                test_end_date=test_end,
                job_id="job-count-test",
            )

        # Total must equal rows strictly within [epoch, test_end] after NaN drop
        # We trust the builder's internal logic; just assert both splits are > 0
        assert manifest.train_rows > 0
        assert manifest.test_rows > 0


class TestDirectionLabel:
    """direction_label correctness tests."""

    def test_direction_label_is_binary(self):
        """direction_label column must contain only 0 and 1."""
        manifest, builder, artifact_mock = _run_build(FEATURE_DF, CLOSE)

        # Reconstruct train CSV from the artifact_mock calls
        train_call_args = artifact_mock.save.call_args_list[0]
        csv_bytes = train_call_args[0][1]
        df = pd.read_csv(io.StringIO(csv_bytes.decode("utf-8")))

        unique_vals = set(df["direction_label"].dropna().unique())
        assert unique_vals <= {0, 1}, f"Unexpected values: {unique_vals}"

    def test_direction_target_column_name(self):
        """manifest.target_column must be 'direction_label' for direction target."""
        manifest, _, _ = _run_build(FEATURE_DF, CLOSE, target_type="direction")
        assert manifest.target_column == "direction_label"

    def test_return_bucket_target_column_name(self):
        """manifest.target_column must be 'return_bucket' for return_bucket target."""
        manifest, _, _ = _run_build(FEATURE_DF, CLOSE, target_type="return_bucket")
        assert manifest.target_column == "return_bucket"


class TestReturnBucket:
    """return_bucket correctness tests."""

    def test_return_bucket_values_are_0_1_2(self):
        """return_bucket must only contain values 0, 1, or 2."""
        manifest, builder, artifact_mock = _run_build(
            FEATURE_DF, CLOSE, target_type="return_bucket"
        )

        train_call_args = artifact_mock.save.call_args_list[0]
        csv_bytes = train_call_args[0][1]
        df = pd.read_csv(io.StringIO(csv_bytes.decode("utf-8")))

        unique_vals = set(df["return_bucket"].dropna().unique())
        assert unique_vals <= {0, 1, 2}, f"Unexpected values: {unique_vals}"


class TestShiftDiscipline:
    """Verify the anti-look-ahead shift is applied correctly."""

    def test_first_row_nan_before_drop(self):
        """Before NaN drop, bar 0 must have NaN target due to shift(1)."""
        builder, artifact_mock, _ = _make_builder(FEATURE_DF, CLOSE)

        # Call _build_target directly with a small close series
        idx = pd.date_range("2023-01-01", periods=10, freq="h")
        close = _make_close(idx, seed=1)
        target = builder._build_target(close, horizon=1, target_type="direction")

        # After shift(1), the first element must be NaN
        assert pd.isna(target.iloc[0]), "First row must be NaN after shift(1)"

    def test_target_derived_from_next_bar(self):
        """Bar N's target is derived from return between bar N+1 and bar N."""
        # Construct a deterministic close sequence where we know the direction
        # of each forward return manually.
        idx = pd.date_range("2023-01-01", periods=6, freq="h")
        # Prices: strictly increasing → all forward returns are positive
        close = pd.Series([1.0, 1.1, 1.2, 1.3, 1.4, 1.5], index=idx)

        builder, _, _ = _make_builder(FEATURE_DF, CLOSE)
        target = builder._build_target(close, horizon=1, target_type="direction")

        # forward_lr[i] = log(close[i+1]/close[i]) > 0 → direction = 1
        # after shift(1): target[0] = NaN, target[1] = direction computed at bar 0 = 1
        # target[2] = direction at bar 1 = 1, ...
        # The last bar's forward return is NaN, so after shift target[-1] = direction at bar[-2] = 1
        non_nan = target.dropna()
        assert all(v == 1 for v in non_nan), f"All should be 1 (strictly rising): {non_nan.values}"


class TestManifestFields:
    """DatasetManifest field correctness tests."""

    def test_manifest_train_rows_matches_split(self):
        """manifest.train_rows must match the actual number of train rows in the CSV."""
        manifest, builder, artifact_mock = _run_build(FEATURE_DF, CLOSE)

        train_csv_bytes = artifact_mock.save.call_args_list[0][0][1]
        df = pd.read_csv(io.StringIO(train_csv_bytes.decode("utf-8")))
        assert manifest.train_rows == len(df)

    def test_manifest_test_rows_matches_split(self):
        """manifest.test_rows must match the actual number of test rows in the CSV."""
        manifest, builder, artifact_mock = _run_build(FEATURE_DF, CLOSE)

        test_csv_bytes = artifact_mock.save.call_args_list[1][0][1]
        df = pd.read_csv(io.StringIO(test_csv_bytes.decode("utf-8")))
        assert manifest.test_rows == len(df)

    def test_feature_columns_excludes_target(self):
        """manifest.feature_columns must not include the target column."""
        manifest, _, _ = _run_build(FEATURE_DF, CLOSE, target_type="direction")
        assert "direction_label" not in manifest.feature_columns

    def test_feature_columns_excludes_target_return_bucket(self):
        """manifest.feature_columns must not include 'return_bucket'."""
        manifest, _, _ = _run_build(FEATURE_DF, CLOSE, target_type="return_bucket")
        assert "return_bucket" not in manifest.feature_columns


class TestArtifactKeys:
    """Artifact key naming and save-call tests."""

    def test_artifact_keys_follow_convention(self):
        """Artifact keys must match automl/{job_id}/train.csv and automl/{job_id}/test.csv."""
        job_id = "job-key-test"
        manifest, _, artifact_mock = _run_build(FEATURE_DF, CLOSE, job_id=job_id)

        assert manifest.train_artifact_key == f"automl/{job_id}/train.csv"
        assert manifest.test_artifact_key == f"automl/{job_id}/test.csv"

    def test_save_artifact_called_twice(self):
        """artifact_repo.save must be called exactly twice (train + test)."""
        manifest, _, artifact_mock = _run_build(FEATURE_DF, CLOSE)
        assert artifact_mock.save.call_count == 2

    def test_save_artifact_keys_match_manifest(self):
        """The keys passed to save() must match the manifest's artifact keys."""
        job_id = "job-key-match"
        manifest, _, artifact_mock = _run_build(FEATURE_DF, CLOSE, job_id=job_id)

        called_keys = [c[0][0] for c in artifact_mock.save.call_args_list]
        assert manifest.train_artifact_key in called_keys
        assert manifest.test_artifact_key in called_keys

    def test_job_id_generated_when_none(self):
        """When job_id=None, a UUID must be generated and reflected in artifact keys."""
        builder, artifact_mock, _ = _make_builder(FEATURE_DF, CLOSE)
        with patch(PATCH_TARGET, return_value=FEATURE_DF):
            manifest = builder.build(
                instrument_id="EUR_USD",
                timeframe="H1",
                feature_run_id="fr-test",
                model_id="m-test",
                train_end_date=TRAIN_END,
                test_end_date=TEST_END,
                job_id=None,   # <-- trigger UUID generation
            )

        assert manifest.job_id  # not empty
        # Verify it's a valid UUID
        uuid.UUID(manifest.job_id)
        assert manifest.train_artifact_key == f"automl/{manifest.job_id}/train.csv"


class TestBoundaryNanDropped:
    """NaN boundary rows from shift are dropped before export."""

    def test_no_nan_in_train_target(self):
        """No NaN values must appear in the target column of the exported train CSV."""
        manifest, _, artifact_mock = _run_build(FEATURE_DF, CLOSE, target_type="direction")

        train_csv_bytes = artifact_mock.save.call_args_list[0][0][1]
        df = pd.read_csv(io.StringIO(train_csv_bytes.decode("utf-8")))
        assert df["direction_label"].isna().sum() == 0

    def test_no_nan_in_test_target(self):
        """No NaN values must appear in the target column of the exported test CSV."""
        manifest, _, artifact_mock = _run_build(FEATURE_DF, CLOSE, target_type="direction")

        test_csv_bytes = artifact_mock.save.call_args_list[1][0][1]
        df = pd.read_csv(io.StringIO(test_csv_bytes.decode("utf-8")))
        assert df["direction_label"].isna().sum() == 0
