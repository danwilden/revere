"""
TrendFollowStrategy alone — OOS walk-forward validation.

Goal: determine whether TrendFollowStrategy has real OOS edge, or whether
the 0.795/0.804 Sharpe (notebook 05, Trend_H4_Gated) was an in-sample artifact.

Config:
  - TrendFollowStrategy(fast_ema=8, slow_ema=21, adx_threshold=20.0) ALONE
    (no SignalAggregator, no RegimeRouter, no MeanReversion, no Breakout)
  - Structural stops: lookback=10, max_mult=4.0 (CONFIG 1 settings)
  - MINIMUM_HOLD_BARS = 5
  - D1 gate FULL: long signals zeroed when trend_regime_50d < 0 (SMA50 bearish);
                  short signals zeroed when trend_regime_50d > 0 (SMA50 bullish)
  - Trail stop ENABLED via 'atr' column
  - CostModel() — same as all prior runs
  - 5-fold walk-forward; each fold skips first PURGE_BARS + EMBARGO_BARS rows
    for indicator warmup before evaluation
  - USD_JPY + USD_CHF, H4, 2020–now

Run from project root:
    python scripts/run_trend_only.py

Output: results/trend_only_analysis.md
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import forex_system.backtest.engine as _engine_mod
from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import VectorizedBacktester
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.risk.sizing import size_signal
from forex_system.risk.stops import compute_structural_stop
from forex_system.strategy.rules import TrendFollowStrategy

# ── Constants ──────────────────────────────────────────────────────────────────
PAIRS            = ["USD_JPY", "USD_CHF"]
GRAN             = "H4"
N_FOLDS          = 5
INITIAL_EQUITY   = 10_000.0
RISK_PCT         = 0.005
MIN_HOLD         = 5
PURGE_BARS       = 252   # skip per fold for indicator warmup
EMBARGO_BARS     = 10    # additional buffer at fold boundary
WARMUP           = PURGE_BARS + EMBARGO_BARS   # 262 bars total

STRUCTURAL_LOOKBACK  = 10
STRUCTURAL_MAX_MULT  = 4.0

# TF params that produced the in-sample 0.795/0.804 (notebook 05, Trend_H4_Gated)
TF_FAST          = 8
TF_SLOW          = 21
TF_ADX_THRESH    = 20.0


# ── Data loading / feature building ───────────────────────────────────────────

def load_raw(instrument: str, granularity: str) -> pd.DataFrame:
    path = settings.data_raw / f"{instrument}_{granularity}_2020-01-01_now.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Raw data not found: {path}\n"
            "Run notebooks/01_data_pull.ipynb first."
        )
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if "complete" in df.columns:
        df = df[df["complete"]].copy()
    return df


def build_features(instrument: str, h4_df: pd.DataFrame, d_df: pd.DataFrame) -> pd.DataFrame:
    pipeline = FeaturePipeline(horizon=1)
    feat = pipeline.build(
        h4_df,
        include_labels=False,
        filter_incomplete=False,
        daily_df=d_df,
        instrument=instrument,
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


# ── Signal generation ──────────────────────────────────────────────────────────

def gen_tf_signals(feat_df: pd.DataFrame, instrument: str,
                   equity: float = INITIAL_EQUITY,
                   risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    TrendFollowStrategy alone with D1 gate (full) and structural stops.

    D1 gate: long signals blocked when trend_regime_50d < 0 (SMA50 bearish).
             short signals blocked when trend_regime_50d > 0 (SMA50 bullish).
    """
    ohlcv = feat_df[["open", "high", "low", "close"]].copy()

    strat = TrendFollowStrategy(
        fast_ema=TF_FAST,
        slow_ema=TF_SLOW,
        adx_threshold=TF_ADX_THRESH,
    )
    strat_out = strat.generate(ohlcv)
    signal = strat_out["signal"].copy()

    # D1 gate — full mode: filter every bar by SMA50 direction
    gate = feat_df["trend_regime_50d"]
    gate_valid = gate.notna()

    # Block longs when D1 SMA50 is bearish (gate == -1)
    block_long  = gate_valid & (gate < 0) & (signal == 1)
    # Block shorts when D1 SMA50 is bullish (gate == +1)
    block_short = gate_valid & (gate > 0) & (signal == -1)
    # Block everything when gate is NaN (SMA not warmed up yet)
    block_nan   = gate.isna()

    signal[block_long]  = 0
    signal[block_short] = 0
    signal[block_nan]   = 0

    n_sig        = int((signal != 0).sum())
    n_blocked    = int((block_long | block_short | block_nan).sum())
    n_before     = int((strat_out["signal"] != 0).sum())
    logger.debug(
        f"TF+D1 | {instrument} | "
        f"before_gate={n_before} → after_gate={n_sig} "
        f"(blocked={n_blocked}) | "
        f"gate_nan={int(block_nan.sum())} "
        f"block_long={int(block_long.sum())} "
        f"block_short={int(block_short.sum())}"
    )

    # Structural stop
    stop_dist = compute_structural_stop(
        feat_df["high"], feat_df["low"], feat_df["close"],
        direction=signal,
        lookback=STRUCTURAL_LOOKBACK,
        max_mult=STRUCTURAL_MAX_MULT,
    )

    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
        "atr":           feat_df["atr_14"],  # enables trail stop in engine
    })

    return size_signal(result, instrument, equity, risk_pct)


# ── Walk-forward ───────────────────────────────────────────────────────────────

def run_walk_forward(feat_df: pd.DataFrame, instrument: str,
                     n_folds: int = N_FOLDS) -> pd.DataFrame:
    """
    5-fold sequential walk-forward.

    Each fold:
      1. Generate signals on the FULL fold (warm up rolling indicators).
      2. Skip the first WARMUP bars from evaluation (purge + embargo).
      3. Backtest only the post-warmup slice.

    Concatenates the eval slices across folds.
    """
    n = len(feat_df)
    fold_size = n // n_folds
    eval_dfs  = []

    for fold_idx in range(n_folds):
        start_idx = fold_idx * fold_size
        end_idx   = (fold_idx + 1) * fold_size if fold_idx < n_folds - 1 else n
        fold_df   = feat_df.iloc[start_idx:end_idx]

        if len(fold_df) <= WARMUP + 50:
            logger.warning(
                f"Fold {fold_idx}: only {len(fold_df)} bars — fewer than "
                f"{WARMUP+50} needed; skipping."
            )
            continue

        # Generate signals on full fold (allows rolling windows to warm up)
        full_sig_df = gen_tf_signals(fold_df, instrument)

        # Drop warmup rows before evaluation
        eval_sig_df = full_sig_df.iloc[WARMUP:]

        n_eval = len(eval_sig_df)
        n_sig  = int((eval_sig_df["signal"] != 0).sum())
        logger.info(
            f"  Fold {fold_idx} | {instrument} | "
            f"total={len(fold_df)} bars | eval_after_warmup={n_eval} | "
            f"signals={n_sig} ({100*n_sig/max(n_eval,1):.1f}%)"
        )
        eval_dfs.append(eval_sig_df)

    if not eval_dfs:
        raise RuntimeError(f"No valid fold data for {instrument}")

    return pd.concat(eval_dfs)


# ── Backtest runner ────────────────────────────────────────────────────────────

def run_backtest(instrument: str, signal_df: pd.DataFrame,
                 cost_model: CostModel) -> object:
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
    try:
        bt = VectorizedBacktester(initial_equity=INITIAL_EQUITY, cost_model=cost_model)
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


# ── Metrics ────────────────────────────────────────────────────────────────────

def extract_metrics(result, signal_df: pd.DataFrame) -> dict:
    m     = result.metrics
    total = max(len(signal_df), 1)
    n_sig = int((signal_df["signal"] != 0).sum())
    return {
        "sharpe":        m.get("sharpe",        float("nan")),
        "max_dd":        m.get("max_drawdown",   float("nan")),
        "n_trades":      m.get("n_trades",       0),
        "hit_rate":      m.get("hit_rate",       float("nan")),
        "avg_win_pips":  m.get("avg_win_pips",   float("nan")),
        "avg_loss_pips": m.get("avg_loss_pips",  float("nan")),
        "signal_freq":   100.0 * n_sig / total,
    }


# ── Report writer ──────────────────────────────────────────────────────────────

def write_report(results: dict[str, dict], output_path: Path) -> None:
    def fmt_pct(v):
        return f"{v:.1%}" if v == v else "N/A"

    def fmt_f(v, d=3):
        return f"{v:.{d}f}" if v == v else "N/A"

    lines = [
        "# Trend-Only Walk-Forward Analysis",
        "",
        "**Question**: Does TrendFollowStrategy have real OOS edge, or was the",
        "0.795/0.804 Sharpe (notebook 05, Trend_H4_Gated) entirely an in-sample artifact?",
        "",
        f"**Strategy**: `TrendFollowStrategy(fast_ema={TF_FAST}, slow_ema={TF_SLOW},"
        f" adx_threshold={TF_ADX_THRESH})` **alone**",
        "  — no SignalAggregator, no RegimeRouter, no MeanReversion, no Breakout",
        "",
        "**Stops**: Structural stops (lookback=10, max_mult=4.0)",
        "",
        f"**D1 gate**: FULL — long blocked when D1 SMA50 bearish;",
        "  short blocked when D1 SMA50 bullish",
        "",
        f"**Trail stop**: ENABLED",
        "",
        f"**MINIMUM_HOLD_BARS**: {MIN_HOLD}",
        "",
        f"**Walk-forward**: {N_FOLDS} sequential folds, H4 2020–now",
        f"  — each fold skips first {PURGE_BARS} (purge) + {EMBARGO_BARS} (embargo)"
        f" = {WARMUP} bars before evaluation",
        "",
        "**In-sample baseline** (for comparison):",
        "  Notebook 05 single-run: USD_JPY Sharpe 0.795, USD_CHF Sharpe 0.804",
        "  (NOT OOS — full-dataset run, no walk-forward)",
        "",
        "---",
        "",
        "## Results",
        "",
        "| Metric | USD_JPY | USD_CHF |",
        "|--------|---------|---------|",
    ]

    metrics_rows = [
        ("sharpe",       "Sharpe",         lambda v: f"{v:.3f}"),
        ("max_dd",       "Max DD",          lambda v: f"{v:.1%}"),
        ("n_trades",     "N Trades",        lambda v: str(int(v))),
        ("hit_rate",     "Hit Rate",        lambda v: f"{v:.1%}"),
        ("avg_win_pips", "Avg Win (pips)",  lambda v: f"{v:.1f}"),
        ("avg_loss_pips","Avg Loss (pips)", lambda v: f"{v:.1f}"),
        ("signal_freq",  "Signal Freq (%)", lambda v: f"{v:.1f}%"),
    ]

    for key, label, fmt in metrics_rows:
        vals = {}
        for pair in PAIRS:
            v = results[pair].get(key, float("nan"))
            try:
                vals[pair] = fmt(v)
            except (TypeError, ValueError):
                vals[pair] = "N/A"
        lines.append(f"| {label} | {vals['USD_JPY']} | {vals['USD_CHF']} |")

    lines += [
        "",
        "---",
        "",
        "## Comparison: in-sample vs OOS",
        "",
        "| | USD_JPY | USD_CHF |",
        "|-|---------|---------|",
        "| In-sample (notebook 05, single run) | 0.795 | 0.804 |",
    ]

    jpy_oos = results["USD_JPY"].get("sharpe", float("nan"))
    chf_oos = results["USD_CHF"].get("sharpe", float("nan"))
    try:
        jpy_str = f"{jpy_oos:.3f}"
        chf_str = f"{chf_oos:.3f}"
    except (TypeError, ValueError):
        jpy_str = chf_str = "N/A"

    lines.append(
        f"| OOS walk-forward (this run, 5-fold, purge={PURGE_BARS}, embargo={EMBARGO_BARS}) "
        f"| {jpy_str} | {chf_str} |"
    )

    lines += [
        "",
        "---",
        "",
        "_Generated by `scripts/run_trend_only.py`_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report written: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("TrendFollowStrategy — OOS Walk-Forward Validation")
    logger.info(
        f"TF params: fast={TF_FAST}, slow={TF_SLOW}, adx_thresh={TF_ADX_THRESH}"
    )
    logger.info(
        f"Structural stops: lookback={STRUCTURAL_LOOKBACK}, max_mult={STRUCTURAL_MAX_MULT}"
    )
    logger.info(f"D1 gate: FULL | min_hold={MIN_HOLD} | trail=ENABLED")
    logger.info(
        f"Walk-forward: {N_FOLDS} folds | purge={PURGE_BARS} | embargo={EMBARGO_BARS}"
    )
    logger.info("=" * 70)

    cost_model = CostModel()
    pair_results: dict[str, dict] = {}

    for pair in PAIRS:
        logger.info(f"\nLoading {pair}...")
        h4_raw = load_raw(pair, "H4")
        d_raw  = load_raw(pair, "D")
        feat_df = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feat_df)} H4 bars after feature build")

        # Gate coverage diagnostic
        n_valid_gate = int(feat_df["trend_regime_50d"].notna().sum())
        n_bull = int((feat_df["trend_regime_50d"] > 0).sum())
        n_bear = int((feat_df["trend_regime_50d"] < 0).sum())
        logger.info(
            f"{pair}: trend_regime_50d valid={n_valid_gate} | "
            f"bullish={n_bull} ({100*n_bull/max(len(feat_df),1):.1f}%) | "
            f"bearish={n_bear} ({100*n_bear/max(len(feat_df),1):.1f}%)"
        )

        logger.info(f"{pair}: running walk-forward...")
        signal_df = run_walk_forward(feat_df, pair)

        n_sig_total = int((signal_df["signal"] != 0).sum())
        n_total     = len(signal_df)
        logger.info(
            f"{pair}: total eval bars={n_total} | "
            f"signals={n_sig_total} ({100*n_sig_total/max(n_total,1):.1f}%)"
        )

        result = run_backtest(pair, signal_df, cost_model)
        metrics = extract_metrics(result, signal_df)
        pair_results[pair] = metrics

        logger.info(
            f"{pair}: Sharpe={metrics['sharpe']:.3f} | "
            f"MaxDD={metrics['max_dd']:.1%} | "
            f"N={metrics['n_trades']} | "
            f"HR={metrics['hit_rate']:.1%} | "
            f"AvgW={metrics['avg_win_pips']:.1f}p | "
            f"AvgL={metrics['avg_loss_pips']:.1f}p | "
            f"Freq={metrics['signal_freq']:.1f}%"
        )

    # Write report
    output_path = PROJECT_ROOT / "results" / "trend_only_analysis.md"
    write_report(pair_results, output_path)

    # Console summary
    print("\n" + "=" * 70)
    print("TREND-ONLY OOS RESULTS")
    print("=" * 70)
    print(
        f"{'Pair':<10} {'Sharpe':>8} {'MaxDD':>8} {'N':>6} "
        f"{'HR':>6} {'AvgW':>7} {'AvgL':>7} {'Freq':>7}"
    )
    print("-" * 70)
    for pair in PAIRS:
        m = pair_results[pair]
        print(
            f"{pair:<10} {m['sharpe']:>8.3f} {m['max_dd']:>8.1%} {m['n_trades']:>6} "
            f"{m['hit_rate']:>6.1%} {m['avg_win_pips']:>7.1f} {m['avg_loss_pips']:>7.1f} "
            f"{m['signal_freq']:>6.1f}%"
        )
    print("=" * 70)
    print("\nIn-sample baseline (notebook 05): USD_JPY 0.795 | USD_CHF 0.804")
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
