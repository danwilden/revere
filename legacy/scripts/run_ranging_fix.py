"""
Ranging fix analysis — test two fixes for the RANGING regime signal flood.

Baseline: SignalAggregator + structural stops + min_hold=5
Fix A:    RegimeRouter (RANGING → flat, no trade) + structural stops + min_hold=5
Fix B:    RegimeRouter (RANGING → MR with tighter conditions) + structural stops + min_hold=5
             Tighter: RSI < 25 AND close ≤ BB_lower AND ADX > 12  (longs)
                      RSI > 75 AND close ≥ BB_upper AND ADX > 12  (shorts)

All configs:
  - H4, USD_JPY + USD_CHF, 2020–now, 5-fold walk-forward
  - Trail stop ENABLED via 'atr' column (structural stops pair naturally with trail)
  - D1 gate DISABLED (baseline has no gate; keep constant to isolate RANGING fix)
  - No hysteresis (isolates routing change cleanly)
  - risk_pct=0.5%, equity=10,000, same CostModel

Run from project root:
    python scripts/run_ranging_fix.py

Output: results/ranging_fix_analysis.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import ta
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
from forex_system.strategy.rules import (
    BreakoutStrategy,
    MeanReversionStrategy,
    TrendFollowStrategy,
)
from forex_system.strategy.signals import (
    REGIME_BREAKOUT,
    REGIME_RANGING,
    REGIME_TRENDING,
    classify_regime,
)

# ── Constants ──────────────────────────────────────────────────────────────────
PAIRS = ["USD_JPY", "USD_CHF"]
GRAN = "H4"
N_FOLDS = 5
INITIAL_EQUITY = 10_000.0
RISK_PCT = 0.005
MIN_HOLD = 5
STRUCTURAL_LOOKBACK = 10
STRUCTURAL_MAX_MULT = 4.0


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


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _structural_stop(feat_df: pd.DataFrame, signal: pd.Series) -> pd.Series:
    return compute_structural_stop(
        feat_df["high"], feat_df["low"], feat_df["close"],
        direction=signal,
        lookback=STRUCTURAL_LOOKBACK,
        max_mult=STRUCTURAL_MAX_MULT,
    )


def _build_result_df(feat_df: pd.DataFrame, signal: pd.Series,
                     stop_dist: pd.Series) -> pd.DataFrame:
    """Pack signal+stop into a DataFrame with OHLCV and 'atr' for trail."""
    return pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
        "atr":           feat_df["atr_14"],   # enables trail stop in engine
    })


# ── BASELINE: SignalAggregator + structural stops ──────────────────────────────

def gen_baseline(feat_df: pd.DataFrame, instrument: str,
                 equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    SignalAggregator(min_consensus=2) with structural stops and trail.
    Exact match of bisection CONFIG 1, now with 'atr' column for trail stop.
    """
    ohlcv = feat_df[["open", "high", "low", "close"]].copy()
    trend_out = TrendFollowStrategy().generate(ohlcv)
    mr_out    = MeanReversionStrategy().generate(ohlcv)
    bo_out    = BreakoutStrategy().generate(ohlcv)

    vote_sum = (
        trend_out["signal"]
        + mr_out["signal"]
        + bo_out["signal"]
    )
    signal = pd.Series(0, index=feat_df.index, dtype=int)
    signal[vote_sum >= 2]  = 1
    signal[vote_sum <= -2] = -1

    n_sig = int((signal != 0).sum())
    logger.debug(
        f"Baseline | {instrument} | signals={n_sig}/{len(feat_df)} "
        f"({100*n_sig/max(len(feat_df),1):.1f}%)"
    )

    stop_dist = _structural_stop(feat_df, signal)
    result    = _build_result_df(feat_df, signal, stop_dist)
    return size_signal(result, instrument, equity, risk_pct)


# ── FIX A: RegimeRouter — RANGING → flat ──────────────────────────────────────

def gen_fix_a(feat_df: pd.DataFrame, instrument: str,
              equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    RegimeRouter with RANGING regime disabled (no MeanReversion trades at all).
    TRENDING → TrendFollow, BREAKOUT → Breakout, RANGING → flat, UNDEFINED → flat.
    Structural stops + trail.  No hysteresis.  No D1 gate.
    """
    ohlcv     = feat_df[["open", "high", "low", "close"]].copy()
    raw_atr   = feat_df["atr_14"]
    adx_col   = feat_df["adx_14"]
    vol_ratio = feat_df["vol_ratio_10_60"]

    trend_strat = TrendFollowStrategy(fast_ema=8, slow_ema=21, adx_threshold=25.0)
    bo_strat    = BreakoutStrategy()

    trend_out = trend_strat.generate(ohlcv)
    bo_out    = bo_strat.generate(ohlcv)

    regime = classify_regime(
        adx_col, vol_ratio, raw_atr,
        adx_trending_thresh=25.0,
        adx_ranging_thresh=20.0,
    )

    signal = pd.Series(0, index=feat_df.index, dtype=int)
    signal[regime == REGIME_TRENDING] = trend_out["signal"][regime == REGIME_TRENDING]
    signal[regime == REGIME_BREAKOUT] = bo_out["signal"][regime == REGIME_BREAKOUT]
    # RANGING → flat (Fix A: no MeanRev trades)

    n_sig = int((signal != 0).sum())
    ranging_bars = int((regime == REGIME_RANGING).sum())
    logger.info(
        f"Fix A | {instrument} | signals={n_sig}/{len(feat_df)} "
        f"({100*n_sig/max(len(feat_df),1):.1f}%) | "
        f"trend={int((regime==REGIME_TRENDING).sum())} "
        f"ranging={ranging_bars} (ALL FLAT) "
        f"breakout={int((regime==REGIME_BREAKOUT).sum())} "
        f"undefined={int((~regime.isin([REGIME_TRENDING,REGIME_RANGING,REGIME_BREAKOUT])).sum())}"
    )

    stop_dist = _structural_stop(feat_df, signal)
    result    = _build_result_df(feat_df, signal, stop_dist)
    return size_signal(result, instrument, equity, risk_pct)


# ── FIX B: RegimeRouter — RANGING with tighter MR conditions ──────────────────

def gen_fix_b(feat_df: pd.DataFrame, instrument: str,
              equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    RegimeRouter with RANGING → MeanReversion but tightened entry conditions:
        Long:  RSI(14) < 25  AND  close ≤ BB_lower(20,2)  AND  ADX > 12
        Short: RSI(14) > 75  AND  close ≥ BB_upper(20,2)  AND  ADX > 12

    TRENDING → TrendFollow, BREAKOUT → Breakout unchanged.
    Structural stops + trail.  No hysteresis.  No D1 gate.
    """
    ohlcv     = feat_df[["open", "high", "low", "close"]].copy()
    close     = feat_df["close"]
    raw_atr   = feat_df["atr_14"]
    adx_col   = feat_df["adx_14"]
    vol_ratio = feat_df["vol_ratio_10_60"]

    trend_strat = TrendFollowStrategy(fast_ema=8, slow_ema=21, adx_threshold=25.0)
    bo_strat    = BreakoutStrategy()

    trend_out = trend_strat.generate(ohlcv)
    bo_out    = bo_strat.generate(ohlcv)

    # Tightened MR signal — computed on full fold data (same as strategy.generate())
    rsi_val   = ta.momentum.RSIIndicator(close, window=14).rsi()
    bb        = ta.volatility.BollingerBands(close, window=20, window_dev=2.0)
    bb_lower  = bb.bollinger_lband()
    bb_upper  = bb.bollinger_hband()

    mr_long  = (rsi_val < 25) & (close <= bb_lower) & (adx_col > 12)
    mr_short = (rsi_val > 75) & (close >= bb_upper) & (adx_col > 12)

    mr_tight = pd.Series(0, index=feat_df.index, dtype=int)
    mr_tight[mr_long]  = 1
    mr_tight[mr_short] = -1

    regime = classify_regime(
        adx_col, vol_ratio, raw_atr,
        adx_trending_thresh=25.0,
        adx_ranging_thresh=20.0,
    )

    signal = pd.Series(0, index=feat_df.index, dtype=int)
    signal[regime == REGIME_TRENDING] = trend_out["signal"][regime == REGIME_TRENDING]
    signal[regime == REGIME_BREAKOUT] = bo_out["signal"][regime == REGIME_BREAKOUT]
    signal[regime == REGIME_RANGING]  = mr_tight[regime == REGIME_RANGING]

    ranging_bars = int((regime == REGIME_RANGING).sum())
    mr_ranging_signals = int((signal[regime == REGIME_RANGING] != 0).sum())
    n_sig = int((signal != 0).sum())
    logger.info(
        f"Fix B | {instrument} | signals={n_sig}/{len(feat_df)} "
        f"({100*n_sig/max(len(feat_df),1):.1f}%) | "
        f"trend={int((regime==REGIME_TRENDING).sum())} "
        f"ranging={ranging_bars} (active={mr_ranging_signals}) "
        f"breakout={int((regime==REGIME_BREAKOUT).sum())} "
        f"undefined={int((~regime.isin([REGIME_TRENDING,REGIME_RANGING,REGIME_BREAKOUT])).sum())}"
    )

    stop_dist = _structural_stop(feat_df, signal)
    result    = _build_result_df(feat_df, signal, stop_dist)
    return size_signal(result, instrument, equity, risk_pct)


# ── Walk-forward ───────────────────────────────────────────────────────────────

def wf_signals(feat_df: pd.DataFrame, instrument: str, signal_fn,
               n_folds: int = N_FOLDS) -> pd.DataFrame:
    n = len(feat_df)
    fold_size = n // n_folds
    all_dfs = []

    for fold_idx in range(n_folds):
        start_idx = fold_idx * fold_size
        end_idx   = (fold_idx + 1) * fold_size if fold_idx < n_folds - 1 else n
        fold_df   = feat_df.iloc[start_idx:end_idx]

        if len(fold_df) < 100:
            logger.warning(f"Fold {fold_idx} too small ({len(fold_df)} bars), skipping")
            continue

        sig_df = signal_fn(fold_df, instrument)
        all_dfs.append(sig_df)

    if not all_dfs:
        raise RuntimeError(f"No valid folds for {instrument}")
    return pd.concat(all_dfs)


# ── Backtest runner ────────────────────────────────────────────────────────────

def run_backtest(instrument: str, signal_df: pd.DataFrame,
                 cost_model: CostModel, min_hold: int = MIN_HOLD) -> object:
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = min_hold
    try:
        bt = VectorizedBacktester(initial_equity=INITIAL_EQUITY, cost_model=cost_model)
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


# ── Metrics ────────────────────────────────────────────────────────────────────

def extract_metrics(result, signal_df: pd.DataFrame) -> dict:
    m = result.metrics
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

def write_report(all_results: dict, output_path: Path) -> None:
    lines = [
        "# Ranging Fix Analysis",
        "",
        "**Goal**: Test two fixes for the RANGING regime signal flood identified in the bisection.",
        "",
        "**Baseline**: SignalAggregator(min_consensus=2) + structural stops(lookback=10, max_mult=4.0) "
        "+ MINIMUM_HOLD_BARS=5 + trail stop",
        "",
        "**Fix A**: RegimeRouter (RANGING → flat, MeanRev disabled) + structural stops "
        "+ min_hold=5 + trail",
        "",
        "**Fix B**: RegimeRouter (RANGING → MR with RSI<25/BB + ADX>12 gate) + structural stops "
        "+ min_hold=5 + trail",
        "",
        "**Fixed across all configs**: D1 gate DISABLED, no hysteresis, "
        "equity=10,000, risk_pct=0.5%, same CostModel, 5-fold walk-forward H4 2020–now.",
        "",
        "---",
        "",
    ]

    for name, data in all_results.items():
        lines += [
            f"## {name}",
            f"**{data['desc']}**",
            "",
            "| Metric | USD_JPY | USD_CHF |",
            "|--------|---------|---------|",
        ]
        for key, label, fmt in [
            ("sharpe",       "Sharpe",           lambda v: f"{v:.3f}"),
            ("max_dd",       "Max DD",            lambda v: f"{v:.1%}"),
            ("n_trades",     "N Trades",          lambda v: str(int(v))),
            ("hit_rate",     "Hit Rate",          lambda v: f"{v:.1%}"),
            ("avg_win_pips", "Avg Win (pips)",    lambda v: f"{v:.1f}"),
            ("avg_loss_pips","Avg Loss (pips)",   lambda v: f"{v:.1f}"),
            ("signal_freq",  "Signal Freq (%)",   lambda v: f"{v:.1f}%"),
        ]:
            jpy = data["metrics"]["USD_JPY"].get(key, float("nan"))
            chf = data["metrics"]["USD_CHF"].get(key, float("nan"))
            try:
                jpy_s = fmt(jpy)
            except (TypeError, ValueError):
                jpy_s = "N/A"
            try:
                chf_s = fmt(chf)
            except (TypeError, ValueError):
                chf_s = "N/A"
            lines.append(f"| {label} | {jpy_s} | {chf_s} |")
        lines += ["", "---", ""]

    # Summary comparison
    baseline = all_results.get("Baseline", {})
    b_jpy = baseline.get("metrics", {}).get("USD_JPY", {}).get("sharpe", float("nan"))
    b_chf = baseline.get("metrics", {}).get("USD_CHF", {}).get("sharpe", float("nan"))

    lines += [
        "## Summary",
        "",
        "| Config | JPY Sharpe | CHF Sharpe | Δ JPY | Δ CHF | JPY Sig% | CHF Sig% |",
        "|--------|-----------|-----------|-------|-------|----------|----------|",
    ]
    for name, data in all_results.items():
        jpy = data["metrics"]["USD_JPY"].get("sharpe", float("nan"))
        chf = data["metrics"]["USD_CHF"].get("sharpe", float("nan"))
        jpy_f = data["metrics"]["USD_JPY"].get("signal_freq", float("nan"))
        chf_f = data["metrics"]["USD_CHF"].get("signal_freq", float("nan"))
        try:
            djpy = f"{jpy - b_jpy:+.3f}"
            dchf = f"{chf - b_chf:+.3f}"
        except TypeError:
            djpy = dchf = "N/A"
        try:
            jpy_s = f"{jpy:.3f}"
            chf_s = f"{chf:.3f}"
            jpy_fs = f"{jpy_f:.1f}%"
            chf_fs = f"{chf_f:.1f}%"
        except (TypeError, ValueError):
            jpy_s = chf_s = jpy_fs = chf_fs = "N/A"
        lines.append(
            f"| {name} | {jpy_s} | {chf_s} | {djpy} | {dchf} | {jpy_fs} | {chf_fs} |"
        )

    lines += [
        "",
        "---",
        "",
        "_Generated by `scripts/run_ranging_fix.py`_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report written: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

CONFIGS = [
    {
        "name": "Baseline",
        "desc": "SignalAggregator(min_consensus=2) + structural stops + min_hold=5 + trail",
        "gen_fn": gen_baseline,
    },
    {
        "name": "Fix A",
        "desc": "RegimeRouter (RANGING→flat) + structural stops + min_hold=5 + trail",
        "gen_fn": gen_fix_a,
    },
    {
        "name": "Fix B",
        "desc": "RegimeRouter (RANGING→MR with RSI<25/BB + ADX>12) + structural stops + min_hold=5 + trail",
        "gen_fn": gen_fix_b,
    },
]


def main() -> None:
    logger.info("=" * 70)
    logger.info("Ranging Fix Analysis")
    logger.info("=" * 70)

    cost_model = CostModel()

    # Load data once
    feature_dfs: dict[str, pd.DataFrame] = {}
    for pair in PAIRS:
        logger.info(f"Loading {pair}...")
        h4_raw = load_raw(pair, "H4")
        d_raw  = load_raw(pair, "D")
        feature_dfs[pair] = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feature_dfs[pair])} bars")

    all_results: dict[str, dict] = {}

    for cfg in CONFIGS:
        logger.info("")
        logger.info(f"{'=' * 60}")
        logger.info(f"{cfg['name']}: {cfg['desc']}")
        logger.info(f"{'=' * 60}")

        signal_dfs: dict[str, pd.DataFrame] = {}
        for pair in PAIRS:
            signal_dfs[pair] = wf_signals(feature_dfs[pair], pair, cfg["gen_fn"])
            n_sig = int((signal_dfs[pair]["signal"] != 0).sum())
            total = len(signal_dfs[pair])
            logger.info(
                f"  {pair}: {n_sig}/{total} signals ({100*n_sig/max(total,1):.1f}%)"
            )

        pair_metrics: dict[str, dict] = {}
        for pair in PAIRS:
            result = run_backtest(pair, signal_dfs[pair], cost_model)
            pair_metrics[pair] = extract_metrics(result, signal_dfs[pair])
            m = pair_metrics[pair]
            logger.info(
                f"  {pair}: Sharpe={m['sharpe']:.3f}  "
                f"MaxDD={m['max_dd']:.1%}  "
                f"N={m['n_trades']}  "
                f"HR={m['hit_rate']:.1%}  "
                f"AvgW={m['avg_win_pips']:.1f}p  "
                f"AvgL={m['avg_loss_pips']:.1f}p  "
                f"Freq={m['signal_freq']:.1f}%"
            )

        all_results[cfg["name"]] = {
            "desc":    cfg["desc"],
            "metrics": pair_metrics,
        }

    # Write report
    output_path = PROJECT_ROOT / "results" / "ranging_fix_analysis.md"
    write_report(all_results, output_path)

    # Console summary
    print("\n" + "=" * 70)
    print("RANGING FIX SUMMARY")
    print("=" * 70)
    b_jpy = all_results.get("Baseline", {}).get("metrics", {}).get("USD_JPY", {}).get("sharpe", float("nan"))
    b_chf = all_results.get("Baseline", {}).get("metrics", {}).get("USD_CHF", {}).get("sharpe", float("nan"))
    print(f"{'Config':<12} {'JPY Sharpe':>12} {'CHF Sharpe':>12} {'Δ JPY':>10} {'Δ CHF':>10} {'JPY Freq':>10} {'CHF Freq':>10}")
    print("-" * 80)
    for name, data in all_results.items():
        jpy  = data["metrics"]["USD_JPY"].get("sharpe", float("nan"))
        chf  = data["metrics"]["USD_CHF"].get("sharpe", float("nan"))
        jf   = data["metrics"]["USD_JPY"].get("signal_freq", float("nan"))
        cf   = data["metrics"]["USD_CHF"].get("signal_freq", float("nan"))
        try:
            djpy = f"{jpy - b_jpy:+.3f}"
            dchf = f"{chf - b_chf:+.3f}"
        except TypeError:
            djpy = dchf = "N/A"
        print(f"{name:<12} {jpy:>12.3f} {chf:>12.3f} {djpy:>10} {dchf:>10} {jf:>9.1f}% {cf:>9.1f}%")
    print("=" * 70)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
