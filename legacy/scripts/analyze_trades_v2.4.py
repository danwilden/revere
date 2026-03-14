"""
Per-trade P&L breakdown for v2.4 (USD_JPY + USD_CHF H4).

Runs the same pipeline as validate_v2.4.py but dumps:
  - avg winning trade (pips)
  - avg losing trade (pips)
  - profit factor
  - win/loss split by exit reason (stop_hit vs signal_exit)
  - distribution percentiles

Run from project root:
    python scripts/analyze_trades_v2.4.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import VectorizedBacktester
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.strategy.signals import RegimeRouter

PAIRS     = ["USD_JPY", "USD_CHF"]
GRAN      = "H4"
N_FOLDS   = 5
INITIAL_EQUITY = 10_000.0
RISK_PCT  = 0.005
PIP_SIZES = {"USD_JPY": 0.01, "USD_CHF": 0.0001}


def load_raw(instrument: str, granularity: str) -> pd.DataFrame:
    path = settings.data_raw / f"{instrument}_{granularity}_2020-01-01_now.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Raw data not found: {path}\nRun 01_data_pull.ipynb first.")
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if "complete" in df.columns:
        df = df[df["complete"]].copy()
    return df


def build_features(instrument: str, h4_df: pd.DataFrame, d_df: pd.DataFrame) -> pd.DataFrame:
    pipeline = FeaturePipeline(horizon=1)
    feat = pipeline.build(
        h4_df, include_labels=False, filter_incomplete=False,
        daily_df=d_df, instrument=instrument,
    )
    feat = feat.dropna(subset=["adx_14", "vol_ratio_10_60", "atr_14"])
    if feat.index.duplicated().any():
        feat = feat[~feat.index.duplicated(keep="last")]
    ohlc = h4_df[["open", "high", "low", "close"]].copy()
    if ohlc.index.duplicated().any():
        ohlc = ohlc[~ohlc.index.duplicated(keep="last")]
    ohlc = ohlc.reindex(feat.index)
    for col in ["open", "high", "low", "close"]:
        feat[col] = ohlc[col]
    return feat


def build_signals_walkforward(instrument: str, feat_df: pd.DataFrame) -> pd.DataFrame:
    pip_size = PIP_SIZES.get(instrument, 0.0001)
    router   = RegimeRouter()
    n        = len(feat_df)
    fold_size = n // N_FOLDS
    parts = []
    for fold_idx in range(N_FOLDS):
        start = fold_idx * fold_size
        end   = (fold_idx + 1) * fold_size if fold_idx < N_FOLDS - 1 else n
        fold_df = feat_df.iloc[start:end]
        if len(fold_df) < 100:
            continue
        sig_df = router.route(fold_df, instrument=instrument, pip_size=pip_size,
                              equity=INITIAL_EQUITY, risk_pct=RISK_PCT)
        parts.append(sig_df)
    return pd.concat(parts)


def apply_correlation_guard(sig_jpy: pd.DataFrame, sig_chf: pd.DataFrame):
    shared = sig_jpy.index.intersection(sig_chf.index)
    jpy_s  = sig_jpy["signal"].reindex(shared)
    chf_s  = sig_chf["signal"].reindex(shared)
    mask   = (jpy_s != 0) & (chf_s != 0) & (jpy_s == chf_s)
    chf_out = sig_chf.copy()
    chf_out.loc[mask, "signal"] = 0
    chf_out.loc[mask, "units"]  = 0
    return sig_jpy.copy(), chf_out


def trade_stats(trades_df: pd.DataFrame, instrument: str) -> None:
    if trades_df.empty:
        print(f"\n{instrument}: no trades")
        return

    wins  = trades_df[trades_df["pnl_pips"] > 0]["pnl_pips"]
    losses = trades_df[trades_df["pnl_pips"] <= 0]["pnl_pips"]

    gross_win  = wins.sum()
    gross_loss = abs(losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    print(f"\n{'='*60}")
    print(f"  {instrument}  ({len(trades_df)} trades)")
    print(f"{'='*60}")
    print(f"  Hit rate          : {len(wins)/len(trades_df)*100:.1f}%  "
          f"({len(wins)} wins / {len(losses)} losses)")
    print(f"  Avg winning trade : +{wins.mean():.1f} pips")
    print(f"  Avg losing trade  : {losses.mean():.1f} pips")
    print(f"  Max winning trade : +{wins.max():.1f} pips")
    print(f"  Max losing trade  : {losses.min():.1f} pips")
    print(f"  Reward/risk ratio : {abs(wins.mean()/losses.mean()):.3f}")
    print(f"  Profit factor     : {pf:.3f}")
    print(f"  Gross win (pips)  : +{gross_win:.1f}")
    print(f"  Gross loss (pips) : {-gross_loss:.1f}")

    # Pips distribution percentiles
    pcts = [10, 25, 50, 75, 90]
    p_vals = np.percentile(trades_df["pnl_pips"], pcts)
    print(f"\n  P&L percentiles (pips):")
    for p, v in zip(pcts, p_vals):
        print(f"    p{p:2d}: {v:+.1f}")

    # By exit reason
    print(f"\n  By exit reason:")
    for reason, grp in trades_df.groupby("exit_reason"):
        grp_wins  = grp[grp["pnl_pips"] > 0]
        grp_loss  = grp[grp["pnl_pips"] <= 0]
        print(f"    {reason:<15} n={len(grp):3d} | "
              f"avg_win={grp_wins['pnl_pips'].mean():+.1f} | "
              f"avg_loss={grp_loss['pnl_pips'].mean():.1f} | "
              f"hit={len(grp_wins)/len(grp)*100:.1f}%")

    # Trail activation rate (proxy: trades with pnl > 0 AND exit=stop_hit)
    trail_proxy = trades_df[
        (trades_df["exit_reason"] == "stop_hit") & (trades_df["pnl_pips"] > 0)
    ]
    print(f"\n  Trail stop triggered (stop_hit with +pnl): "
          f"{len(trail_proxy)} trades ({len(trail_proxy)/len(trades_df)*100:.1f}%)")
    if not trail_proxy.empty:
        print(f"    Avg pips captured when trail fired: +{trail_proxy['pnl_pips'].mean():.1f}")


def main() -> None:
    logger.info("Loading data and building features...")
    feature_dfs = {}
    for pair in PAIRS:
        h4_raw = load_raw(pair, "H4")
        d_raw  = load_raw(pair, "D")
        feature_dfs[pair] = build_features(pair, h4_raw, d_raw)

    logger.info("Generating walk-forward signals...")
    signal_dfs = {pair: build_signals_walkforward(pair, feature_dfs[pair]) for pair in PAIRS}

    logger.info("Applying correlation guard...")
    signal_dfs["USD_JPY"], signal_dfs["USD_CHF"] = apply_correlation_guard(
        signal_dfs["USD_JPY"], signal_dfs["USD_CHF"]
    )

    logger.info("Running backtests...")
    backtester = VectorizedBacktester(initial_equity=INITIAL_EQUITY, cost_model=CostModel())
    results = {}
    for pair in PAIRS:
        results[pair] = backtester.run(pair, GRAN, signal_dfs[pair])

    print("\n\n" + "="*60)
    print("  v2.4 PER-TRADE P&L BREAKDOWN")
    print("="*60)

    all_trades = []
    for pair in PAIRS:
        tdf = results[pair].trades_df()
        trade_stats(tdf, pair)
        all_trades.append(tdf)

    # Combined
    combined = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    if not combined.empty:
        print(f"\n{'='*60}")
        print("  COMBINED (both pairs)")
        print(f"{'='*60}")
        wins   = combined[combined["pnl_pips"] > 0]["pnl_pips"]
        losses = combined[combined["pnl_pips"] <= 0]["pnl_pips"]
        pf     = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
        print(f"  Total trades      : {len(combined)}")
        print(f"  Hit rate          : {len(wins)/len(combined)*100:.1f}%")
        print(f"  Avg winning trade : +{wins.mean():.1f} pips")
        print(f"  Avg losing trade  : {losses.mean():.1f} pips")
        print(f"  Reward/risk ratio : {abs(wins.mean()/losses.mean()):.3f}")
        print(f"  Profit factor     : {pf:.3f}")

    print()


if __name__ == "__main__":
    main()
