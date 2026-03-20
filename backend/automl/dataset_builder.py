"""AutoML dataset packaging layer.

Responsibilities:
- Load feature matrix from DuckDB via load_feature_matrix()
- Join regime labels for the given model_id
- Construct forward-return targets WITHOUT look-ahead (shift discipline enforced)
- Hard date split into train and test sets
- Export train/test CSVs to the artifact store
- Return a DatasetManifest describing the produced dataset

No look-ahead guarantee
-----------------------
Target at bar N is derived from the log return between bar N+1 and bar N+horizon.
We compute log(close.shift(-horizon) / close) to get the "forward" return, assign
a label, then shift the entire column forward by 1 bar so that bar N receives
the label that was computed at bar N-1. This means bar N never has access to
close prices beyond bar N (bar N itself is available, bar N+1 is not).
"""
from __future__ import annotations

import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from backend.features.compute import load_feature_matrix
from backend.schemas.enums import Timeframe
from backend.schemas.models import DatasetManifest

# Columns that are metadata/label — never treated as model features
_NON_FEATURE_COLS = frozenset({
    "timestamp_utc",
    "instrument_id",
    "timeframe",
    "state_id",
    "regime_label",
    "model_id",
})


class DatasetBuilder:
    """Package a feature matrix + regime labels into a supervised ML dataset.

    Parameters
    ----------
    market_repo:
        DuckDB store — used both for the feature matrix (via load_feature_matrix)
        and for a direct regime_labels query.
    artifact_repo:
        LocalArtifactRepository (or any ArtifactRepository) — CSVs are written
        here via save(key, data_bytes).
    """

    def __init__(self, market_repo, artifact_repo) -> None:
        self._market_repo = market_repo
        self._artifact_repo = artifact_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        instrument_id: str,
        timeframe: str,
        feature_run_id: str,
        model_id: str,
        train_end_date: str,
        test_end_date: str,
        target_horizon_bars: int = 1,
        target_type: str = "direction",
        s3_prefix: str = "automl-datasets",
        job_id: str | None = None,
    ) -> DatasetManifest:
        """Build train/test CSVs and return a DatasetManifest.

        Parameters
        ----------
        instrument_id:
            e.g. "EUR_USD"
        timeframe:
            e.g. "H1" — must be a valid Timeframe enum value
        feature_run_id:
            ID of the feature pipeline run to load
        model_id:
            HMM model whose regime labels should be joined in
        train_end_date:
            Hard boundary (inclusive). Rows on or before this date → train.
            ISO date string "YYYY-MM-DD".
        test_end_date:
            Rows strictly after train_end_date and on or before this date → test.
            ISO date string "YYYY-MM-DD".
        target_horizon_bars:
            How many bars ahead to look for the forward return. Default 1.
        target_type:
            "direction"      → binary 0/1 label (sign of forward log return)
            "return_bucket"  → quantile bucket 0/1/2
        s3_prefix:
            Unused locally but recorded in manifest for cloud context.
        job_id:
            If None, a UUID is generated.
        """
        if job_id is None:
            job_id = str(uuid.uuid4())

        tf_enum = Timeframe(timeframe)

        # 1. Load feature matrix (wide DataFrame, DatetimeIndex = timestamp_utc)
        # Use a very wide time window to capture everything in the feature run.
        # The date split is applied later so we request the full range here.
        _epoch = datetime(1970, 1, 1)
        _far_future = datetime(2100, 1, 1)
        feature_df = load_feature_matrix(
            instrument_id=instrument_id,
            timeframe=tf_enum,
            feature_run_id=feature_run_id,
            start=_epoch,
            end=_far_future,
            market_repo=self._market_repo,
            dropna=True,
        )

        if feature_df.empty:
            raise ValueError(
                f"No features found for feature_run_id={feature_run_id!r}, "
                f"instrument={instrument_id!r}, timeframe={timeframe!r}"
            )

        # 2. Join regime labels
        # Direct DuckDB query — no start/end filtering needed since we already
        # have the feature index and will inner-join on timestamp.
        regime_df = self._load_regime_labels(instrument_id, timeframe, model_id)

        if not regime_df.empty:
            feature_df = feature_df.join(
                regime_df[["state_id", "regime_label"]],
                how="left",
            )

        # 3. We need the close column for target construction.
        # load_feature_matrix returns pure features (no OHLCV), so we need to
        # fetch the close prices separately for the forward-return calculation.
        close_series = self._load_close(instrument_id, tf_enum)

        if close_series.empty:
            raise ValueError(
                f"No bar data found for {instrument_id!r} {timeframe!r} — "
                "cannot construct forward-return targets."
            )

        # Align close to feature index
        close_aligned = close_series.reindex(feature_df.index)

        # 4. Construct target column (no look-ahead — see module docstring)
        target_col = self._build_target(
            close_aligned, target_horizon_bars, target_type
        )
        feature_df = feature_df.copy()
        feature_df[target_col.name] = target_col

        # Drop boundary NaN rows introduced by the shift
        feature_df = feature_df.dropna(subset=[target_col.name]).copy()

        # Cast target to int (qcut returns a Categorical when labels provided)
        feature_df[target_col.name] = feature_df[target_col.name].astype(int)

        # 5. Hard date split
        train_boundary = pd.Timestamp(train_end_date)
        test_boundary = pd.Timestamp(test_end_date)

        # Ensure the index is timezone-naive for comparison (feature matrix uses
        # UTC-naive timestamps throughout this codebase)
        idx = feature_df.index
        if hasattr(idx, "tz") and idx.tz is not None:
            idx = idx.tz_localize(None)
            feature_df.index = idx

        train_df = feature_df[idx <= train_boundary].copy()
        test_df = feature_df[(idx > train_boundary) & (idx <= test_boundary)].copy()

        # 6. Determine feature columns (exclude target and metadata cols)
        target_name = target_col.name
        feature_columns = [
            c for c in feature_df.columns
            if c not in _NON_FEATURE_COLS and c != target_name
        ]

        # 7. Export CSVs
        train_key = f"automl/{job_id}/train.csv"
        test_key = f"automl/{job_id}/test.csv"

        self._artifact_repo.save(
            train_key,
            train_df.reset_index().to_csv(index=False).encode("utf-8"),
        )
        self._artifact_repo.save(
            test_key,
            test_df.reset_index().to_csv(index=False).encode("utf-8"),
        )

        # 8. Build and return manifest
        return DatasetManifest(
            job_id=job_id,
            instrument_id=instrument_id,
            timeframe=timeframe,
            feature_run_id=feature_run_id,
            model_id=model_id,
            train_rows=len(train_df),
            test_rows=len(test_df),
            feature_columns=feature_columns,
            target_column=target_name,
            target_type=target_type,
            train_artifact_key=train_key,
            test_artifact_key=test_key,
            train_end_date=train_end_date,
            test_end_date=test_end_date,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_target(
        self,
        close: pd.Series,
        horizon: int,
        target_type: str,
    ) -> pd.Series:
        """Return a target Series with the shift-forward discipline applied.

        The forward log return at bar N looks ahead `horizon` bars.
        We then shift the entire series forward by 1 so bar N receives the
        label derived at bar N-1, ensuring bar N sees no future data.
        """
        # forward log return: at bar N this is log(close[N+horizon] / close[N])
        forward_lr = np.log(close.shift(-horizon) / close)

        if target_type == "direction":
            label = (forward_lr > 0).astype(float)
            # NaN wherever forward_lr is NaN (i.e. the last `horizon` bars)
            label[forward_lr.isna()] = float("nan")
            # Shift forward by 1 bar to prevent look-ahead at bar N
            label = label.shift(1)
            label.name = "direction_label"
        elif target_type == "return_bucket":
            # qcut uses ranks to bin; NaN positions are preserved
            non_nan = forward_lr.dropna()
            buckets = pd.qcut(non_nan, q=3, labels=[0, 1, 2], duplicates="drop")
            label = buckets.reindex(forward_lr.index)
            # Shift forward by 1 bar to prevent look-ahead at bar N
            label = label.shift(1)
            label.name = "return_bucket"
        else:
            raise ValueError(f"Unknown target_type={target_type!r}. Use 'direction' or 'return_bucket'.")

        return label

    def _load_regime_labels(
        self,
        instrument_id: str,
        timeframe: str,
        model_id: str,
    ) -> pd.DataFrame:
        """Query regime_labels from DuckDB and return as a DataFrame indexed by timestamp."""
        try:
            result = self._market_repo._conn.execute(
                """
                SELECT timestamp_utc, state_id, regime_label
                FROM regime_labels
                WHERE model_id = ? AND instrument_id = ? AND timeframe = ?
                ORDER BY timestamp_utc
                """,
                [model_id, instrument_id, timeframe],
            ).fetchall()
        except Exception:
            return pd.DataFrame()

        if not result:
            return pd.DataFrame()

        df = pd.DataFrame(result, columns=["timestamp_utc", "state_id", "regime_label"])
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        return df.set_index("timestamp_utc")

    def _load_close(self, instrument_id: str, timeframe: Timeframe) -> pd.Series:
        """Load close prices for the given instrument/timeframe from DuckDB."""
        _epoch = datetime(1970, 1, 1)
        _far_future = datetime(2100, 1, 1)

        if timeframe == Timeframe.M1:
            rows = self._market_repo.get_bars_1m(instrument_id, _epoch, _far_future)
        else:
            rows = self._market_repo.get_bars_agg(instrument_id, timeframe, _epoch, _far_future)

        if not rows:
            return pd.Series(dtype=float)

        df = pd.DataFrame(rows)
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.set_index("timestamp_utc").sort_index()
        return df["close"]
