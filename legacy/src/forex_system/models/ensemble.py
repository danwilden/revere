"""
Pod-based ensemble model for EV regression.

Architecture: 5 feature pods, each trained on a semantically grouped subset of
features, plus a Ridge stacker trained exclusively on OOS pod predictions.

    Pod 1: Returns + Volatility      → LightGBM Regressor
    Pod 2: Trend + Momentum          → LightGBM Regressor
    Pod 3: Calendar + Session        → Ridge Regression
    Pod 4: Regime                    → LightGBM Regressor
    Pod 5: Microstructure            → Ridge Regression

    Stacker: Ridge([P1_ev, P2_ev, P3_ev, P4_ev, P5_ev])
             trained ONLY on OOS fold predictions — never in-sample

Key constraints:
- Each pod uses identical walk-forward split indices
- Stacker training data is built from OOS pod predictions (stacking)
- Each pod's features are scaled independently (StandardScaler per pod)
- Target: label_ev (ATR-normalized expected value, continuous)

Regression pivot (v2.3):
    - All pod classifiers replaced with regressors
    - predict_proba() renamed to predict_ev(); outputs ensemble_ev (continuous)
    - Metrics: IC (Spearman ρ) per pod, replacing AUC
    - scale_pos_weight and nunique() class checks removed

Usage:
    from forex_system.models.ensemble import PodEnsemble

    ensemble = PodEnsemble(n_splits=5, purge_bars=60)
    result = ensemble.fit(features_df, label_col="label_ev",
                          instrument="EUR_USD", granularity="H4")
    ev_df = ensemble.predict_ev(new_features_df)
    # ev_df has: p1_ev, p2_ev, p3_ev, p4_ev, p5_ev, ensemble_ev, ensemble_signal
"""

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sklearn.base import clone
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline

# ── Pod feature definitions ───────────────────────────────────────────────────

POD_FEATURES: dict[str, list[str]] = {
    "pod1_vol": [   # Returns + Volatility
        "log_ret_1", "log_ret_4",
        "rvol_10", "rvol_20", "atr_pct_14",
        "bb_width_20", "vol_ratio_10_60",
    ],
    "pod2_trend": [  # Trend + Momentum
        "ema_spread_12_26", "ema_spread_5_20",
        "macd_hist", "adx_14", "adx_ratio_20",
        "roc_12",
    ],
    "pod3_calendar": [  # Calendar + Session + Price Structure
        "london_open", "ny_close", "bb_pos_20",
    ],
    "pod4_regime": [  # Regime
        "vol_regime_60", "trend_regime_50d", "atr_zscore_252",
    ],
    "pod5_micro": [  # Microstructure
        "ret_autocorr_1_20", "bb_width_20",
    ],
}

POD_NAMES: list[str] = list(POD_FEATURES.keys())


@dataclass
class PodEnsembleResult:
    instrument: str
    granularity: str
    n_splits: int
    pod_ics: dict[str, float] = field(default_factory=dict)
    stacker_ic: float = 0.0
    model_path: Path | None = None

    def summary(self) -> str:
        lines = [
            f"PodEnsemble — {self.instrument} {self.granularity}",
            f"  Stacker OOS IC: {self.stacker_ic:.4f}",
        ]
        for pod, ic in self.pod_ics.items():
            lines.append(f"  {pod}: IC={ic:.4f}")
        return "\n".join(lines)


class PodEnsemble:
    """
    5-pod feature ensemble with Ridge stacker for EV regression.

    Each pod is a separate weak learner trained on a semantically coherent
    feature subset. The stacker is a Ridge regressor fitted on the
    concatenated OOS predictions from all pods — it never sees in-sample data.

    Args:
        n_splits:     Walk-forward folds (default 5).
        purge_bars:   Bars removed from training tail for label leakage (default 60).
        embargo_bars: Buffer between train end and test start (default 10).
        ev_threshold: Absolute pred_ev threshold for signal generation (default 0.05).
    """

    def __init__(
        self,
        n_splits: int = 5,
        purge_bars: int = 60,
        embargo_bars: int = 10,
        ev_threshold: float = 0.05,
    ) -> None:
        self.n_splits = n_splits
        self.purge_bars = purge_bars
        self.embargo_bars = embargo_bars
        self.ev_threshold = ev_threshold

        # Fitted artifacts (populated by fit())
        self._pod_models: dict[str, list] = {}   # pod_name → list of fold models
        self._stacker: Ridge | None = None
        self._pod_scalers: dict[str, list] = {}  # pod_name → list of fold scalers

    # ── Public ──────────────────────────────────────────────────────────────

    def fit(
        self,
        features_df: pd.DataFrame,
        label_col: str,
        instrument: str,
        granularity: str,
    ) -> PodEnsembleResult:
        """
        Train all pods via walk-forward + fit stacker on OOS predictions.

        Args:
            features_df: Output of FeaturePipeline.build() with label_ev column.
                         NaN rows are dropped using all pod features + label.
            label_col:   Continuous label column (use "label_ev").
            instrument:  e.g. "EUR_USD"
            granularity: e.g. "H4"

        Returns:
            PodEnsembleResult with per-pod and stacker OOS IC.
        """
        all_cols = list(set(
            col for cols in POD_FEATURES.values() for col in cols
        )) + [label_col]
        df = features_df.dropna(subset=all_cols).copy()

        y = df[label_col]
        n = len(df)
        fold_size = n // (self.n_splits + 1)

        # Compute walk-forward split indices once (shared across all pods)
        splits = self._compute_splits(n, fold_size)

        # Step 1: Collect OOS predictions from each pod across all folds
        oos_pod_evs: dict[str, pd.Series] = {}
        for pod_name, pod_cols in POD_FEATURES.items():
            X_pod = df[pod_cols]
            oos_evs = self._fit_pod_oos(pod_name, X_pod, y, splits)
            oos_pod_evs[pod_name] = oos_evs
            logger.info(f"{pod_name}: {oos_evs.notna().sum()} OOS predictions collected")

        # Step 2: Build stacker training matrix (only rows where all pods predicted)
        stacker_df = pd.DataFrame(oos_pod_evs)
        valid_stacker_mask = stacker_df.notna().all(axis=1)
        X_stack = stacker_df[valid_stacker_mask]
        y_stack = y[valid_stacker_mask]

        if len(X_stack) < 50:
            logger.warning("Insufficient OOS data for stacker — using uniform weights")
            self._stacker = None
        else:
            self._stacker = Ridge(alpha=1.0)
            self._stacker.fit(X_stack, y_stack)

        # Step 3: Compute OOS IC per pod
        pod_ics = {}
        for pod_name, oos_evs in oos_pod_evs.items():
            valid = oos_evs.notna() & y.notna()
            if valid.sum() > 20:
                ic_corr, _ = spearmanr(oos_evs[valid], y[valid])
                pod_ics[pod_name] = float(ic_corr) if not np.isnan(ic_corr) else 0.0
            else:
                pod_ics[pod_name] = 0.0

        # Stacker IC
        stacker_ic = 0.0
        if self._stacker is not None:
            stk_preds = self._stacker.predict(X_stack)
            ic_corr, _ = spearmanr(stk_preds, y_stack)
            stacker_ic = float(ic_corr) if not np.isnan(ic_corr) else 0.0

        # Step 4: Refit all pods on the full dataset for production inference
        for pod_name, pod_cols in POD_FEATURES.items():
            X_pod = df[pod_cols]
            full_model, full_scaler = self._make_pod_model(pod_name)
            X_scaled = full_scaler.fit_transform(X_pod)
            full_model.fit(X_scaled, y)
            self._pod_models[pod_name] = [full_model]
            self._pod_scalers[pod_name] = [full_scaler]

        # Save
        model_path = self._save(instrument, granularity)

        result = PodEnsembleResult(
            instrument=instrument,
            granularity=granularity,
            n_splits=self.n_splits,
            pod_ics=pod_ics,
            stacker_ic=stacker_ic,
            model_path=model_path,
        )
        logger.info(result.summary())
        return result

    def predict_ev(self, features_df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate per-pod predicted EV + stacked EV for each row.

        Args:
            features_df: Feature DataFrame with all pod columns present.
                         NaN rows are handled gracefully (output NaN).

        Returns:
            DataFrame with columns: p1_ev, p2_ev, p3_ev, p4_ev, p5_ev,
            ensemble_ev (continuous), ensemble_signal (1/-1/0 based on ev_threshold).
        """
        if not self._pod_models:
            raise RuntimeError("PodEnsemble not fitted. Call fit() first.")

        result = features_df.copy()
        pod_ev_cols = []

        for pod_name, pod_cols in POD_FEATURES.items():
            idx = POD_NAMES.index(pod_name) + 1
            col_name = f"p{idx}_ev"
            pod_ev_cols.append(col_name)

            valid_mask = features_df[pod_cols].notna().all(axis=1)
            if not valid_mask.any():
                result[col_name] = np.nan
                continue

            X_valid = features_df.loc[valid_mask, pod_cols]
            scaler = self._pod_scalers[pod_name][0]
            model = self._pod_models[pod_name][0]
            X_scaled = scaler.transform(X_valid)

            preds = model.predict(X_scaled)
            result[col_name] = np.nan
            result.loc[valid_mask, col_name] = preds

        # Stacked EV
        stacker_valid = result[pod_ev_cols].notna().all(axis=1)
        result["ensemble_ev"] = np.nan

        if self._stacker is not None and stacker_valid.any():
            X_stk = result.loc[stacker_valid, pod_ev_cols]
            stk_preds = self._stacker.predict(X_stk)
            result.loc[stacker_valid, "ensemble_ev"] = stk_preds
        elif stacker_valid.any():
            # Fallback: simple mean if stacker not trained
            result.loc[stacker_valid, "ensemble_ev"] = (
                result.loc[stacker_valid, pod_ev_cols].mean(axis=1)
            )

        # Signal from ensemble EV
        result["ensemble_signal"] = 0
        result.loc[result["ensemble_ev"] > self.ev_threshold, "ensemble_signal"] = 1
        result.loc[result["ensemble_ev"] < -self.ev_threshold, "ensemble_signal"] = -1

        return result

    @classmethod
    def load(cls, instrument: str, granularity: str) -> "PodEnsemble":
        """Load a saved PodEnsemble from data/artifacts/."""
        path = settings.data_artifacts / f"{instrument}_{granularity}_ensemble.pkl"
        if not path.exists():
            raise FileNotFoundError(
                f"No ensemble model at {path}. Run fit() first."
            )
        with open(path, "rb") as fh:
            return pickle.load(fh)

    # ── Private ─────────────────────────────────────────────────────────────

    def _compute_splits(self, n: int, fold_size: int) -> list[dict]:
        """Return list of {train_end, test_start, test_end} dicts."""
        splits = []
        for fold_id in range(self.n_splits):
            train_end_idx = (fold_id + 1) * fold_size
            effective_train_end = train_end_idx - self.purge_bars
            test_start_idx = train_end_idx + self.embargo_bars
            test_end_idx = min(test_start_idx + fold_size, n)
            if effective_train_end > 0 and test_start_idx < n:
                splits.append({
                    "fold_id": fold_id,
                    "train_end": effective_train_end,
                    "test_start": test_start_idx,
                    "test_end": test_end_idx,
                })
        return splits

    def _make_pod_model(self, pod_name: str):
        """Return (model, scaler) for a given pod."""
        scaler = StandardScaler()
        if pod_name in ("pod1_vol", "pod2_trend", "pod4_regime"):
            model = lgb.LGBMRegressor(
                n_estimators=100,
                learning_rate=0.05,
                num_leaves=15,
                min_child_samples=30,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                verbose=-1,
            )
        else:  # pod3_calendar, pod5_micro
            model = Ridge(alpha=1.0)
        return model, scaler

    def _fit_pod_oos(
        self,
        pod_name: str,
        X_pod: pd.DataFrame,
        y: pd.Series,
        splits: list[dict],
    ) -> pd.Series:
        """Walk-forward OOS predictions for a single pod."""
        oos_evs = pd.Series(np.nan, index=X_pod.index, name=f"{pod_name}_ev")

        for split in splits:
            train_end = split["train_end"]
            test_start = split["test_start"]
            test_end = split["test_end"]

            X_tr = X_pod.iloc[:train_end]
            y_tr = y.iloc[:train_end]
            X_te = X_pod.iloc[test_start:test_end]

            if len(X_te) == 0:
                continue

            model, scaler = self._make_pod_model(pod_name)
            X_tr_scaled = scaler.fit_transform(X_tr)
            X_te_scaled = scaler.transform(X_te)
            model.fit(X_tr_scaled, y_tr)

            preds = model.predict(X_te_scaled)
            oos_evs.iloc[test_start:test_end] = preds

        return oos_evs

    def _save(self, instrument: str, granularity: str) -> Path:
        artifacts_dir = settings.data_artifacts
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = artifacts_dir / f"{instrument}_{granularity}_ensemble.pkl"
        with open(path, "wb") as fh:
            pickle.dump(self, fh)
        logger.info(f"Saved PodEnsemble: {path.name}")
        return path
