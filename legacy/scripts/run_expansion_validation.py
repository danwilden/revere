"""
Phase 1: Expansion pair validation.

Runs identical walk-forward validation on each candidate pair using EXACTLY
the same configuration as the proven system (USD_JPY Sharpe 1.289,
USD_CHF Sharpe 0.950):

  Strategy:   TrendFollowStrategy(fast=8, slow=21, adx=20)
  Gate:       D1 SMA50 full mode (every bar)
  Stops:      Structural (lookback=10, max_mult=4.0)
  Trail:      DISABLED
  Min hold:   5 bars
  Risk:       0.5% per trade
  Walk-forward: 5 folds, purge=252, embargo=10, H4

Candidate pairs tested in order:
  Round 1: USD_CAD
  Round 2: EUR_USD, GBP_USD
  Round 3: AUD_USD

Acceptance criteria per pair:
  Sharpe > 0.5
  MaxDD < 12%
  N_trades > 150
  Hit rate > 38%
  Profit factor > 1.0

Saves results to:
  results/expansion/USD_CAD_validation.md
  results/expansion/EUR_USD_validation.md
  results/expansion/GBP_USD_validation.md
  results/expansion/AUD_USD_validation.md

Run from project root:
    python scripts/run_expansion_validation.py
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

# ── Constants (FROZEN — identical to run_trendfollow_baseline.py) ──────────────
PAIRS = ["USD_CAD", "EUR_USD", "GBP_USD", "AUD_USD"]
GRAN            = "H4"
N_FOLDS         = 5
INITIAL_EQUITY  = 10_000.0
RISK_PCT        = 0.005
MIN_HOLD        = 5
PURGE_BARS      = 252
EMBARGO_BARS    = 10
WARMUP          = PURGE_BARS + EMBARGO_BARS

STRUCTURAL_LOOKBACK = 10
STRUCTURAL_MAX_MULT = 4.0

TF_FAST       = 8
TF_SLOW       = 21
TF_ADX_THRESH = 20.0

# ── Acceptance criteria ────────────────────────────────────────────────────────
ACCEPT_SHARPE         = 0.5
ACCEPT_MAX_DD         = 0.12   # 12% (stored as negative fraction from engine)
ACCEPT_MIN_TRADES     = 150
ACCEPT_HIT_RATE       = 0.38
ACCEPT_PROFIT_FACTOR  = 1.0

OUTPUT_DIR = PROJECT_ROOT / "results" / "expansion"


# ── Data / features (verbatim from run_trendfollow_baseline.py) ────────────────

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


# ── Signal generation (FROZEN — verbatim from run_trendfollow_baseline.py) ─────

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
    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
        "atr_raw":       feat_df["atr_14"],  # for reference; NOT 'atr' → trail never fires
        "units":         0,                  # placeholder; size_signal will fill
    })
    sized = size_signal(
        result.drop(columns=["units"]),
        instrument, equity, risk_pct
    )
    sized["atr_raw"] = feat_df["atr_14"].reindex(sized.index)
    return sized


# ── Walk-forward (verbatim from run_trendfollow_baseline.py) ───────────────────

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


# ── Backtest (verbatim from run_trendfollow_baseline.py) ───────────────────────

def run_backtest(instrument: str, signal_df: pd.DataFrame,
                 cost_model: CostModel) -> object:
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
    try:
        bt = VectorizedBacktester(
            initial_equity=INITIAL_EQUITY,
            cost_model=cost_model,
            trail_enabled=False,
        )
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


# ── Acceptance check ───────────────────────────────────────────────────────────

def check_criteria(m: dict) -> dict[str, bool]:
    sharpe  = m.get("sharpe", float("nan"))
    max_dd  = m.get("max_drawdown", float("nan"))  # stored as negative fraction
    n       = m.get("n_trades", 0)
    hr      = m.get("hit_rate", float("nan"))
    pf      = m.get("profit_factor", float("nan"))

    return {
        "sharpe":        (not np.isnan(sharpe)) and sharpe > ACCEPT_SHARPE,
        "max_dd":        (not np.isnan(max_dd)) and abs(max_dd) < ACCEPT_MAX_DD,
        "n_trades":      n > ACCEPT_MIN_TRADES,
        "hit_rate":      (not np.isnan(hr)) and hr > ACCEPT_HIT_RATE,
        "profit_factor": (not np.isnan(pf)) and pf > ACCEPT_PROFIT_FACTOR,
    }


# ── Report writer ──────────────────────────────────────────────────────────────

def _p(v, fmt=".3f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return format(v, fmt)


def write_pair_report(instrument: str, m: dict, signal_df: pd.DataFrame,
                      output_path: Path) -> None:
    criteria = check_criteria(m)
    all_pass = all(criteria.values())

    n_sig   = int((signal_df["signal"] != 0).sum())
    n_total = len(signal_df)
    sig_freq = 100.0 * n_sig / max(n_total, 1)

    def tick(ok: bool) -> str:
        return "✓" if ok else "✗"

    max_dd_val = m.get("max_drawdown", float("nan"))
    max_dd_str = f"{max_dd_val:.1%}" if not np.isnan(max_dd_val) else "N/A"

    hit_rate_val = m.get("hit_rate", float("nan"))
    hit_rate_str = f"{hit_rate_val:.1%}" if not np.isnan(hit_rate_val) else "N/A"

    lines = [
        f"# {instrument} Expansion Validation",
        "",
        "## Configuration (frozen — identical to proven system)",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Strategy | TrendFollowStrategy(fast_ema={TF_FAST}, slow_ema={TF_SLOW},"
        f" adx_threshold={TF_ADX_THRESH}) |",
        "| D1 gate | Full mode — SMA50, every bar |",
        f"| Stops | Structural (lookback={STRUCTURAL_LOOKBACK},"
        f" max_mult={STRUCTURAL_MAX_MULT}) |",
        "| Trail stop | DISABLED |",
        f"| Min hold bars | {MIN_HOLD} |",
        f"| Risk per trade | {RISK_PCT:.1%} |",
        f"| Walk-forward | {N_FOLDS} sequential folds, H4 2020–now |",
        f"| Purge / embargo | {PURGE_BARS} / {EMBARGO_BARS} bars |",
        "| Cost model | Default spreads + 0.5 pip slippage |",
        "",
        "## Walk-Forward Results (5-fold OOS, H4, 2020–now)",
        "",
        "| Metric | Value | Threshold | Pass? |",
        "|--------|-------|-----------|-------|",
        f"| Sharpe | {_p(m.get('sharpe'))} | > {ACCEPT_SHARPE} |"
        f" {tick(criteria['sharpe'])} |",
        f"| Max DD | {max_dd_str} | < {ACCEPT_MAX_DD:.0%} |"
        f" {tick(criteria['max_dd'])} |",
        f"| N Trades | {int(m.get('n_trades', 0))} | > {ACCEPT_MIN_TRADES} |"
        f" {tick(criteria['n_trades'])} |",
        f"| Hit Rate | {hit_rate_str} | > {ACCEPT_HIT_RATE:.0%} |"
        f" {tick(criteria['hit_rate'])} |",
        f"| Profit Factor | {_p(m.get('profit_factor'), '.2f')} | > {ACCEPT_PROFIT_FACTOR} |"
        f" {tick(criteria['profit_factor'])} |",
        "",
        "### Additional metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| CAGR | {_p(m.get('cagr'), '.1%')} |",
        f"| Payoff ratio | {_p(m.get('payoff_ratio'), '.2f')} |",
        f"| Avg win (pips) | {_p(m.get('avg_win_pips'), '.1f')} |",
        f"| Avg loss (pips) | {_p(m.get('avg_loss_pips'), '.1f')} |",
        f"| Signal frequency | {sig_freq:.1f}% |",
        "",
        "## Verdict",
        "",
    ]

    if all_pass:
        lines.append(
            f"**PASS** — All 5 criteria met. {instrument} is a candidate for"
            " portfolio inclusion."
        )
    else:
        failed = [k for k, v in criteria.items() if not v]
        lines.append(
            f"**FAIL** — Failed criteria: {', '.join(failed)}. "
            f"{instrument} does not meet the acceptance bar."
        )

    lines += [
        "",
        "---",
        "",
        "_Generated by `scripts/run_expansion_validation.py`_",
        f"_Proven system reference: USD\\_JPY Sharpe 1.289 | USD\\_CHF Sharpe 0.950_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("Phase 1: Expansion Pair Validation")
    logger.info(
        f"TF: fast={TF_FAST}, slow={TF_SLOW}, adx={TF_ADX_THRESH} | "
        f"D1 gate: full | min_hold={MIN_HOLD}"
    )
    logger.info(
        f"Structural stops: lookback={STRUCTURAL_LOOKBACK}, max_mult={STRUCTURAL_MAX_MULT}"
    )
    logger.info(f"Walk-forward: {N_FOLDS} folds | purge={PURGE_BARS} | embargo={EMBARGO_BARS}")
    logger.info(f"Pairs: {PAIRS}")
    logger.info("=" * 70)

    cost_model = CostModel()
    summary_rows: list[dict] = []

    for pair in PAIRS:
        logger.info(f"\n{'─' * 50}")
        logger.info(f"Loading {pair}...")
        h4_raw  = load_raw(pair, "H4")
        d_raw   = load_raw(pair, "D")
        feat_df = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feat_df)} H4 bars after feature build")

        logger.info(f"{pair}: running walk-forward...")
        signal_df = run_walk_forward(feat_df, pair)

        result = run_backtest(pair, signal_df, cost_model)
        m      = result.metrics

        pair_metrics = {
            "sharpe":        m.get("sharpe",        float("nan")),
            "max_drawdown":  m.get("max_drawdown",   float("nan")),
            "n_trades":      m.get("n_trades",       0),
            "hit_rate":      m.get("hit_rate",       float("nan")),
            "profit_factor": m.get("profit_factor",  float("nan")),
            "payoff_ratio":  m.get("payoff_ratio",   float("nan")),
            "avg_win_pips":  m.get("avg_win_pips",   float("nan")),
            "avg_loss_pips": m.get("avg_loss_pips",  float("nan")),
            "cagr":          m.get("cagr",           float("nan")),
        }

        criteria = check_criteria(pair_metrics)
        verdict  = "PASS" if all(criteria.values()) else "FAIL"

        logger.info(
            f"{pair}: Sharpe={pair_metrics['sharpe']:.3f} | "
            f"MaxDD={pair_metrics['max_drawdown']:.1%} | "
            f"N={pair_metrics['n_trades']} | "
            f"HR={pair_metrics['hit_rate']:.1%} | "
            f"PF={pair_metrics['profit_factor']:.2f} | "
            f"→ {verdict}"
        )

        out_path = OUTPUT_DIR / f"{pair}_validation.md"
        write_pair_report(pair, pair_metrics, signal_df, out_path)

        summary_rows.append({
            "pair":    pair,
            "metrics": pair_metrics,
            "verdict": verdict,
            "criteria": criteria,
        })

    # ── Console summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("EXPANSION VALIDATION SUMMARY")
    print("=" * 78)
    print(f"{'Pair':<10} {'Sharpe':>8} {'MaxDD':>8} {'N':>5} {'HR':>6} "
          f"{'PF':>5} {'Verdict':>8}")
    print("-" * 78)
    for row in summary_rows:
        pm = row["metrics"]
        sharpe_str  = f"{pm['sharpe']:.3f}" if not np.isnan(pm['sharpe']) else "N/A"
        maxdd_str   = f"{pm['max_drawdown']:.1%}" if not np.isnan(pm['max_drawdown']) else "N/A"
        hr_str      = f"{pm['hit_rate']:.1%}" if not np.isnan(pm['hit_rate']) else "N/A"
        pf_str      = f"{pm['profit_factor']:.2f}" if not np.isnan(pm['profit_factor']) else "N/A"
        print(
            f"{row['pair']:<10} {sharpe_str:>8} {maxdd_str:>8} "
            f"{pm['n_trades']:>5} {hr_str:>6} {pf_str:>5} {row['verdict']:>8}"
        )
    print("=" * 78)

    passers = [r["pair"] for r in summary_rows if r["verdict"] == "PASS"]
    failers = [r["pair"] for r in summary_rows if r["verdict"] == "FAIL"]
    print(f"\nPass: {passers if passers else 'none'}")
    print(f"Fail: {failers if failers else 'none'}")
    print(f"\nResults saved to: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
