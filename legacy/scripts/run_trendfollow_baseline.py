"""
STEP 0 + STEP 1: TrendFollowStrategy + D1 gate (full) — clean baseline + loss diagnosis.

STEP 0 (baseline):
    - TrendFollowStrategy(fast=8, slow=21, adx=20) alone
    - D1 gate FULL mode
    - Structural stops (lookback=10, max_mult=4.0)
    - Trail stop DISABLED (clean reference for exit testing)
    - MINIMUM_HOLD_BARS=5
    - 5-fold walk-forward, H4, 2020–now, USD_JPY + USD_CHF
    → results/tf_baseline.md

STEP 1 (loss diagnosis):
    Using trades from STEP 0, reports:
    A) Loss distribution by exit_reason (stop_hit vs signal_exit)
    B) Loss / win distribution by bars_held buckets (5-10, 11-20, 21+)
    C) Win distribution by bars_held
    D) Stop distance analysis (placed vs actual loss)
    → results/tf_loss_diagnosis.md

Run from project root:
    python scripts/run_trendfollow_baseline.py
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
PURGE_BARS       = 252
EMBARGO_BARS     = 10
WARMUP           = PURGE_BARS + EMBARGO_BARS

STRUCTURAL_LOOKBACK = 10
STRUCTURAL_MAX_MULT = 4.0

TF_FAST       = 8
TF_SLOW       = 21
TF_ADX_THRESH = 20.0


# ── Data / features ────────────────────────────────────────────────────────────

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


# ── Signal generation (FROZEN — do not modify) ─────────────────────────────────

def gen_tf_signals(feat_df: pd.DataFrame, instrument: str,
                   equity: float = INITIAL_EQUITY,
                   risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    TrendFollowStrategy alone with D1 gate (full) and structural stops.
    No 'atr' column in output → trail stop will not activate in engine.
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
    block_long  = gate_valid & (gate < 0) & (signal == 1)
    block_short = gate_valid & (gate > 0) & (signal == -1)
    block_nan   = gate.isna()
    signal[block_long]  = 0
    signal[block_short] = 0
    signal[block_nan]   = 0

    logger.debug(
        f"TF+D1 | {instrument} | "
        f"before_gate={int((strat_out['signal'] != 0).sum())} → "
        f"after_gate={int((signal != 0).sum())} "
        f"(blocked={int((block_long | block_short | block_nan).sum())})"
    )

    stop_dist = compute_structural_stop(
        feat_df["high"], feat_df["low"], feat_df["close"],
        direction=signal,
        lookback=STRUCTURAL_LOOKBACK,
        max_mult=STRUCTURAL_MAX_MULT,
    )

    # NOTE: no 'atr' column → trail_enabled is irrelevant (has_atr_col=False)
    # We pass stop_distance only; the engine needs it for pip diagnostics.
    # We do include atr separately for diagnosis purposes in the result but
    # do NOT name it 'atr' so the engine's trail logic never fires.
    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
        "atr_raw":       feat_df["atr_14"],  # for diagnosis; NOT 'atr' → trail never fires
        "units":         0,                  # placeholder; size_signal will fill
    })
    # size_signal needs stop_distance + instrument meta; returns with 'units' filled
    sized = size_signal(
        result.drop(columns=["units"]),
        instrument, equity, risk_pct
    )
    # Preserve atr_raw through size_signal (it drops unknown cols, so re-attach)
    sized["atr_raw"] = feat_df["atr_14"].reindex(sized.index)
    return sized


# ── Walk-forward ───────────────────────────────────────────────────────────────

def run_walk_forward(feat_df: pd.DataFrame, instrument: str) -> pd.DataFrame:
    n = len(feat_df)
    fold_size = n // N_FOLDS
    eval_dfs  = []

    for fold_idx in range(N_FOLDS):
        start_idx = fold_idx * fold_size
        end_idx   = (fold_idx + 1) * fold_size if fold_idx < N_FOLDS - 1 else n
        fold_df   = feat_df.iloc[start_idx:end_idx]

        if len(fold_df) <= WARMUP + 50:
            logger.warning(f"Fold {fold_idx}: {len(fold_df)} bars — skipping.")
            continue

        full_sig_df = gen_tf_signals(fold_df, instrument)
        eval_sig_df = full_sig_df.iloc[WARMUP:]

        logger.info(
            f"  Fold {fold_idx} | {instrument} | "
            f"total={len(fold_df)} | eval={len(eval_sig_df)} | "
            f"signals={int((eval_sig_df['signal'] != 0).sum())} "
            f"({100*int((eval_sig_df['signal'] != 0).sum())/max(len(eval_sig_df),1):.1f}%)"
        )
        eval_dfs.append(eval_sig_df)

    if not eval_dfs:
        raise RuntimeError(f"No valid fold data for {instrument}")
    return pd.concat(eval_dfs)


# ── Backtest ───────────────────────────────────────────────────────────────────

def run_backtest(instrument: str, signal_df: pd.DataFrame,
                 cost_model: CostModel) -> object:
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
    try:
        bt = VectorizedBacktester(
            initial_equity=INITIAL_EQUITY,
            cost_model=cost_model,
            trail_enabled=False,  # STEP 0: no trail — clean baseline
        )
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


# ── Loss diagnosis (STEP 1) ────────────────────────────────────────────────────

def _bucket_label(bars: int) -> str:
    if bars <= 10:
        return "5–10"
    elif bars <= 20:
        return "11–20"
    else:
        return "21+"


def compute_diagnosis(trades_df: pd.DataFrame, signal_df: pd.DataFrame,
                      instrument: str, pip_size: float) -> dict:
    """
    Returns a dict with sub-dicts for sections A, B, C, D.
    """
    if trades_df.empty:
        return {}

    # Exclude end_of_data from analysis (incomplete trades)
    td = trades_df[trades_df["exit_reason"] != "end_of_data"].copy()
    if td.empty:
        return {}

    losers = td[td["pnl_pips"] < 0]
    winners = td[td["pnl_pips"] >= 0]

    # ── A: by exit_reason ────────────────────────────────────────────────────
    section_a = {}
    total_losses = len(losers)
    for reason in ["stop_hit", "signal_exit"]:
        grp = losers[losers["exit_reason"] == reason]
        section_a[reason] = {
            "count":          len(grp),
            "avg_loss_pips":  float(grp["pnl_pips"].mean()) if len(grp) else float("nan"),
            "pct_of_losses":  100.0 * len(grp) / max(total_losses, 1),
        }

    # ── B: losing trades by bars_held bucket ─────────────────────────────────
    section_b = {bkt: {"count": 0, "avg_pnl_pips": float("nan")}
                 for bkt in ["5–10", "11–20", "21+"]}
    if "bars_held" in losers.columns:
        for _, row in losers.iterrows():
            bkt = _bucket_label(int(row["bars_held"]))
            d   = section_b[bkt]
            d["count"] = d.get("count", 0) + 1
            d.setdefault("_pips", []).append(float(row["pnl_pips"]))
        for bkt in section_b:
            if "_pips" in section_b[bkt]:
                section_b[bkt]["avg_pnl_pips"] = float(np.mean(section_b[bkt].pop("_pips")))

    # ── C: winning trades by bars_held bucket ────────────────────────────────
    section_c = {bkt: {"count": 0, "avg_pnl_pips": float("nan")}
                 for bkt in ["5–10", "11–20", "21+"]}
    if "bars_held" in winners.columns:
        for _, row in winners.iterrows():
            bkt = _bucket_label(int(row["bars_held"]))
            d   = section_c[bkt]
            d["count"] = d.get("count", 0) + 1
            d.setdefault("_pips", []).append(float(row["pnl_pips"]))
        for bkt in section_c:
            if "_pips" in section_c[bkt]:
                section_c[bkt]["avg_pnl_pips"] = float(np.mean(section_c[bkt].pop("_pips")))

    # ── D: stop distance vs actual loss ──────────────────────────────────────
    # avg stop_distance at signal-change bars (entry signals)
    sig_changes = signal_df[signal_df["signal"] != 0].copy()
    # Detect entries: first bar of each run (signal differs from previous)
    prev_sig = sig_changes["signal"].shift(1).fillna(0).astype(int)
    entries  = sig_changes[sig_changes["signal"] != prev_sig]
    avg_stop_pips = float(
        (entries["stop_distance"] / pip_size).mean()
    ) if len(entries) else float("nan")

    stop_hit_losers = losers[losers["exit_reason"] == "stop_hit"]
    avg_actual_loss_pips = float(
        stop_hit_losers["pnl_pips"].abs().mean()
    ) if len(stop_hit_losers) else float("nan")

    ratio = avg_actual_loss_pips / avg_stop_pips if avg_stop_pips > 0 else float("nan")

    section_d = {
        "avg_stop_distance_pips":  avg_stop_pips,
        "avg_actual_loss_pips":    avg_actual_loss_pips,
        "actual_vs_placed_ratio":  ratio,
        "n_entry_bars":            len(entries),
        "n_stop_hit_losers":       len(stop_hit_losers),
    }

    return {"A": section_a, "B": section_b, "C": section_c, "D": section_d}


# ── Report writers ─────────────────────────────────────────────────────────────

def _fmt(v, d=3):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return f"{v:.{d}f}"


def write_baseline_report(results: dict[str, dict], output_path: Path) -> None:
    lines = [
        "# TrendFollow Baseline (No Trail)",
        "",
        f"**Strategy**: `TrendFollowStrategy(fast_ema={TF_FAST}, slow_ema={TF_SLOW},"
        f" adx_threshold={TF_ADX_THRESH})` alone",
        "  — no SignalAggregator, no RegimeRouter, no MeanReversion, no Breakout",
        "",
        "**Stops**: Structural stops (lookback=10, max_mult=4.0)",
        "",
        "**D1 gate**: FULL — long blocked when SMA50 bearish; short blocked when SMA50 bullish",
        "",
        "**Trail stop**: DISABLED (clean exit baseline)",
        "",
        f"**MINIMUM_HOLD_BARS**: {MIN_HOLD}",
        "",
        f"**Walk-forward**: {N_FOLDS} sequential folds, H4 2020–now",
        f"  — each fold skips first {PURGE_BARS} (purge) + {EMBARGO_BARS} (embargo)"
        f" = {WARMUP} bars",
        "",
        "**Reference**: trend_only_analysis.md (trail=ENABLED): JPY 0.816 / CHF 0.542",
        "",
        "---",
        "",
        "## Results",
        "",
        "| Metric | USD_JPY | USD_CHF |",
        "|--------|---------|---------|",
    ]

    rows = [
        ("sharpe",        "Sharpe",          lambda v: f"{v:.3f}"),
        ("max_dd",        "Max DD",           lambda v: f"{v:.1%}"),
        ("n_trades",      "N Trades",         lambda v: str(int(v))),
        ("hit_rate",      "Hit Rate",         lambda v: f"{v:.1%}"),
        ("avg_win_pips",  "Avg Win (pips)",   lambda v: f"{v:.1f}"),
        ("avg_loss_pips", "Avg Loss (pips)",  lambda v: f"{v:.1f}"),
        ("payoff_ratio",  "Payoff Ratio",     lambda v: f"{v:.2f}"),
        ("signal_freq",   "Signal Freq (%)",  lambda v: f"{v:.1f}%"),
    ]
    for key, label, fmt in rows:
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
        "_Generated by `scripts/run_trendfollow_baseline.py`_",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Baseline report: {output_path}")


def write_diagnosis_report(
    diagnoses: dict[str, dict],
    baseline_results: dict[str, dict],
    output_path: Path,
) -> None:
    def fv(v, d=1):
        return "N/A" if (v is None or (isinstance(v, float) and np.isnan(v))) else f"{v:.{d}f}"

    lines = [
        "# Loss Diagnosis (STEP 1)",
        "",
        "Diagnosis is based on the no-trail baseline (see `tf_baseline.md`).",
        "Trades with `exit_reason='end_of_data'` are excluded (incomplete).",
        "",
    ]

    for pair in PAIRS:
        d = diagnoses.get(pair, {})
        if not d:
            lines.append(f"## {pair} — no diagnosis data\n")
            continue

        m = baseline_results.get(pair, {})
        lines += [
            f"## {pair}",
            "",
            f"N trades: {int(m.get('n_trades', 0))}  |  "
            f"Hit rate: {m.get('hit_rate', float('nan')):.1%}  |  "
            f"Avg win: {fv(m.get('avg_win_pips'))} pips  |  "
            f"Avg loss: {fv(m.get('avg_loss_pips'))} pips",
            "",
        ]

        # ── Section A ────────────────────────────────────────────────────────
        lines += [
            "### A — Loss distribution by exit type",
            "",
            "| Exit type | Count | Avg loss (pips) | % of all losses |",
            "|-----------|-------|-----------------|-----------------|",
        ]
        for reason, da in d.get("A", {}).items():
            lines.append(
                f"| {reason} | {da['count']} | "
                f"{fv(da['avg_loss_pips'])} | "
                f"{da['pct_of_losses']:.1f}% |"
            )
        lines.append("")

        # ── Section B ────────────────────────────────────────────────────────
        lines += [
            "### B — Losing trades by bars_held",
            "",
            "| Bars held | Count | Avg PnL (pips) |",
            "|-----------|-------|----------------|",
        ]
        for bkt in ["5–10", "11–20", "21+"]:
            db = d.get("B", {}).get(bkt, {})
            lines.append(
                f"| {bkt} | {db.get('count', 0)} | {fv(db.get('avg_pnl_pips'))} |"
            )
        lines.append("")

        # ── Section C ────────────────────────────────────────────────────────
        lines += [
            "### C — Winning trades by bars_held",
            "",
            "| Bars held | Count | Avg PnL (pips) |",
            "|-----------|-------|----------------|",
        ]
        for bkt in ["5–10", "11–20", "21+"]:
            dc = d.get("C", {}).get(bkt, {})
            lines.append(
                f"| {bkt} | {dc.get('count', 0)} | {fv(dc.get('avg_pnl_pips'))} |"
            )
        lines.append("")

        # ── Section D ────────────────────────────────────────────────────────
        dd = d.get("D", {})
        lines += [
            "### D — Stop distance vs actual loss",
            "",
            f"- Avg structural stop distance at entry: **{fv(dd.get('avg_stop_distance_pips'))} pips**"
            f"  (n={dd.get('n_entry_bars', 'N/A')} entry bars)",
            f"- Avg actual loss on stop_hit trades: **{fv(dd.get('avg_actual_loss_pips'))} pips**"
            f"  (n={dd.get('n_stop_hit_losers', 'N/A')} trades)",
            f"- Actual / placed ratio: **{fv(dd.get('actual_vs_placed_ratio'), d=2)}**"
            "  (1.0 = fills exactly at stop; >1.0 = slippage beyond stop)",
            "",
        ]

        # Interpretation hint
        sec_a    = d.get("A", {})
        sh_pct   = sec_a.get("stop_hit", {}).get("pct_of_losses", 50.0)
        se_pct   = sec_a.get("signal_exit", {}).get("pct_of_losses", 50.0)
        b_20plus = d.get("B", {}).get("21+", {}).get("count", 0)
        b_short  = (d.get("B", {}).get("5–10", {}).get("count", 0) +
                    d.get("B", {}).get("11–20", {}).get("count", 0))

        hint = []
        if sh_pct > 60:
            hint.append("Losses dominated by stop_hit → structural stop may be too wide or "
                         "entries in choppy markets; time stop could help.")
        elif se_pct > 60:
            hint.append("Losses dominated by signal_exit → signal flips before SL fires; "
                         "minimum hold and/or trail stop may help.")
        if b_20plus > b_short:
            hint.append("Long-duration trades contribute more losses → time stop likely useful.")
        else:
            hint.append("Short-duration trades dominate losses → whipsaw pattern; "
                         "trail activation at smaller profit threshold may capture more gains.")

        lines += ["**Interpretation notes:**", ""] + [f"- {h}" for h in hint] + ["", "---", ""]

    lines.append("_Generated by `scripts/run_trendfollow_baseline.py`_")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Diagnosis report: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("STEP 0: TrendFollow Baseline (trail=DISABLED)")
    logger.info(
        f"TF: fast={TF_FAST}, slow={TF_SLOW}, adx={TF_ADX_THRESH} | "
        f"D1 gate: full | min_hold={MIN_HOLD}"
    )
    logger.info(
        f"Structural stops: lookback={STRUCTURAL_LOOKBACK}, max_mult={STRUCTURAL_MAX_MULT}"
    )
    logger.info(f"Walk-forward: {N_FOLDS} folds | purge={PURGE_BARS} | embargo={EMBARGO_BARS}")
    logger.info("=" * 70)

    cost_model  = CostModel()
    pair_results: dict[str, dict]    = {}
    pair_diagnoses: dict[str, dict]  = {}

    for pair in PAIRS:
        logger.info(f"\nLoading {pair}...")
        h4_raw  = load_raw(pair, "H4")
        d_raw   = load_raw(pair, "D")
        feat_df = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feat_df)} H4 bars after feature build")

        logger.info(f"{pair}: running walk-forward...")
        signal_df = run_walk_forward(feat_df, pair)

        result  = run_backtest(pair, signal_df, cost_model)
        m       = result.metrics
        n_sig   = int((signal_df["signal"] != 0).sum())
        n_total = len(signal_df)

        pair_results[pair] = {
            "sharpe":        m.get("sharpe",        float("nan")),
            "max_dd":        m.get("max_drawdown",   float("nan")),
            "n_trades":      m.get("n_trades",       0),
            "hit_rate":      m.get("hit_rate",       float("nan")),
            "avg_win_pips":  m.get("avg_win_pips",   float("nan")),
            "avg_loss_pips": m.get("avg_loss_pips",  float("nan")),
            "payoff_ratio":  m.get("payoff_ratio",   float("nan")),
            "signal_freq":   100.0 * n_sig / max(n_total, 1),
        }

        logger.info(
            f"{pair}: Sharpe={m.get('sharpe', float('nan')):.3f} | "
            f"MaxDD={m.get('max_drawdown', float('nan')):.1%} | "
            f"N={m.get('n_trades', 0)} | "
            f"HR={m.get('hit_rate', float('nan')):.1%} | "
            f"AvgW={m.get('avg_win_pips', float('nan')):.1f}p | "
            f"AvgL={m.get('avg_loss_pips', float('nan')):.1f}p"
        )

        # STEP 1: diagnosis
        logger.info(f"{pair}: computing STEP 1 loss diagnosis...")
        from forex_system.data.instruments import registry
        pip_size = registry.get(pair).pip_size
        trades_df = result.trades_df()
        diagnosis = compute_diagnosis(trades_df, signal_df, pair, pip_size)
        pair_diagnoses[pair] = diagnosis

    # Write reports
    baseline_path  = PROJECT_ROOT / "results" / "tf_baseline.md"
    diagnosis_path = PROJECT_ROOT / "results" / "tf_loss_diagnosis.md"
    write_baseline_report(pair_results, baseline_path)
    write_diagnosis_report(pair_diagnoses, pair_results, diagnosis_path)

    # Console summary
    print("\n" + "=" * 70)
    print("STEP 0 — BASELINE RESULTS (trail=DISABLED)")
    print("=" * 70)
    print(f"{'Pair':<10} {'Sharpe':>8} {'MaxDD':>8} {'N':>6} {'HR':>6} "
          f"{'AvgW':>7} {'AvgL':>7} {'PR':>6}")
    print("-" * 70)
    for pair in PAIRS:
        m = pair_results[pair]
        print(
            f"{pair:<10} {m['sharpe']:>8.3f} {m['max_dd']:>8.1%} "
            f"{m['n_trades']:>6} {m['hit_rate']:>6.1%} "
            f"{m['avg_win_pips']:>7.1f} {m['avg_loss_pips']:>7.1f} "
            f"{m.get('payoff_ratio', float('nan')):>6.2f}"
        )
    print("=" * 70)
    print(f"\nBaseline report:  {baseline_path}")
    print(f"Diagnosis report: {diagnosis_path}")
    print("\nReview tf_loss_diagnosis.md before running run_tf_exit_tests.py")


if __name__ == "__main__":
    main()
