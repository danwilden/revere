"""
FeaturePipeline: orchestrates transforms into a reproducible feature matrix.

The same pipeline runs in notebooks (research), backtest engine, and live
inference — guaranteeing zero drift between environments.

Usage:
    from forex_system.features.builders import FeaturePipeline

    pipeline = FeaturePipeline(horizon=1)
    features = pipeline.build(raw_df, daily_df=d_raw)   # includes labels
    pipeline.save(features, "EUR_USD", "H4")             # → data/processed/

    # At inference time (no labels):
    features = pipeline.build(raw_df, include_labels=False, daily_df=d_raw)
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from forex_system.config import settings
from forex_system.features.transforms import (
    adx,
    atr,
    atr_pct,
    atr_zscore,
    bb_position,
    bb_width,
    binary_direction,
    carry_differential,
    day_of_week,
    ema_spread,
    expected_value_label,
    forward_return,
    hour_of_day,
    label_barrier_symmetric,  # CHANGE 4 (v2.4)
    label_forward_5,          # CHANGE 4 (v2.4)
    log_returns,
    london_open,
    macd_signal,
    ny_close,
    realized_volatility,
    return_autocorr,
    roc,
    rsi,
    session_overlap,
    trend_regime,
    trend_strength_ratio,
    triple_barrier_label,
    vol_ratio,
    vol_regime,
)

# Bump this string whenever the feature set changes — will invalidate cached files
FEATURE_VERSION = "v2.2"


class FeaturePipeline:
    """
    Builds a feature + label DataFrame from OHLCV candle data.

    Guarantees:
    - Consistent column set across all pairs and timeframes
    - feature_hash() uniquely identifies this pipeline configuration
    - Incomplete bars filtered before feature computation (unless disabled)
    - NaN rows from rolling windows are NOT dropped — caller decides

    v2.2 changes vs v2.1:
    - Dropped from ML_FEATURE_COLS: carry_diff (constant per pair, |IC|<0.02),
      day_of_week (|IC|<0.02 dead feature). Both still computed for compatibility.
    - Replaced ny_overlap with london_open + ny_close (more precise session splits)
    - Added: vol_ratio_10_60 (vol compression/expansion signal)
    - Added label: label_ev (continuous EV target for LGBMRegressor)
    - Net: 20 - 2 - 1 + 2 + 1 = 20 ML features (same count, different composition)

    v2.1 changes vs v2.0:
    - Added: ny_overlap, ret_autocorr_1_20, atr_zscore_252, carry_diff (4 new features)
    - Net: 16 + 4 = 20 ML features

    v2.0 changes vs v1.0:
    - Added: vol_regime_60, trend_regime_50d, adx_ratio_20 (3 new features)
    - Removed from ML_FEATURE_COLS: rsi_14, hour_of_day (still computed for compat)
    - Added: triple_barrier_label (Lopez de Prado, pt=2×ATR, sl=1×ATR, t=20)
    - build() accepts daily_df for regime feature alignment
    """

    # Longest rolling window across all transforms (atr_zscore uses 252).
    # NOTE: purge_bars in WalkForwardTrainer covers label contamination (max_holding=20)
    # and is set independently — it does NOT need to equal MAX_LOOKBACK.
    MAX_LOOKBACK: int = 252

    # Ordered list of ML feature column names (excludes raw OHLCV and labels)
    # v2.2: 20 features — dropped carry_diff + day_of_week (|IC|<0.02);
    #        replaced ny_overlap with london_open + ny_close; added vol_ratio_10_60
    ML_FEATURE_COLS: list[str] = [
        # Returns (2)
        "log_ret_1",
        "log_ret_4",
        # Volatility (6)
        "rvol_10",
        "rvol_20",
        "atr_pct_14",
        "bb_width_20",
        "bb_pos_20",
        "vol_ratio_10_60",    # NEW v2.2: short/long vol ratio (compression/expansion)
        # Trend (5)
        "ema_spread_12_26",
        "ema_spread_5_20",
        "macd_hist",
        "adx_14",
        "adx_ratio_20",
        # Momentum (1)
        "roc_12",
        # Regime (2)
        "vol_regime_60",
        "trend_regime_50d",
        # Calendar (2) — NEW v2.2: precise session splits replace ny_overlap
        "london_open",        # H4 bar at 08:00 UTC (London open)
        "ny_close",           # H4 bar at 16:00 UTC (NY close coverage)
        # Microstructure / orthogonal signals (2)
        "ret_autocorr_1_20",  # rolling return autocorrelation (momentum vs mean-rev)
        "atr_zscore_252",     # ATR relative to its 1-year history
        # Dropped v2.2: carry_diff (constant per pair, |IC|<0.02)
        # Dropped v2.2: day_of_week (|IC|<0.02 dead across all pairs)
        # Dropped v2.2: ny_overlap (replaced by london_open + ny_close)
    ]

    def __init__(
        self,
        horizon: int = 1,
        label_threshold: float = 0.0,
        pt_multiplier: float = 2.0,
        sl_multiplier: float = 1.0,
        max_holding: int = 20,
    ) -> None:
        self.horizon = horizon
        self.label_threshold = label_threshold
        self.pt_multiplier = pt_multiplier
        self.sl_multiplier = sl_multiplier
        self.max_holding = max_holding

    def build(
        self,
        df: pd.DataFrame,
        include_labels: bool = True,
        filter_incomplete: bool = True,
        daily_df: pd.DataFrame | None = None,
        label_type: str = "triple_barrier",
        instrument: str | None = None,
    ) -> pd.DataFrame:
        """
        Compute features (and optionally labels) from an OHLCV DataFrame.

        Args:
            df: OHLCV DataFrame with DatetimeIndex in UTC. Must have columns:
                open, high, low, close, volume. Optional: complete (bool).
            include_labels: If True, adds label columns.
                            Set False for live inference to avoid any label leakage.
            filter_incomplete: Drop bars where complete=False before computing.
            daily_df: Optional D1 OHLCV DataFrame for trend_regime_50d feature.
                      If None, trend_regime_50d is set to NaN (graceful degradation).
            label_type: Which labels to compute when include_labels=True.
                        "triple_barrier" (default), "binary", or "both".
            instrument: Instrument name (e.g. "EUR_USD") for carry_diff computation.
                        If None, carry_diff is set to 0.0.

        Returns:
            DataFrame with feature columns aligned to the input index.
            NaN rows (from rolling windows) are present — drop with .dropna() as needed.
        """
        logger.debug(
            f"Building features | rows={len(df)} | horizon={self.horizon} "
            f"| version={FEATURE_VERSION} | hash={self.feature_hash()}"
        )

        if filter_incomplete and "complete" in df.columns:
            df = df[df["complete"]].copy()
        else:
            df = df.copy()

        close = df["close"]
        high = df["high"]
        low = df["low"]

        feat = pd.DataFrame(index=df.index)

        # Returns and volatility
        feat["log_ret_1"] = log_returns(close, 1)
        feat["log_ret_4"] = log_returns(close, 4)
        feat["rvol_10"] = realized_volatility(close, 10)
        feat["rvol_20"] = realized_volatility(close, 20)

        # Trend
        feat["ema_spread_12_26"] = ema_spread(close, 12, 26)
        feat["ema_spread_5_20"] = ema_spread(close, 5, 20)
        feat["macd_hist"] = macd_signal(close)
        feat["adx_14"] = adx(high, low, close, 14)

        # Volatility
        feat["atr_14"] = atr(high, low, close, 14)
        feat["atr_pct_14"] = atr_pct(high, low, close, 14)
        feat["bb_width_20"] = bb_width(close, 20)
        feat["bb_pos_20"] = bb_position(close, 20)

        # Momentum
        feat["rsi_14"] = rsi(close, 14)   # kept for backward compat, not in ML_FEATURE_COLS
        feat["roc_12"] = roc(close, 12)

        # Calendar
        feat["day_of_week"] = day_of_week(df.index)    # kept for backward compat; dropped from ML_FEATURE_COLS v2.2
        feat["hour_of_day"] = hour_of_day(df.index)    # kept for backward compat
        feat["ny_overlap"] = session_overlap(df.index) # kept for backward compat; replaced by london_open + ny_close
        feat["london_open"] = london_open(df.index)    # NEW v2.2
        feat["ny_close"] = ny_close(df.index)          # NEW v2.2

        # Microstructure / orthogonal signals (v2.1)
        feat["ret_autocorr_1_20"] = return_autocorr(close, lag=1, window=20)
        feat["atr_zscore_252"] = atr_zscore(feat["atr_14"], window=252)
        feat["carry_diff"] = (                          # kept as metadata; dropped from ML_FEATURE_COLS v2.2
            carry_differential(instrument) if instrument else 0.0
        )

        # Volatility ratio (v2.2)
        feat["vol_ratio_10_60"] = vol_ratio(close, short_window=10, long_window=60)

        # Regime features (v2.0)
        if daily_df is not None:
            if "complete" in daily_df.columns:
                daily_close = daily_df.loc[daily_df["complete"], "close"]
            else:
                daily_close = daily_df["close"]
            # trend_regime uses reindex(..., method="ffill") which requires unique index
            if not daily_close.index.is_unique:
                daily_close = daily_close[~daily_close.index.duplicated(keep="last")]
            feat["trend_regime_50d"] = trend_regime(daily_close, df.index, sma_window=50)
        else:
            logger.warning("daily_df not provided — trend_regime_50d set to NaN")
            feat["trend_regime_50d"] = np.nan

        feat["vol_regime_60"] = vol_regime(feat["atr_pct_14"], lookback=60)
        feat["adx_ratio_20"] = trend_strength_ratio(feat["adx_14"], window=20)

        # Labels — look-ahead; only include during research, not live inference
        if include_labels:
            fwd = forward_return(close, self.horizon)
            feat["fwd_ret"] = fwd
            feat["label_direction"] = binary_direction(fwd)  # always for backward compat

            if label_type in ("triple_barrier", "both"):
                feat["label_triple_barrier"] = triple_barrier_label(
                    close,
                    high,
                    low,
                    atr_series=feat["atr_14"],
                    pt_multiplier=self.pt_multiplier,
                    sl_multiplier=self.sl_multiplier,
                    max_holding=self.max_holding,
                )
                feat["label_ev"] = expected_value_label(
                    close,
                    high,
                    low,
                    atr_series=feat["atr_14"],
                    pt_multiplier=self.pt_multiplier,
                    sl_multiplier=self.sl_multiplier,
                    max_holding=self.max_holding,
                )

            # CHANGE 4 (v2.4): additional label schemes — always computed when
            # include_labels=True, regardless of label_type.
            # label_forward_5: unbiased 5-bar forward sign — primary ML target going
            #   forward; eliminates barrier-asymmetry bias in label_ev/triple_barrier.
            # label_barrier_sym: PT=SL=1.5×ATR — expected ~45-55% TP vs 65% SL
            #   in v2.3 (which used PT=2×ATR, SL=1×ATR).
            feat["label_forward_5"] = label_forward_5(close, horizon=5)
            feat["label_barrier_sym"] = label_barrier_symmetric(
                close,
                high,
                low,
                atr_series=feat["atr_14"],
                max_holding=self.max_holding,
                pt_mult=1.5,
                sl_mult=1.5,
            )

        return feat

    def feature_hash(self) -> str:
        """
        Short hash that uniquely identifies this pipeline config.
        Logged with every run for reproducibility auditing.
        """
        payload = json.dumps(
            {
                "version": FEATURE_VERSION,
                "horizon": self.horizon,
                "max_lookback": self.MAX_LOOKBACK,
                "n_features": len(self.ML_FEATURE_COLS),
            },
            sort_keys=True,
        )
        return hashlib.md5(payload.encode()).hexdigest()[:8]

    def save(
        self, features: pd.DataFrame, instrument: str, granularity: str
    ) -> Path:
        """Save processed features to data/processed/ as parquet."""
        out_dir = settings.data_processed
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{instrument}_{granularity}_{FEATURE_VERSION}.parquet"
        features.to_parquet(path)
        logger.info(f"Saved features: {path.name} ({len(features)} rows)")
        return path

    def save_v24(
        self, features: pd.DataFrame, instrument: str, granularity: str
    ) -> Path:
        """
        CHANGE 4 (v2.4): Save versioned parquet with v2.4 label schemes.

        Writes {instrument}_{granularity}_v2.4.parquet alongside existing
        v2.2/v2.3 files (does NOT overwrite them). The v2.4 file includes
        label_forward_5 and label_barrier_sym in addition to all v2.2 features.
        """
        out_dir = settings.data_processed
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{instrument}_{granularity}_v2.4.parquet"
        features.to_parquet(path)
        logger.info(f"Saved v2.4 features+labels: {path.name} ({len(features)} rows)")
        return path

    @staticmethod
    def load(
        instrument: str,
        granularity: str,
        version: str = FEATURE_VERSION,
    ) -> pd.DataFrame:
        """Load previously saved feature matrix from data/processed/."""
        path = (
            settings.data_processed / f"{instrument}_{granularity}_{version}.parquet"
        )
        if not path.exists():
            raise FileNotFoundError(f"No processed features at {path}")
        return pd.read_parquet(path)


# Module-level aliases (mirrors train.py pattern) — import directly from this module
ML_FEATURE_COLS: list[str] = FeaturePipeline.ML_FEATURE_COLS
MAX_LOOKBACK: int = FeaturePipeline.MAX_LOOKBACK
