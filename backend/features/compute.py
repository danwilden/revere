"""Feature computation pipeline.

Computes a versioned, reproducible feature set from stored bars.
No look-ahead: all features at bar N use only bars 0..N-1 (via shift/rolling).

Feature set 'default_v1':
  - log_ret_1       — 1-bar log return
  - log_ret_5       — 5-bar log return
  - log_ret_20      — 20-bar log return
  - rvol_20         — 20-bar rolling volatility (annualised)
  - atr_14          — Average True Range (14)
  - atr_pct_14      — ATR as fraction of close
  - rsi_14          — RSI (14)
  - ema_slope_20    — EMA-20 slope (change per bar, normalised by close)
  - ema_slope_50    — EMA-50 slope
  - adx_14          — ADX (14)
  - breakout_20     — close vs rolling 20-bar high/low range (0=low, 1=high)
  - session         — 0=Asia, 1=London, 2=NY, 3=London/NY overlap (int)
  - day_of_week     — 0=Monday … 6=Sunday (timestamp-derived, zero leakage)
  - hour_of_day     — UTC hour 0–23 (timestamp-derived, zero leakage)
  - is_friday       — 1 if day_of_week==4, else 0
  - minute_of_hour  — 0–59 UTC minute
  - week_of_year    — ISO week number 1–53
  - month_of_year   — 1–12
  - *_sin / *_cos   — cyclical encodings for minute_of_hour, hour_of_day,
                       day_of_week, week_of_year, month_of_year
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.data.repositories import MarketDataRepository, MetadataRepository
from backend.schemas.enums import Timeframe

# Bump this when the computation logic changes to invalidate old feature runs.
FEATURE_CODE_VERSION = "v1.2"


# ---------------------------------------------------------------------------
# Core indicator functions (no ta dependency — pure numpy/pandas)
# ---------------------------------------------------------------------------

def _log_ret(close: pd.Series, periods: int) -> pd.Series:
    return np.log(close / close.shift(periods)).rename(f"log_ret_{periods}")


def _rvol(close: pd.Series, window: int = 20) -> pd.Series:
    lr = np.log(close / close.shift(1))
    # annualise assuming 252 trading days; for intraday this is a relative measure
    return (lr.rolling(window).std() * np.sqrt(252)).rename(f"rvol_{window}")


def _ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def _ema_slope(close: pd.Series, span: int) -> pd.Series:
    ema = _ema(close, span)
    slope = ema.diff(1) / close
    return slope.rename(f"ema_slope_{span}")


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean().rename(f"atr_{window}")


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff(1)
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=window - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=window - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).rename(f"rsi_{window}")


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr_s = pd.Series(tr).ewm(com=window - 1, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(com=window - 1, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(com=window - 1, adjust=False).mean() / atr_s.replace(0, np.nan)

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(com=window - 1, adjust=False).mean().rename(f"adx_{window}")


def _breakout(close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    roll_high = high.rolling(window).max()
    roll_low = low.rolling(window).min()
    rng = (roll_high - roll_low).replace(0, np.nan)
    return ((close - roll_low) / rng).rename(f"breakout_{window}")


def _session(index: pd.DatetimeIndex) -> pd.Series:
    """Forex session indicator based on UTC hour:
    0 = Asia        (00:00–07:59 UTC)
    1 = London      (08:00–12:59 UTC)
    2 = Overlap     (13:00–16:59 UTC) — London/NY overlap
    3 = NY          (17:00–20:59 UTC)
    4 = Off-hours   (21:00–23:59 UTC)
    """
    hour = pd.Series(index.hour, index=index)
    session = pd.Series(0, index=index, dtype=float, name="session")
    session[hour.between(8, 12)] = 1.0
    session[hour.between(13, 16)] = 2.0
    session[hour.between(17, 20)] = 3.0
    session[hour >= 21] = 4.0
    return session


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def compute_features(
    df: pd.DataFrame,
    feature_set_name: str = "default_v1",
) -> pd.DataFrame:
    """Compute all features from a bar DataFrame.

    Args:
        df: DataFrame with columns [open, high, low, close, volume] and
            DatetimeIndex (timestamp_utc). Must be sorted ascending.
        feature_set_name: identifier for this feature set.

    Returns:
        Wide DataFrame with same index as df and one column per feature.
        Leading NaN rows arise from rolling windows — callers must decide
        whether to dropna before training.
    """
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]
    idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)

    feats = pd.DataFrame(index=df.index)
    feats["log_ret_1"] = _log_ret(close, 1)
    feats["log_ret_5"] = _log_ret(close, 5)
    feats["log_ret_20"] = _log_ret(close, 20)
    feats["rvol_20"] = _rvol(close, 20)
    feats["atr_14"] = _atr(high, low, close, 14)
    feats["atr_pct_14"] = feats["atr_14"] / close.replace(0, np.nan)
    feats["rsi_14"] = _rsi(close, 14)
    feats["ema_slope_20"] = _ema_slope(close, 20)
    feats["ema_slope_50"] = _ema_slope(close, 50)
    feats["adx_14"] = _adx(high, low, close, 14)
    feats["breakout_20"] = _breakout(close, high, low, 20)
    feats["session"] = _session(idx)

    # --- Calendar / time-of-day features (zero leakage: derived from timestamp only) ---
    ts_series = idx.to_series() if isinstance(idx, pd.DatetimeIndex) else pd.to_datetime(df["timestamp_utc"])
    feats["day_of_week"] = ts_series.dt.weekday.values   # 0=Mon, 6=Sun
    feats["hour_of_day"] = ts_series.dt.hour.values       # 0-23 UTC
    feats["is_friday"] = (ts_series.dt.weekday == 4).astype(int).values  # 1 on Friday, 0 otherwise
    feats["minute_of_hour"] = ts_series.dt.minute.values   # 0-59
    feats["week_of_year"] = idx.isocalendar().week.astype(int).values  # ISO week 1-53
    feats["month_of_year"] = ts_series.dt.month.values     # 1-12

    # --- Cyclical (sin/cos) encodings for periodic calendar fields ---
    tau = 2 * np.pi
    feats["minute_of_hour_sin"] = np.sin(tau * feats["minute_of_hour"] / 60)
    feats["minute_of_hour_cos"] = np.cos(tau * feats["minute_of_hour"] / 60)
    feats["hour_of_day_sin"] = np.sin(tau * feats["hour_of_day"] / 24)
    feats["hour_of_day_cos"] = np.cos(tau * feats["hour_of_day"] / 24)
    feats["day_of_week_sin"] = np.sin(tau * feats["day_of_week"] / 7)
    feats["day_of_week_cos"] = np.cos(tau * feats["day_of_week"] / 7)
    feats["week_of_year_sin"] = np.sin(tau * (feats["week_of_year"] - 1) / 52)
    feats["week_of_year_cos"] = np.cos(tau * (feats["week_of_year"] - 1) / 52)
    feats["month_of_year_sin"] = np.sin(tau * (feats["month_of_year"] - 1) / 12)
    feats["month_of_year_cos"] = np.cos(tau * (feats["month_of_year"] - 1) / 12)

    return feats


def _params_hash(params: dict) -> str:
    s = json.dumps(params, sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def run_feature_pipeline(
    instrument_id: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    metadata_repo: MetadataRepository,
    feature_set_name: str = "default_v1",
    force: bool = False,
) -> str:
    """Compute features for an instrument/timeframe window and persist to DuckDB.

    Returns:
        feature_run_id — the ID of the created (or existing) FeatureRun record.
    """
    params: dict[str, Any] = {
        "instrument_id": instrument_id,
        "timeframe": timeframe.value,
        "feature_set_name": feature_set_name,
        "code_version": FEATURE_CODE_VERSION,
    }

    if timeframe == Timeframe.M1:
        raw = market_repo.get_bars_1m(instrument_id, start, end)
    else:
        raw = market_repo.get_bars_agg(instrument_id, timeframe, start, end)

    if not raw:
        raise ValueError(
            f"No bars found for {instrument_id} {timeframe.value} "
            f"in [{start}, {end})"
        )

    df = pd.DataFrame(raw)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
    df = df.set_index("timestamp_utc").sort_index()

    feature_df = compute_features(df, feature_set_name)

    # Create feature run record
    run_id = str(uuid.uuid4())
    feature_run = {
        "id": run_id,
        "feature_set_name": feature_set_name,
        "code_version": FEATURE_CODE_VERSION,
        "parameters_json": json.dumps(params),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "created_at": datetime.utcnow().isoformat(),
    }
    metadata_repo.save_feature_run(feature_run)

    # Write tall-format feature rows to DuckDB
    rows: list[dict] = []
    for ts, row in feature_df.iterrows():
        for feat_name, feat_val in row.items():
            val = None if (feat_val is None or (isinstance(feat_val, float) and np.isnan(feat_val))) else float(feat_val)
            rows.append({
                "instrument_id": instrument_id,
                "timeframe": timeframe.value,
                "timestamp_utc": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "feature_run_id": run_id,
                "feature_name": feat_name,
                "feature_value": val,
            })

    # Batch upsert
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        market_repo.upsert_features(rows[i : i + BATCH])

    return run_id


def load_feature_matrix(
    instrument_id: str,
    timeframe: Timeframe,
    feature_run_id: str,
    start: datetime,
    end: datetime,
    market_repo: MarketDataRepository,
    dropna: bool = True,
) -> pd.DataFrame:
    """Pivot tall feature rows back into a wide DataFrame.

    Returns:
        DataFrame with DatetimeIndex and one column per feature, sorted ascending.
    """
    rows = market_repo.get_features(instrument_id, timeframe, feature_run_id, start, end)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
    wide = df.pivot(index="timestamp_utc", columns="feature_name", values="feature_value")
    wide = wide.sort_index()

    if dropna:
        wide = wide.dropna()

    return wide
