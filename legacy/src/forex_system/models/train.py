"""
Walk-forward model training with purge/embargo to prevent data leakage.

Walk-forward split timeline for each fold:
    [─────────── TRAIN ───────────] [─PURGE─] [─EMBARGO─] [──TEST──]

    purge   = MAX_LOOKBACK bars removed from the tail of the training window.
              These bars have labels that "look into" the test window due to
              overlapping rolling features.
    embargo = additional buffer to prevent leakage from correlated residuals.

Supports model types: "lightgbm", "xgboost", "ridge"

Regression pivot (v2.3):
    - Target: label_ev (ATR-normalized expected value, clipped to triple-barrier)
    - Classifiers (LGBMClassifier, XGBClassifier, LogisticRegression) replaced with
      regressors (LGBMRegressor, XGBRegressor, Ridge)
    - Platt scaling removed (classification-only)
    - Metrics: MSE, R², IC (Spearman ρ between pred_ev and label_ev)
    - generate_oos_predictions() returns continuous pred_ev, not probabilities

Usage:
    from forex_system.models.train import WalkForwardTrainer

    trainer = WalkForwardTrainer(model_type="lightgbm", n_splits=5)
    result = trainer.fit(features_df, instrument="EUR_USD", granularity="H1")
    print(f"Mean IC: {result.mean_ic:.3f}")
"""

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from loguru import logger
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline

# Feature columns used for ML (no raw OHLCV, no labels, no atr_14 raw value)
ML_FEATURE_COLS: list[str] = FeaturePipeline.ML_FEATURE_COLS


@dataclass
class FoldResult:
    fold_id: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    mse: float
    r2: float
    ic: float   # Spearman rank correlation between pred_ev and label_ev
    model_path: Path


@dataclass
class WalkForwardResult:
    instrument: str
    granularity: str
    model_type: str
    folds: list[FoldResult] = field(default_factory=list)
    feature_hash: str = ""

    @property
    def mean_mse(self) -> float:
        if not self.folds:
            return 0.0
        return float(np.mean([f.mse for f in self.folds]))

    @property
    def mean_ic(self) -> float:
        if not self.folds:
            return 0.0
        return float(np.mean([f.ic for f in self.folds]))

    @property
    def mean_r2(self) -> float:
        if not self.folds:
            return 0.0
        return float(np.mean([f.r2 for f in self.folds]))

    def summary(self) -> pd.DataFrame:
        """Return a DataFrame summarising per-fold results."""
        return pd.DataFrame(
            [
                {
                    "fold": f.fold_id,
                    "train_start": f.train_start,
                    "train_end": f.train_end,
                    "test_start": f.test_start,
                    "test_end": f.test_end,
                    "mse": f.mse,
                    "r2": f.r2,
                    "ic": f.ic,
                }
                for f in self.folds
            ]
        )


class WalkForwardTrainer:
    """
    Walk-forward trainer for regression on label_ev (ATR-normalized expected value).

    Args:
        model_type:    "lightgbm" | "xgboost" | "ridge"
        n_splits:      Number of walk-forward folds (default 5).
        purge_bars:    Bars removed from training tail to avoid leakage.
                       Should equal FeaturePipeline.MAX_LOOKBACK (252).
        embargo_bars:  Additional buffer after purge (default 10).
        label_col:     Target column name (default "label_ev").
        model_params:  Override default model hyperparameters.
    """

    def __init__(
        self,
        model_type: str = "lightgbm",
        n_splits: int = 5,
        purge_bars: int = FeaturePipeline.MAX_LOOKBACK,
        embargo_bars: int = 10,
        label_col: str = "label_ev",
        model_params: dict | None = None,
    ) -> None:
        self.model_type = model_type
        self.n_splits = n_splits
        self.purge_bars = purge_bars
        self.embargo_bars = embargo_bars
        self.label_col = label_col
        self.model_params = model_params or {}

    # ── Public ──────────────────────────────────────────────────────────────

    def fit(
        self,
        features_df: pd.DataFrame,
        instrument: str,
        granularity: str,
    ) -> WalkForwardResult:
        """
        Run walk-forward training.

        Args:
            features_df: Output of FeaturePipeline.build(), including label_ev.
                         NaN rows are dropped before splitting.
            instrument:  e.g. "EUR_USD"
            granularity: e.g. "H1"

        Returns:
            WalkForwardResult with per-fold metrics (MSE, R², IC) and saved model paths.
        """
        required_cols = ML_FEATURE_COLS + [self.label_col]
        df = features_df.dropna(subset=required_cols).copy()

        if len(df) < (self.n_splits + 1) * (self.purge_bars + self.embargo_bars + 50):
            logger.warning(
                f"Dataset may be too small for {self.n_splits} splits "
                f"with purge={self.purge_bars} embargo={self.embargo_bars}"
            )

        X = df[ML_FEATURE_COLS]
        y = df[self.label_col]
        n = len(df)
        fold_size = n // (self.n_splits + 1)

        result = WalkForwardResult(
            instrument=instrument,
            granularity=granularity,
            model_type=self.model_type,
            feature_hash=FeaturePipeline().feature_hash(),
        )

        for fold_id in range(self.n_splits):
            train_end_idx = (fold_id + 1) * fold_size
            # Purge: remove last purge_bars from training (label leakage)
            effective_train_end = train_end_idx - self.purge_bars
            test_start_idx = train_end_idx + self.embargo_bars
            test_end_idx = min(test_start_idx + fold_size, n)

            if effective_train_end <= 0 or test_end_idx > n or test_start_idx >= n:
                logger.warning(f"Fold {fold_id}: insufficient data, skipping")
                continue

            X_train = X.iloc[:effective_train_end]
            y_train = y.iloc[:effective_train_end]
            X_test = X.iloc[test_start_idx:test_end_idx]
            y_test = y.iloc[test_start_idx:test_end_idx]

            if len(X_test) == 0:
                logger.warning(f"Fold {fold_id}: empty test set, skipping")
                continue

            model = self._make_model()
            model.fit(X_train, y_train)

            preds = model.predict(X_test)

            mse = float(mean_squared_error(y_test, preds))
            r2 = float(r2_score(y_test, preds))
            ic_corr, _ = spearmanr(preds, y_test)
            ic = float(ic_corr) if not np.isnan(ic_corr) else 0.0

            model_path = self._save_model(model, instrument, granularity, fold_id)

            fold_res = FoldResult(
                fold_id=fold_id,
                train_start=df.index[0],
                train_end=df.index[effective_train_end - 1],
                test_start=df.index[test_start_idx],
                test_end=df.index[test_end_idx - 1],
                mse=mse,
                r2=r2,
                ic=ic,
                model_path=model_path,
            )
            result.folds.append(fold_res)

            logger.info(
                f"Fold {fold_id}: mse={mse:.4f} r2={r2:.3f} ic={ic:.3f} | "
                f"train=[{fold_res.train_start.date()} → {fold_res.train_end.date()}] | "
                f"test=[{fold_res.test_start.date()} → {fold_res.test_end.date()}]"
            )

        logger.info(
            f"Walk-forward complete: {instrument} {granularity} {self.model_type} | "
            f"folds={len(result.folds)} mean_ic={result.mean_ic:.3f}"
        )
        return result

    # ── Private ─────────────────────────────────────────────────────────────

    def _make_model(self):
        if self.model_type == "lightgbm":
            defaults = dict(
                n_estimators=300,
                learning_rate=0.05,
                num_leaves=31,
                min_child_samples=50,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                verbose=-1,
            )
            return lgb.LGBMRegressor(**{**defaults, **self.model_params})
        elif self.model_type == "xgboost":
            defaults = dict(
                n_estimators=300,
                learning_rate=0.05,
                max_depth=4,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="reg:squarederror",
                random_state=42,
                verbosity=0,
            )
            return xgb.XGBRegressor(**{**defaults, **self.model_params})
        elif self.model_type in ("ridge", "logistic"):
            # "logistic" kept as alias for backward compatibility with notebook configs
            return Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("reg", Ridge(alpha=1.0)),
                ]
            )
        else:
            raise ValueError(
                f"Unknown model_type: '{self.model_type}'. "
                "Choose 'lightgbm', 'xgboost', or 'ridge'."
            )

    def _save_model(
        self,
        model,
        instrument: str,
        granularity: str,
        fold_id: int,
    ) -> Path:
        artifacts_dir = settings.data_artifacts
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = (
            artifacts_dir
            / f"{instrument}_{granularity}_{self.model_type}_fold{fold_id}.pkl"
        )
        with open(path, "wb") as fh:
            pickle.dump({"model": model}, fh)
        logger.debug(f"Saved model: {path.name}")
        return path


# ── Module-level utilities ────────────────────────────────────────────────────


def generate_oos_predictions(
    X: pd.DataFrame,
    y: pd.Series,
    model,
    n_splits: int = 5,
    purge_bars: int = 60,
    embargo_bars: int = 10,
) -> pd.Series:
    """
    Generate out-of-sample predicted EV for stacking.

    Trains `model` on each walk-forward training window and predicts on the
    corresponding test window. Returns a Series of OOS predicted EV values
    aligned to the original X index — only test-window rows are populated
    (NaN elsewhere).

    Used to build stacker training data in PodEnsemble: each pod calls this
    function and the resulting OOS predictions become the stacker's features.

    Args:
        X:            Feature DataFrame (pre-filtered, no NaN in model features).
        y:            Continuous label_ev Series aligned with X.
        model:        Unfitted sklearn-compatible regressor (will be cloned per fold).
        n_splits:     Walk-forward folds.
        purge_bars:   Bars removed from training tail (label contamination).
        embargo_bars: Buffer between train end and test start.

    Returns:
        pd.Series of OOS predicted EV values, NaN for non-test rows.
    """
    import sklearn.base as skbase

    n = len(X)
    fold_size = n // (n_splits + 1)
    oos_preds = pd.Series(np.nan, index=X.index, name="pred_ev")

    for fold_id in range(n_splits):
        train_end_idx = (fold_id + 1) * fold_size
        effective_train_end = train_end_idx - purge_bars
        test_start_idx = train_end_idx + embargo_bars
        test_end_idx = min(test_start_idx + fold_size, n)

        if effective_train_end <= 0 or test_start_idx >= n:
            continue

        X_train = X.iloc[:effective_train_end]
        y_train = y.iloc[:effective_train_end]
        X_test = X.iloc[test_start_idx:test_end_idx]

        if len(X_test) == 0:
            continue

        fold_model = skbase.clone(model)
        fold_model.fit(X_train, y_train)
        preds = fold_model.predict(X_test)
        oos_preds.iloc[test_start_idx:test_end_idx] = preds

    return oos_preds
