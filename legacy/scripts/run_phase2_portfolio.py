"""
Phase 2: Portfolio construction validation.

Runs 4 staged portfolio configurations in sequence, showing the marginal effect
of adding each passing pair to the core (USD_JPY + USD_CHF) system.

Phase 1 results:
    PASS: USD_CAD (Sharpe 0.876), GBP_USD (0.837), AUD_USD (1.414)
    FAIL: EUR_USD (Sharpe 0.415) — excluded from portfolio

Stage sequence:
    Stage 1: Core (USD_JPY + USD_CHF)          — baseline
    Stage 2: Core + USD_CAD                    — +same USD-direction cluster
    Stage 3: Core + USD_CAD + GBP_USD          — +opposite USD-direction
    Stage 4: Full (+ AUD_USD)                  — +highest Sharpe pair

Frozen config (identical to Phase 1 expansion validation):
    Strategy:       TrendFollowStrategy(fast=8, slow=21, adx=20)
    Gate:           D1 SMA50 full mode (every bar)
    Stops:          Structural (lookback=10, max_mult=4.0)
    Trail:          DISABLED
    Min hold:       5 bars
    Risk:           0.5% per trade (throttled to 0.25% on 5%+ portfolio DD)
    Walk-forward:   5 folds, purge=252, embargo=10, H4

Portfolio rules enforced at entry:
    Rule 1: USD_CLUSTER_CAP     — max 2 simultaneous same-direction USD positions
    Rule 2: EURO_CLUSTER_CAP    — max 1 position in {EUR_USD, GBP_USD}
    Rule 4: PORTFOLIO_RISK_CAP  — max 3 simultaneous open positions
    Rule 5: DRAWDOWN_HALT       — halt new entries at 8% portfolio DD

Output: results/phase2/
    stage1_core.md, stage2_add_usdcad.md, stage3_add_gbpusd.md,
    stage4_full_portfolio.md, portfolio_summary.md

Run from project root:
    python scripts/run_phase2_portfolio.py
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
from forex_system.backtest.portfolio_engine import (
    PortfolioBacktester,
    PortfolioResult,
)
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.risk.sizing import size_signal
from forex_system.risk.stops import compute_structural_stop
from forex_system.strategy.rules import TrendFollowStrategy

# ── Constants (FROZEN — identical to run_expansion_validation.py) ─────────────
GRAN            = "H4"
N_FOLDS         = 5
INITIAL_EQUITY  = 10_000.0
RISK_PCT        = 0.005
THROTTLED_RISK  = 0.0025
MIN_HOLD        = 5
PURGE_BARS      = 252
EMBARGO_BARS    = 10
WARMUP          = PURGE_BARS + EMBARGO_BARS

STRUCTURAL_LOOKBACK = 10
STRUCTURAL_MAX_MULT = 4.0

TF_FAST       = 8
TF_SLOW       = 21
TF_ADX_THRESH = 20.0

OUTPUT_DIR = PROJECT_ROOT / "results" / "phase2"

# ── Stage definitions ─────────────────────────────────────────────────────────
STAGES = [
    {
        "id":    "stage1_core",
        "label": "Stage 1: Core (USD_JPY + USD_CHF)",
        "pairs": ["USD_JPY", "USD_CHF"],
        "desc":  "Proven core — baseline for portfolio comparison.",
    },
    {
        "id":    "stage2_add_usdcad",
        "label": "Stage 2: Core + USD_CAD",
        "pairs": ["USD_JPY", "USD_CHF", "USD_CAD"],
        "desc":  "Add Phase 1 PASS: USD_CAD (Sharpe 0.876). "
                 "Same USD-base cluster — USD_CLUSTER_CAP tested.",
    },
    {
        "id":    "stage3_add_gbpusd",
        "label": "Stage 3: Core + USD_CAD + GBP_USD",
        "pairs": ["USD_JPY", "USD_CHF", "USD_CAD", "GBP_USD"],
        "desc":  "Add Phase 1 PASS: GBP_USD (Sharpe 0.837). "
                 "USD-quote cluster — adds EURO_CLUSTER rule.",
    },
    {
        "id":    "stage4_full_portfolio",
        "label": "Stage 4: Full Portfolio (+ AUD_USD)",
        "pairs": ["USD_JPY", "USD_CHF", "USD_CAD", "GBP_USD", "AUD_USD"],
        "desc":  "Add Phase 1 PASS: AUD_USD (Sharpe 1.414). "
                 "PORTFOLIO_RISK_CAP (max 3 positions) actively tested.",
    },
]

ALL_PAIRS = ["USD_JPY", "USD_CHF", "USD_CAD", "GBP_USD", "AUD_USD"]


# ── Data / features (verbatim from run_expansion_validation.py) ───────────────

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


# ── Signal generation (FROZEN — identical to run_expansion_validation.py) ─────

def gen_tf_signals(
    feat_df: pd.DataFrame,
    instrument: str,
    equity: float = INITIAL_EQUITY,
    risk_pct: float = RISK_PCT,
) -> pd.DataFrame:
    """
    TrendFollowStrategy with D1 gate (full) and structural stops.
    No 'atr' column in output → trail stop will not activate.
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

    result = pd.DataFrame({
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
        "atr_raw":       feat_df["atr_14"],
        "units":         0,
    })
    sized = size_signal(result.drop(columns=["units"]), instrument, equity, risk_pct)
    sized["atr_raw"] = feat_df["atr_14"].reindex(sized.index)
    return sized


def run_walk_forward(feat_df: pd.DataFrame, instrument: str) -> pd.DataFrame:
    n = len(feat_df)
    fold_size = n // N_FOLDS
    eval_dfs: list[pd.DataFrame] = []

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


# ── Report formatting ─────────────────────────────────────────────────────────

def _p(v, fmt: str = ".3f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return format(v, fmt)


def write_stage_report(
    stage: dict,
    result: PortfolioResult,
    baseline_sharpe: float | None,
    output_path: Path,
) -> None:
    m = result.portfolio_metrics
    pm = result.per_pair_metrics

    sharpe = m.get("sharpe", float("nan"))
    max_dd = m.get("max_drawdown", float("nan"))
    cagr   = m.get("cagr", float("nan"))
    n      = int(m.get("n_trades", 0))
    hr     = m.get("hit_rate", float("nan"))
    pf     = m.get("profit_factor", float("nan"))
    pr     = m.get("payoff_ratio", float("nan"))

    max_dd_str = f"{max_dd:.1%}" if not np.isnan(max_dd) else "N/A"
    hr_str     = f"{hr:.1%}" if not np.isnan(hr) else "N/A"
    cagr_str   = f"{cagr:.1%}" if not np.isnan(cagr) else "N/A"

    if baseline_sharpe is not None and not np.isnan(sharpe) and not np.isnan(baseline_sharpe):
        delta = sharpe - baseline_sharpe
        delta_str = f"{delta:+.3f}"
        if delta >= 0.05:
            verdict_line = f"Delta vs Stage 1 baseline: **{delta_str}** — IMPROVES portfolio"
        elif delta >= -0.05:
            verdict_line = f"Delta vs Stage 1 baseline: **{delta_str}** — NEUTRAL"
        else:
            verdict_line = f"Delta vs Stage 1 baseline: **{delta_str}** — DEGRADES portfolio"
    else:
        verdict_line = "Stage 1 baseline — reference point for subsequent stages."

    # Per-pair breakdown rows
    pair_rows: list[str] = []
    for pair in stage["pairs"]:
        pm_pair = pm.get(pair, {})
        ps  = _p(pm_pair.get("sharpe"), ".3f")
        pdd = f"{pm_pair.get('max_drawdown', float('nan')):.1%}" if pm_pair.get("max_drawdown") is not None else "N/A"
        pn  = int(pm_pair.get("n_trades", 0))
        phr = f"{pm_pair.get('hit_rate', float('nan')):.1%}" if pm_pair.get("hit_rate") is not None else "N/A"
        ppf = _p(pm_pair.get("profit_factor"), ".2f")
        pair_rows.append(f"| {pair} | {ps} | {pdd} | {pn} | {phr} | {ppf} |")

    lines = [
        f"# {stage['label']}",
        "",
        f"**Description:** {stage['desc']}",
        f"**Pairs:** {', '.join(stage['pairs'])}",
        "",
        "## Configuration (frozen — identical to proven system)",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Strategy | TrendFollowStrategy(fast={TF_FAST}, slow={TF_SLOW}, adx={TF_ADX_THRESH}) |",
        "| D1 gate | Full mode — SMA50, every bar |",
        f"| Stops | Structural (lookback={STRUCTURAL_LOOKBACK}, max_mult={STRUCTURAL_MAX_MULT}) |",
        "| Trail stop | DISABLED |",
        f"| Min hold bars | {MIN_HOLD} |",
        f"| Base risk per trade | {RISK_PCT:.1%} |",
        f"| Throttled risk (DD ≥ 5%) | {THROTTLED_RISK:.2%} |",
        "| DD halt threshold | 8% portfolio drawdown |",
        f"| Max open positions | 3 |",
        "| USD cluster cap | 2 same-direction |",
        "| Euro cluster cap | 1 (EUR_USD / GBP_USD) |",
        f"| Walk-forward | {N_FOLDS} sequential folds, H4 2020–now |",
        f"| Purge / embargo | {PURGE_BARS} / {EMBARGO_BARS} bars |",
        "| Cost model | Default spreads + 0.5 pip slippage |",
        "",
        "## Portfolio Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Sharpe | {_p(sharpe)} |",
        f"| Max DD | {max_dd_str} |",
        f"| CAGR | {cagr_str} |",
        f"| N Trades (portfolio total) | {n} |",
        f"| Hit Rate | {hr_str} |",
        f"| Profit Factor | {_p(pf, '.2f')} |",
        f"| Payoff Ratio | {_p(pr, '.2f')} |",
        f"| Avg win (pips) | {_p(m.get('avg_win_pips'), '.1f')} |",
        f"| Avg loss (pips) | {_p(m.get('avg_loss_pips'), '.1f')} |",
        "",
        "## Portfolio Rule Activity",
        "",
        "| Rule | Blocks | Description |",
        "|------|--------|-------------|",
        f"| USD_CLUSTER_CAP | {m.get('n_blocked_usd_cluster', 0)} | Max 2 same-direction USD positions |",
        f"| EURO_CLUSTER_CAP | {m.get('n_blocked_euro_cluster', 0)} | Max 1 EUR/GBP position |",
        f"| PORTFOLIO_RISK_CAP | {m.get('n_blocked_risk_cap', 0)} | Max 3 simultaneous positions |",
        f"| DRAWDOWN_HALT | {m.get('n_blocked_dd_halt', 0)} | New entries halted at 8% DD |",
        f"| **Total blocked** | **{m.get('n_blocked_total', 0)}** | All rules combined |",
        "",
        "## Per-Pair Breakdown",
        "",
        "_(Sharpe and MaxDD are approximate — computed from isolated pair P&L, not shared equity)_",
        "",
        "| Pair | Sharpe* | MaxDD* | N Trades | Hit Rate | PF |",
        "|------|---------|--------|----------|----------|----|",
    ] + pair_rows + [
        "",
        "## Stage Assessment",
        "",
        verdict_line,
        "",
        "---",
        "",
        "_Generated by `scripts/run_phase2_portfolio.py`_",
        f"_Phase 1 reference: USD\\_JPY Sharpe 1.289 | USD\\_CHF Sharpe 0.950_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report: {output_path}")


def write_summary(stage_results: list[dict], output_path: Path) -> None:
    """Write comparison table across all stages."""
    lines = [
        "# Phase 2 Portfolio Validation Summary",
        "",
        "Four staged portfolio configurations, each adding one Phase 1 PASS pair.",
        "Each stage's metrics reflect the full 5-fold walk-forward OOS period (H4, 2020–now).",
        "",
        "## Results Comparison",
        "",
        "| Stage | Pairs | Sharpe | Max DD | CAGR | N Trades | Hit Rate | PF | Payoff |",
        "|-------|-------|--------|--------|------|----------|----------|----|--------|",
    ]

    baseline_sharpe = None
    for row in stage_results:
        stage = row["stage"]
        m = row["result"].portfolio_metrics
        sharpe = m.get("sharpe", float("nan"))
        max_dd = m.get("max_drawdown", float("nan"))
        cagr   = m.get("cagr", float("nan"))
        n      = int(m.get("n_trades", 0))
        hr     = m.get("hit_rate", float("nan"))
        pf     = m.get("profit_factor", float("nan"))
        pr     = m.get("payoff_ratio", float("nan"))

        if baseline_sharpe is None and not np.isnan(sharpe):
            baseline_sharpe = sharpe

        pairs_str  = " + ".join(p.replace("USD_", "") for p in stage["pairs"])
        sharpe_str = _p(sharpe)
        max_dd_str = f"{max_dd:.1%}" if not np.isnan(max_dd) else "N/A"
        cagr_str   = f"{cagr:.1%}" if not np.isnan(cagr) else "N/A"
        hr_str     = f"{hr:.1%}" if not np.isnan(hr) else "N/A"
        pf_str     = _p(pf, ".2f")
        pr_str     = _p(pr, ".2f")

        lines.append(
            f"| {stage['label']} | {pairs_str} | {sharpe_str} | {max_dd_str} | "
            f"{cagr_str} | {n} | {hr_str} | {pf_str} | {pr_str} |"
        )

    lines += [
        "",
        "## Rule Impact Across Stages",
        "",
        "| Stage | USD_CAP | EURO_CAP | RISK_CAP | DD_HALT | Total Blocked |",
        "|-------|---------|----------|----------|---------|---------------|",
    ]
    for row in stage_results:
        stage = row["stage"]
        m = row["result"].portfolio_metrics
        lines.append(
            f"| {stage['label']} | "
            f"{m.get('n_blocked_usd_cluster', 0)} | "
            f"{m.get('n_blocked_euro_cluster', 0)} | "
            f"{m.get('n_blocked_risk_cap', 0)} | "
            f"{m.get('n_blocked_dd_halt', 0)} | "
            f"{m.get('n_blocked_total', 0)} |"
        )

    lines += [
        "",
        "## Acceptance Criteria",
        "",
        "A stage **passes** if portfolio Sharpe ≥ Stage 1 baseline **and** MaxDD ≤ 12%.",
        "Each pair added should not degrade the risk-adjusted return of the portfolio.",
        "",
        "---",
        "",
        "_Generated by `scripts/run_phase2_portfolio.py`_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Summary: {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("Phase 2: Portfolio Construction Validation")
    logger.info(
        f"TF: fast={TF_FAST}, slow={TF_SLOW}, adx={TF_ADX_THRESH} | "
        f"D1 gate: full | min_hold={MIN_HOLD}"
    )
    logger.info(
        f"Structural stops: lookback={STRUCTURAL_LOOKBACK}, max_mult={STRUCTURAL_MAX_MULT}"
    )
    logger.info(f"Walk-forward: {N_FOLDS} folds | purge={PURGE_BARS} | embargo={EMBARGO_BARS}")
    logger.info(f"All pairs: {ALL_PAIRS}")
    logger.info("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Load data and build features for all pairs once ──────────────
    feat_dfs: dict[str, pd.DataFrame] = {}
    for pair in ALL_PAIRS:
        logger.info(f"\nLoading {pair}...")
        h4_raw = load_raw(pair, "H4")
        d_raw  = load_raw(pair, "D")
        feat_dfs[pair] = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feat_dfs[pair])} H4 bars after feature build")

    # ── Step 2: Generate walk-forward signals for all pairs once ─────────────
    logger.info("\n" + "─" * 70)
    logger.info("Generating walk-forward signals for all pairs...")
    signal_dfs: dict[str, pd.DataFrame] = {}
    for pair in ALL_PAIRS:
        logger.info(f"\n{pair}: running {N_FOLDS}-fold walk-forward...")
        signal_dfs[pair] = run_walk_forward(feat_dfs[pair], pair)
        n_sig = int((signal_dfs[pair]["signal"] != 0).sum())
        logger.info(f"{pair}: {len(signal_dfs[pair])} eval bars | {n_sig} signals")

    # ── Step 3: Run each stage ───────────────────────────────────────────────
    stage_results: list[dict] = []
    baseline_sharpe: float | None = None

    for stage in STAGES:
        logger.info(f"\n{'═' * 70}")
        logger.info(f"Running: {stage['label']}")
        logger.info(f"Pairs: {stage['pairs']}")
        logger.info("─" * 70)

        stage_signals = {p: signal_dfs[p] for p in stage["pairs"]}

        # Set MINIMUM_HOLD_BARS to the frozen config value
        orig_min_hold = _engine_mod.MINIMUM_HOLD_BARS
        _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
        try:
            bt = PortfolioBacktester(
                initial_equity=INITIAL_EQUITY,
                cost_model=CostModel(),
                base_risk_pct=RISK_PCT,
                throttled_risk_pct=THROTTLED_RISK,
                trail_enabled=False,
                stage_label=stage["label"],
            )
            result = bt.run(stage_signals)
        finally:
            _engine_mod.MINIMUM_HOLD_BARS = orig_min_hold

        m = result.portfolio_metrics
        sharpe = m.get("sharpe", float("nan"))
        max_dd = m.get("max_drawdown", float("nan"))

        # Stage 1 sets the baseline
        if baseline_sharpe is None and not np.isnan(sharpe):
            baseline_sharpe = sharpe

        logger.info(
            f"  Sharpe: {_p(sharpe)} | "
            f"MaxDD: {max_dd:.1%} | "
            f"CAGR: {_p(m.get('cagr'), '.1%')} | "
            f"N: {int(m.get('n_trades', 0))} | "
            f"HR: {_p(m.get('hit_rate'), '.1%')} | "
            f"PF: {_p(m.get('profit_factor'), '.2f')}"
        )
        logger.info(
            f"  Blocked — USD_CAP: {m.get('n_blocked_usd_cluster', 0)} | "
            f"EURO_CAP: {m.get('n_blocked_euro_cluster', 0)} | "
            f"RISK_CAP: {m.get('n_blocked_risk_cap', 0)} | "
            f"DD_HALT: {m.get('n_blocked_dd_halt', 0)} | "
            f"Total: {m.get('n_blocked_total', 0)}"
        )

        out_path = OUTPUT_DIR / f"{stage['id']}.md"
        write_stage_report(stage, result, baseline_sharpe, out_path)
        stage_results.append({"stage": stage, "result": result})

    # ── Step 4: Write summary ─────────────────────────────────────────────────
    write_summary(stage_results, OUTPUT_DIR / "portfolio_summary.md")

    # ── Console summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("PHASE 2 PORTFOLIO VALIDATION SUMMARY")
    print("=" * 78)
    print(f"{'Stage':<38} {'Sharpe':>8} {'MaxDD':>8} {'N':>5} {'HR':>6} {'PF':>5}")
    print("-" * 78)
    for row in stage_results:
        m = row["result"].portfolio_metrics
        sharpe = m.get("sharpe", float("nan"))
        max_dd = m.get("max_drawdown", float("nan"))
        hr     = m.get("hit_rate", float("nan"))
        pf     = m.get("profit_factor", float("nan"))
        label  = row["stage"]["label"][:37]
        print(
            f"{label:<38} "
            f"{_p(sharpe):>8} "
            f"{max_dd:.1%}  "
            f"{int(m.get('n_trades', 0)):>5} "
            f"{hr:.1%}  "
            f"{_p(pf, '.2f'):>5}"
        )
    print("=" * 78)
    print(f"\nResults saved to: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
