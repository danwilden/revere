"""
Bisection analysis: identify which single change caused Sharpe regression
from original rule system (0.795/0.804) to v2.4 (-0.454/+0.229).

6 configurations, changing ONE thing at a time vs CONFIG 0 (original).

Run from project root:
    python scripts/run_bisection.py

Output: results/bisection_analysis.md

CONFIG 0 — Original rule system (recreate exactly):
  - SignalAggregator(min_consensus=2): need 2-of-3 strategies to agree
  - ATR × 2.0 stops (average of all 3 strategy stop distances)
  - No trail stop (was not in original; added with structural stops in v2.4)
  - No D1 gate (was not in original v1.0 — added in v2.5 as CHANGE 10)
  - No hysteresis, no stop width cap, no min hold, no corr guard
  - Strategy params (original defaults): TF(12,26,adx=20), MR, BO

CONFIG 1 — Add structural stops only (vs CONFIG 0):
  - compute_structural_stop(lookback=10, max_mult=4.0) replaces ATR×2.0

CONFIG 2 — Add stop width cap only (vs CONFIG 0):
  - ATR × 2.0 stops clipped at 2.5 × ATR(14)

CONFIG 3 — Add RegimeRouter routing only (vs CONFIG 0):
  - Regime → strategy routing replaces consensus voting
  - ATR × 2.0 stops (each strategy's own stop, NOT structural override)
  - No hysteresis, no D1 gate
  - NOTE: RegimeRouter uses TF(8,21,adx=25) internally — param change is
    bundled with the routing change and noted in the report.

CONFIG 4 — Add minimum hold only (vs CONFIG 0):
  - MINIMUM_HOLD_BARS = 5 in backtest engine
  - Same signals as CONFIG 0

CONFIG 5 — Add correlation guard only (vs CONFIG 0):
  - Reject USD_CHF signal when same direction as simultaneous USD_JPY signal
  - Same signals as CONFIG 0
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import forex_system.backtest.engine as _engine_mod
from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import VectorizedBacktester, PERIODS_PER_YEAR
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
PIP_SIZES = {"USD_JPY": 0.01, "USD_CHF": 0.0001}


# ── Data loading / feature building (mirrors validate_v2.4.py) ─────────────────

def load_raw(instrument: str, granularity: str) -> pd.DataFrame:
    path = settings.data_raw / f"{instrument}_{granularity}_2020-01-01_now.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Raw data not found: {path}\n"
            "Run notebooks/01_data_pull.ipynb first to cache raw candles."
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


# ── Signal generators ──────────────────────────────────────────────────────────

def _run_three_strategies(feat_df: pd.DataFrame, tf_fast: int = 12, tf_slow: int = 26,
                           tf_adx_thresh: float = 20.0):
    """Run all 3 strategies on feat_df OHLCV.  Returns (trend_out, mr_out, bo_out)."""
    ohlcv = feat_df[["open", "high", "low", "close"]].copy()
    trend_strat = TrendFollowStrategy(
        fast_ema=tf_fast, slow_ema=tf_slow,
        adx_window=14, adx_threshold=tf_adx_thresh
    )
    mr_strat = MeanReversionStrategy()
    bo_strat = BreakoutStrategy()
    return trend_strat.generate(ohlcv), mr_strat.generate(ohlcv), bo_strat.generate(ohlcv)


def _signal_aggregator(trend_out, mr_out, bo_out, feat_df: pd.DataFrame,
                        instrument: str, equity: float, risk_pct: float,
                        stop_cap_atr_mult: float | None = None) -> pd.DataFrame:
    """
    SignalAggregator(min_consensus=2) logic.
    vote_sum = sum of all three signals; fire long if >= 2, short if <= -2.
    stop_distance = average of all three strategy stop distances.
    No 'atr' column → no trail stop in engine.
    """
    sigs = pd.DataFrame({
        "trend": trend_out["signal"],
        "mr":    mr_out["signal"],
        "bo":    bo_out["signal"],
    })
    vote_sum = sigs.sum(axis=1)
    signal = pd.Series(0, index=sigs.index, dtype=int)
    signal[vote_sum >= 2]  = 1
    signal[vote_sum <= -2] = -1

    # Average stop from all three strategies (original SignalAggregator behaviour)
    avg_stop = pd.DataFrame({
        "trend": trend_out["stop_distance"],
        "mr":    mr_out["stop_distance"],
        "bo":    bo_out["stop_distance"],
    }).mean(axis=1)

    if stop_cap_atr_mult is not None:
        cap = stop_cap_atr_mult * feat_df["atr_14"]
        avg_stop = avg_stop.clip(upper=cap)

    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": avg_stop,
    })
    return size_signal(result, instrument, equity, risk_pct)


# ── CONFIG 0: Original — SignalAggregator, ATR×2.0 stops ──────────────────────
def gen_config0(feat_df: pd.DataFrame, instrument: str,
                equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    trend_out, mr_out, bo_out = _run_three_strategies(feat_df)
    return _signal_aggregator(trend_out, mr_out, bo_out, feat_df, instrument, equity, risk_pct)


# ── CONFIG 1: Structural stops only ───────────────────────────────────────────
def gen_config1(feat_df: pd.DataFrame, instrument: str,
                equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    trend_out, mr_out, bo_out = _run_three_strategies(feat_df)

    sigs = pd.DataFrame({
        "trend": trend_out["signal"],
        "mr":    mr_out["signal"],
        "bo":    bo_out["signal"],
    })
    vote_sum = sigs.sum(axis=1)
    signal = pd.Series(0, index=sigs.index, dtype=int)
    signal[vote_sum >= 2]  = 1
    signal[vote_sum <= -2] = -1

    # Structural stop instead of ATR×2.0; original v2.4 cap of 4.0×ATR
    stop_dist = compute_structural_stop(
        feat_df["high"], feat_df["low"], feat_df["close"],
        direction=signal,
        lookback=10,
        max_mult=4.0,
    )

    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
    })
    return size_signal(result, instrument, equity, risk_pct)


# ── CONFIG 2: Stop width cap only (2.5×ATR applied to ATR×2.0 stops) ──────────
def gen_config2(feat_df: pd.DataFrame, instrument: str,
                equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    trend_out, mr_out, bo_out = _run_three_strategies(feat_df)
    return _signal_aggregator(
        trend_out, mr_out, bo_out, feat_df, instrument, equity, risk_pct,
        stop_cap_atr_mult=2.5,
    )


# ── CONFIG 3: RegimeRouter routing + ATR×2.0 stops from each strategy ─────────
def gen_config3(feat_df: pd.DataFrame, instrument: str,
                equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    Regime-based routing (RegimeRouter logic) but each strategy's own ATR stop
    is used instead of the structural stop override.

    Strategy params match RegimeRouter internals: TF(8, 21, adx=25).
    No hysteresis. No D1 gate. No stop cap.
    No 'atr' column → no trail stop (isolating routing change only).
    """
    trend_out, mr_out, bo_out = _run_three_strategies(
        feat_df, tf_fast=8, tf_slow=21, tf_adx_thresh=25.0
    )

    adx_col       = feat_df["adx_14"]
    vol_ratio_col = feat_df["vol_ratio_10_60"]
    raw_atr       = feat_df["atr_14"]

    # Raw regime classification — no hysteresis
    regime = classify_regime(
        adx_col, vol_ratio_col, raw_atr,
        adx_trending_thresh=25.0,
        adx_ranging_thresh=20.0,
    )

    # Route signal by regime (UNDEFINED → 0)
    signal = pd.Series(0, index=feat_df.index, dtype=int)
    signal[regime == REGIME_TRENDING] = trend_out["signal"][regime == REGIME_TRENDING]
    signal[regime == REGIME_RANGING]  = mr_out["signal"][regime == REGIME_RANGING]
    signal[regime == REGIME_BREAKOUT] = bo_out["signal"][regime == REGIME_BREAKOUT]

    # Use each strategy's own ATR-based stop distance (not structural)
    stop_distance = pd.Series(0.0, index=feat_df.index)
    stop_distance[regime == REGIME_TRENDING] = (
        trend_out["stop_distance"][regime == REGIME_TRENDING]
    )
    stop_distance[regime == REGIME_RANGING] = (
        mr_out["stop_distance"][regime == REGIME_RANGING]
    )
    stop_distance[regime == REGIME_BREAKOUT] = (
        bo_out["stop_distance"][regime == REGIME_BREAKOUT]
    )

    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_distance,
    })
    n_sig = int((signal != 0).sum())
    freq  = 100.0 * n_sig / max(len(feat_df), 1)
    logger.info(
        f"CONFIG 3 | {instrument} | signals={n_sig}/{len(feat_df)} ({freq:.1f}%) | "
        f"trend={int((regime==REGIME_TRENDING).sum())} "
        f"ranging={int((regime==REGIME_RANGING).sum())} "
        f"breakout={int((regime==REGIME_BREAKOUT).sum())} "
        f"undefined={int((~regime.isin([REGIME_TRENDING,REGIME_RANGING,REGIME_BREAKOUT])).sum())}"
    )
    return size_signal(result, instrument, equity, risk_pct)


# CONFIG 4 & 5 reuse CONFIG 0 signals; differences handled at backtest level
def gen_config4(feat_df: pd.DataFrame, instrument: str,
                equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    return gen_config0(feat_df, instrument, equity, risk_pct)


def gen_config5(feat_df: pd.DataFrame, instrument: str,
                equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    return gen_config0(feat_df, instrument, equity, risk_pct)


# ── Walk-forward runner (5 folds, rule-based so no training) ─────────────────

def wf_signals(feat_df: pd.DataFrame, instrument: str, signal_fn, n_folds: int = N_FOLDS,
               equity: float = INITIAL_EQUITY, risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """Generate signals across 5 sequential folds; concatenate for full OOS curve."""
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

        sig_df = signal_fn(fold_df, instrument, equity, risk_pct)
        n_sig  = int((sig_df["signal"] != 0).sum())
        logger.debug(
            f"  Fold {fold_idx}/{n_folds-1} | {instrument} | "
            f"bars={len(fold_df)} | signals={n_sig} ({100*n_sig/max(len(fold_df),1):.1f}%)"
        )
        all_dfs.append(sig_df)

    if not all_dfs:
        raise RuntimeError(f"No valid folds generated for {instrument}")
    return pd.concat(all_dfs)


# ── Backtest runner with configurable min_hold ─────────────────────────────────

def run_backtest(
    instrument: str,
    signal_df: pd.DataFrame,
    cost_model: CostModel,
    min_hold: int = 0,
) -> object:
    """Run VectorizedBacktester, temporarily patching MINIMUM_HOLD_BARS."""
    orig_hold = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = min_hold
    try:
        bt = VectorizedBacktester(initial_equity=INITIAL_EQUITY, cost_model=cost_model)
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig_hold


# ── Correlation guard (same logic as validate_v2.4.py) ────────────────────────

def apply_corr_guard(
    signal_jpy: pd.DataFrame,
    signal_chf: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    shared_idx     = signal_jpy.index.intersection(signal_chf.index)
    jpy_sig        = signal_jpy["signal"].reindex(shared_idx)
    chf_sig        = signal_chf["signal"].reindex(shared_idx)
    both_active    = (jpy_sig != 0) & (chf_sig != 0)
    same_direction = jpy_sig == chf_sig
    rejected       = both_active & same_direction
    n_rej = int(rejected.sum())
    logger.info(f"Corr guard: rejected {n_rej} USD_CHF signals (same-dir as USD_JPY)")
    chf_out = signal_chf.copy()
    chf_out.loc[rejected, "signal"] = 0
    chf_out.loc[rejected, "units"]  = 0
    return signal_jpy.copy(), chf_out


# ── Metrics extraction ─────────────────────────────────────────────────────────

def extract_metrics(result) -> dict:
    m = result.metrics
    return {
        "sharpe":        m.get("sharpe",        float("nan")),
        "max_dd":        m.get("max_drawdown",   float("nan")),
        "n_trades":      m.get("n_trades",       0),
        "hit_rate":      m.get("hit_rate",       float("nan")),
        "avg_win_pips":  m.get("avg_win_pips",   float("nan")),
        "avg_loss_pips": m.get("avg_loss_pips",  float("nan")),
    }


# ── Config table ──────────────────────────────────────────────────────────────

CONFIGS = [
    {
        "name":       "CONFIG 0",
        "desc":       "Original: SignalAggregator(min_consensus=2) + ATR×2.0 stops",
        "details":    [
            "TF(12,26,adx=20), MR, BO — all default params",
            "Stop = mean(TF_stop, MR_stop, BO_stop); no cap",
            "No trail stop | No D1 gate | No corr guard | min_hold=0",
        ],
        "gen_fn":     gen_config0,
        "min_hold":   0,
        "corr_guard": False,
    },
    {
        "name":       "CONFIG 1",
        "desc":       "Add structural stops only (max_mult=4.0)",
        "details":    [
            "Same SignalAggregator routing as CONFIG 0",
            "Stop = compute_structural_stop(lookback=10, max_mult=4.0)",
            "No trail stop | No D1 gate | No corr guard | min_hold=0",
        ],
        "gen_fn":     gen_config1,
        "min_hold":   0,
        "corr_guard": False,
    },
    {
        "name":       "CONFIG 2",
        "desc":       "Add stop width cap only (2.5×ATR cap on ATR×2.0 stops)",
        "details":    [
            "Same SignalAggregator routing as CONFIG 0",
            "ATR×2.0 stops clipped at 2.5×ATR(14)",
            "No trail stop | No D1 gate | No corr guard | min_hold=0",
        ],
        "gen_fn":     gen_config2,
        "min_hold":   0,
        "corr_guard": False,
    },
    {
        "name":       "CONFIG 3",
        "desc":       "Add RegimeRouter routing only (ATR×2.0 stops, no hysteresis)",
        "details":    [
            "Regime routing: TRENDING→TF, RANGING→MR, BREAKOUT→BO, UNDEFINED→flat",
            "Each strategy's own ATR stop (NOT structural override)",
            "TF params changed to (8,21,adx=25) — bundled with routing change",
            "No hysteresis | No D1 gate | No stop cap | No corr guard | min_hold=0",
        ],
        "gen_fn":     gen_config3,
        "min_hold":   0,
        "corr_guard": False,
    },
    {
        "name":       "CONFIG 4",
        "desc":       "Add MINIMUM_HOLD_BARS=5 only",
        "details":    [
            "Same SignalAggregator routing and ATR×2.0 stops as CONFIG 0",
            "Signal exits blocked until 5 bars held (stop hits unaffected)",
            "No trail stop | No D1 gate | No corr guard",
        ],
        "gen_fn":     gen_config4,
        "min_hold":   5,
        "corr_guard": False,
    },
    {
        "name":       "CONFIG 5",
        "desc":       "Add correlation guard only",
        "details":    [
            "Same SignalAggregator routing and ATR×2.0 stops as CONFIG 0",
            "Reject USD_CHF signals that are same-dir as simultaneous USD_JPY signal",
            "No trail stop | No D1 gate | min_hold=0",
        ],
        "gen_fn":     gen_config5,
        "min_hold":   0,
        "corr_guard": True,
    },
]


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(all_results: dict, output_path: Path) -> None:
    lines = [
        "# Bisection Analysis: Sharpe Regression — Original → v2.4",
        "",
        "**Goal**: Identify which single change caused Sharpe to drop from "
        "~0.8 (original rule system) to -0.454/+0.229 (v2.4).",
        "",
        "**Method**: Start from CONFIG 0 (original SignalAggregator), add ONE change "
        "at a time, run 5-fold walk-forward backtest on H4 USD_JPY + USD_CHF (2020–now).",
        "",
        "**Fixed across all configs**: "
        "trail stop DISABLED (was added in v2.4), "
        "D1 gate DISABLED (was added in v2.5), "
        "equity=10,000, risk_pct=0.5%, same CostModel.",
        "",
        "---",
        "",
    ]

    # Per-config sections
    for cfg_name, data in all_results.items():
        lines += [
            f"## {cfg_name}",
            f"**{data['desc']}**",
            "",
        ]
        for detail in data.get("details", []):
            lines.append(f"- {detail}")
        lines += [""]

        lines += [
            "| Metric | USD_JPY | USD_CHF |",
            "|--------|---------|---------|",
        ]

        def fmt_val(key, val):
            try:
                if key == "max_dd":
                    return f"{val:.1%}"
                elif key in ("sharpe", "hit_rate"):
                    return f"{val:.3f}"
                elif key == "n_trades":
                    return str(int(val))
                else:
                    return f"{val:.1f}"
            except (ValueError, TypeError):
                return "N/A"

        row_defs = [
            ("sharpe",        "Sharpe"),
            ("max_dd",        "Max DD"),
            ("n_trades",      "N Trades"),
            ("hit_rate",      "Hit Rate"),
            ("avg_win_pips",  "Avg Win (pips)"),
            ("avg_loss_pips", "Avg Loss (pips)"),
        ]
        for key, label in row_defs:
            jpy = data["metrics"]["USD_JPY"].get(key, float("nan"))
            chf = data["metrics"]["USD_CHF"].get(key, float("nan"))
            lines.append(f"| {label} | {fmt_val(key, jpy)} | {fmt_val(key, chf)} |")

        lines += ["", "---", ""]

    # ── Summary table: Sharpe delta vs CONFIG 0 ────────────────────────────────
    c0 = all_results.get("CONFIG 0", {})
    c0_jpy = c0.get("metrics", {}).get("USD_JPY", {}).get("sharpe", float("nan"))
    c0_chf = c0.get("metrics", {}).get("USD_CHF", {}).get("sharpe", float("nan"))

    lines += [
        "## Summary: Sharpe by Config",
        "",
        "| Config | USD_JPY Sharpe | USD_CHF Sharpe | Δ JPY vs C0 | Δ CHF vs C0 | Verdict |",
        "|--------|----------------|----------------|-------------|-------------|---------|",
    ]
    for cfg_name, data in all_results.items():
        jpy = data["metrics"]["USD_JPY"].get("sharpe", float("nan"))
        chf = data["metrics"]["USD_CHF"].get("sharpe", float("nan"))
        try:
            djpy = jpy - c0_jpy
            dchf = chf - c0_chf
            djpy_str = f"{djpy:+.3f}"
            dchf_str = f"{dchf:+.3f}"
            verdict  = "REGRESS" if (djpy < -0.10 or dchf < -0.10) else "OK"
        except (TypeError, ValueError):
            djpy_str = dchf_str = "N/A"
            verdict  = "?"
        try:
            jpy_str = f"{jpy:.3f}"
            chf_str = f"{chf:.3f}"
        except (TypeError, ValueError):
            jpy_str = chf_str = "N/A"
        lines.append(
            f"| {cfg_name} | {jpy_str} | {chf_str} | {djpy_str} | {dchf_str} | {verdict} |"
        )

    lines += [
        "",
        "---",
        "",
        "_Generated by `scripts/run_bisection.py`_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report written: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("Bisection Analysis: Sharpe Regression Original → v2.4")
    logger.info("=" * 70)

    cost_model = CostModel()

    # 1. Load and build features once for both pairs
    feature_dfs: dict[str, pd.DataFrame] = {}
    for pair in PAIRS:
        logger.info(f"Loading {pair} H4 + D raw data...")
        h4_raw = load_raw(pair, "H4")
        d_raw  = load_raw(pair, "D")
        logger.info(f"Building features for {pair}...")
        feature_dfs[pair] = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feature_dfs[pair])} bars after NaN drop")

    # 2. Run each config
    all_results: dict[str, dict] = {}

    for cfg in CONFIGS:
        logger.info("")
        logger.info(f"{'=' * 60}")
        logger.info(f"{cfg['name']}: {cfg['desc']}")
        logger.info(f"{'=' * 60}")

        # Generate signals (walk-forward, 5 folds)
        signal_dfs: dict[str, pd.DataFrame] = {}
        for pair in PAIRS:
            signal_dfs[pair] = wf_signals(
                feature_dfs[pair], pair, cfg["gen_fn"]
            )
            n_sig = int((signal_dfs[pair]["signal"] != 0).sum())
            total = len(signal_dfs[pair])
            logger.info(
                f"  {pair} total signals: {n_sig}/{total} "
                f"({100*n_sig/max(total,1):.1f}%)"
            )

        # Apply corr guard if needed (CONFIG 5)
        if cfg["corr_guard"]:
            signal_dfs["USD_JPY"], signal_dfs["USD_CHF"] = apply_corr_guard(
                signal_dfs["USD_JPY"], signal_dfs["USD_CHF"]
            )

        # Run backtest per pair
        pair_metrics: dict[str, dict] = {}
        for pair in PAIRS:
            result = run_backtest(
                pair, signal_dfs[pair], cost_model, min_hold=cfg["min_hold"]
            )
            pair_metrics[pair] = extract_metrics(result)
            m = pair_metrics[pair]
            logger.info(
                f"  {pair}: Sharpe={m['sharpe']:.3f}  "
                f"MaxDD={m['max_dd']:.1%}  "
                f"N={m['n_trades']}  "
                f"HR={m['hit_rate']:.1%}  "
                f"AvgW={m['avg_win_pips']:.1f}p  "
                f"AvgL={m['avg_loss_pips']:.1f}p"
            )

        all_results[cfg["name"]] = {
            "desc":    cfg["desc"],
            "details": cfg["details"],
            "metrics": pair_metrics,
        }

    # 3. Write report
    output_path = PROJECT_ROOT / "results" / "bisection_analysis.md"
    write_report(all_results, output_path)

    # 4. Print summary
    print("\n" + "=" * 70)
    print("BISECTION SUMMARY")
    print("=" * 70)
    c0 = all_results.get("CONFIG 0", {})
    c0_jpy = c0.get("metrics", {}).get("USD_JPY", {}).get("sharpe", float("nan"))
    c0_chf = c0.get("metrics", {}).get("USD_CHF", {}).get("sharpe", float("nan"))
    print(f"{'Config':<12} {'JPY Sharpe':>12} {'CHF Sharpe':>12} {'Δ JPY':>10} {'Δ CHF':>10}")
    print("-" * 60)
    for cfg_name, data in all_results.items():
        jpy = data["metrics"]["USD_JPY"].get("sharpe", float("nan"))
        chf = data["metrics"]["USD_CHF"].get("sharpe", float("nan"))
        try:
            djpy = f"{jpy - c0_jpy:+.3f}"
            dchf = f"{chf - c0_chf:+.3f}"
        except TypeError:
            djpy = dchf = "N/A"
        print(f"{cfg_name:<12} {jpy:>12.3f} {chf:>12.3f} {djpy:>10} {dchf:>10}")
    print("=" * 70)
    print(f"\nFull report: {output_path}")


if __name__ == "__main__":
    main()
