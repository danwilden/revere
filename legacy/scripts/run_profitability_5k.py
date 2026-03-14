"""
Dollar profitability of the confirmed system with $5,000 starting capital.

System (unchanged from STEP 0 baseline decision):
  - TrendFollowStrategy(fast=8, slow=21, adx=20) + D1 gate (full)
  - Structural stops (lookback=10, max_mult=4.0)
  - Trail: DISABLED
  - MINIMUM_HOLD_BARS=5
  - 5-fold walk-forward, H4 2020–now

Two sections:
  1. Full portfolio (USD_JPY + USD_CHF + USD_CAD + GBP_USD + AUD_USD)
     run at 4 risk levels: 0.5%, 1.0%, 1.5%, 2.0%
     Throttled risk = half of base risk on 5%+ portfolio DD.
  2. Core pairs (USD_JPY + USD_CHF) independently at base risk (0.5%)
     as a reference baseline.

Run from project root:
    python scripts/run_profitability_5k.py

Output: results/tf_profitability_5k.md
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
from forex_system.backtest.portfolio_engine import PortfolioBacktester
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.risk.sizing import size_signal
from forex_system.risk.stops import compute_structural_stop
from forex_system.strategy.rules import TrendFollowStrategy

PAIRS_CORE    = ["USD_JPY", "USD_CHF"]
ALL_PAIRS     = ["USD_JPY", "USD_CHF", "USD_CAD", "GBP_USD", "AUD_USD"]
RISK_LEVELS   = [0.005, 0.01, 0.015, 0.02]   # 0.5%, 1.0%, 1.5%, 2.0%

GRAN           = "H4"
N_FOLDS        = 5
INITIAL_EQUITY = 5_000.0
BASE_RISK_PCT  = 0.005   # used for core-pair individual runs
MIN_HOLD       = 5
PURGE_BARS     = 252
EMBARGO_BARS   = 10
WARMUP         = PURGE_BARS + EMBARGO_BARS

STRUCTURAL_LOOKBACK = 10
STRUCTURAL_MAX_MULT = 4.0
TF_FAST       = 8
TF_SLOW       = 21
TF_ADX_THRESH = 20.0


# ── Data / features ───────────────────────────────────────────────────────────

def load_raw(instrument: str, granularity: str) -> pd.DataFrame:
    path = settings.data_raw / f"{instrument}_{granularity}_2020-01-01_now.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Raw data not found: {path}\nRun notebooks/01_data_pull.ipynb first."
        )
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if "complete" in df.columns:
        df = df[df["complete"]].copy()
    return df


def build_features(instrument: str, h4_df: pd.DataFrame, d_df: pd.DataFrame) -> pd.DataFrame:
    pipeline = FeaturePipeline(horizon=1)
    feat = pipeline.build(h4_df, include_labels=False, filter_incomplete=False,
                          daily_df=d_df, instrument=instrument)
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


def gen_signals(
    feat_df: pd.DataFrame,
    instrument: str,
    equity: float = INITIAL_EQUITY,
    risk_pct: float = BASE_RISK_PCT,
) -> pd.DataFrame:
    """TrendFollowStrategy + D1 gate (full) + structural stops."""
    ohlcv = feat_df[["open", "high", "low", "close"]].copy()
    strat = TrendFollowStrategy(fast_ema=TF_FAST, slow_ema=TF_SLOW, adx_threshold=TF_ADX_THRESH)
    signal = strat.generate(ohlcv)["signal"].copy()

    gate = feat_df["trend_regime_50d"]
    signal[gate.notna() & (gate < 0) & (signal == 1)]  = 0
    signal[gate.notna() & (gate > 0) & (signal == -1)] = 0
    signal[gate.isna()] = 0

    stop_dist = compute_structural_stop(
        feat_df["high"], feat_df["low"], feat_df["close"],
        direction=signal, lookback=STRUCTURAL_LOOKBACK, max_mult=STRUCTURAL_MAX_MULT,
    )
    result = pd.DataFrame({
        "open": feat_df["open"], "high": feat_df["high"],
        "low":  feat_df["low"],  "close": feat_df["close"],
        "signal": signal, "stop_distance": stop_dist,
    })
    return size_signal(result, instrument, equity, risk_pct)


def run_walk_forward(
    feat_df: pd.DataFrame,
    instrument: str,
    equity: float = INITIAL_EQUITY,
    risk_pct: float = BASE_RISK_PCT,
) -> pd.DataFrame:
    n = len(feat_df)
    fold_size = n // N_FOLDS
    eval_dfs: list[pd.DataFrame] = []
    for fold_idx in range(N_FOLDS):
        start = fold_idx * fold_size
        end   = (fold_idx + 1) * fold_size if fold_idx < N_FOLDS - 1 else n
        fold  = feat_df.iloc[start:end]
        if len(fold) <= WARMUP + 50:
            continue
        sig = gen_signals(fold, instrument, equity=equity, risk_pct=risk_pct)
        eval_dfs.append(sig.iloc[WARMUP:])
    if not eval_dfs:
        raise RuntimeError(f"No fold data for {instrument}")
    return pd.concat(eval_dfs)


def run_single_pair_backtest(
    instrument: str,
    signal_df: pd.DataFrame,
    cost_model: CostModel,
    equity: float,
):
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
    try:
        bt = VectorizedBacktester(
            initial_equity=equity,
            cost_model=cost_model,
            trail_enabled=False,
        )
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


def run_portfolio_backtest(
    signal_dfs: dict[str, pd.DataFrame],
    risk_pct: float,
    cost_model: CostModel,
    equity: float,
):
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
    try:
        bt = PortfolioBacktester(
            initial_equity=equity,
            cost_model=cost_model,
            base_risk_pct=risk_pct,
            throttled_risk_pct=risk_pct / 2,   # half risk on 5%+ DD
            trail_enabled=False,
            stage_label=f"full_portfolio_{risk_pct:.1%}",
        )
        return bt.run(signal_dfs)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


# ── Equity analysis helpers ───────────────────────────────────────────────────

def year_by_year(equity_curve: pd.Series) -> list[dict]:
    eq = equity_curve.copy()
    if not isinstance(eq.index, pd.DatetimeIndex):
        return []
    rows = []
    for yr, grp in eq.groupby(eq.index.year):
        start_eq = grp.iloc[0]
        end_eq   = grp.iloc[-1]
        rows.append({
            "year":       yr,
            "start":      start_eq,
            "end":        end_eq,
            "gain_usd":   end_eq - start_eq,
            "return_pct": (end_eq - start_eq) / start_eq,
        })
    return rows


def extract_portfolio_stats(result, initial_equity: float) -> dict:
    m  = result.portfolio_metrics
    eq = result.equity_curve
    final_eq   = float(eq.iloc[-1]) if len(eq) else initial_equity
    total_gain = final_eq - initial_equity
    max_dd_pct = m.get("max_drawdown", float("nan"))
    max_dd_usd = max_dd_pct * initial_equity

    trades_df = result.trades_df()
    avg_win_usd  = float(trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean()) if not trades_df.empty else float("nan")
    avg_loss_usd = float(trades_df.loc[trades_df["pnl"] < 0, "pnl"].mean()) if not trades_df.empty else float("nan")

    return {
        "start_equity": initial_equity,
        "final_equity": final_eq,
        "total_gain":   total_gain,
        "total_return": total_gain / initial_equity,
        "cagr":         m.get("cagr", float("nan")),
        "max_dd_pct":   max_dd_pct,
        "max_dd_usd":   max_dd_usd,
        "sharpe":       m.get("sharpe", float("nan")),
        "n_trades":     m.get("n_trades", 0),
        "hit_rate":     m.get("hit_rate", float("nan")),
        "profit_factor": m.get("profit_factor", float("nan")),
        "payoff_ratio":  m.get("payoff_ratio", float("nan")),
        "avg_win_usd":  avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
        "year_by_year": year_by_year(eq),
        "n_blocked_usd_cluster": m.get("n_blocked_usd_cluster", 0),
        "n_blocked_risk_cap":    m.get("n_blocked_risk_cap", 0),
        "n_blocked_total":       m.get("n_blocked_total", 0),
    }


def extract_single_pair_stats(result, initial_equity: float) -> dict:
    m  = result.metrics
    eq = result.equity_curve
    final_eq   = float(eq.iloc[-1]) if len(eq) else initial_equity
    total_gain = final_eq - initial_equity
    max_dd_pct = m.get("max_drawdown", float("nan"))
    max_dd_usd = max_dd_pct * initial_equity

    trades_df = result.trades_df()
    avg_win_usd  = float(trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean()) if not trades_df.empty else float("nan")
    avg_loss_usd = float(trades_df.loc[trades_df["pnl"] < 0, "pnl"].mean()) if not trades_df.empty else float("nan")

    return {
        "start_equity": initial_equity,
        "final_equity": final_eq,
        "total_gain":   total_gain,
        "total_return": total_gain / initial_equity,
        "cagr":         m.get("cagr", float("nan")),
        "max_dd_pct":   max_dd_pct,
        "max_dd_usd":   max_dd_usd,
        "sharpe":       m.get("sharpe", float("nan")),
        "n_trades":     m.get("n_trades", 0),
        "hit_rate":     m.get("hit_rate", float("nan")),
        "avg_win_usd":  avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
        "year_by_year": year_by_year(eq),
    }


# ── Report ─────────────────────────────────────────────────────────────────────

def _f(v, fmt) -> str:
    try:
        if isinstance(v, float) and np.isnan(v):
            return "N/A"
        return fmt(v)
    except (TypeError, ValueError):
        return "N/A"


def write_report(
    portfolio_stats: dict[float, dict],   # risk_pct → stats
    core_stats: dict[str, dict],          # pair → stats
    output_path: Path,
) -> None:
    lines = [
        "# Dollar Profitability — $5,000 Starting Capital",
        "",
        f"**System**: TrendFollowStrategy(fast={TF_FAST}, slow={TF_SLOW},"
        f" adx={TF_ADX_THRESH}) + D1 gate (full)",
        f"**Stops**: structural (lookback={STRUCTURAL_LOOKBACK},"
        f" max_mult={STRUCTURAL_MAX_MULT}) | Trail: DISABLED",
        f"**Walk-forward**: {N_FOLDS} folds, H4, purge={PURGE_BARS}, embargo={EMBARGO_BARS}",
        f"**Starting capital**: ${INITIAL_EQUITY:,.0f}",
        "",
        "---",
        "",
        "## Full Portfolio — Risk Sweep",
        "",
        f"5 pairs: {' + '.join(ALL_PAIRS)}",
        "Portfolio rules: max 3 positions | USD cluster cap (max 2 same-direction) |"
        " Euro cluster cap | 5% DD throttle (half risk) | 8% DD halt",
        "",
        "| Risk | Final ($) | Total Gain | Return | CAGR | Max DD | Max DD ($) | Sharpe | N | HR | PF |",
        "|------|-----------|------------|--------|------|--------|------------|--------|---|----|----|",
    ]

    for risk in RISK_LEVELS:
        s = portfolio_stats[risk]
        lines.append(
            f"| {risk:.1%} "
            f"| ${s['final_equity']:>8,.0f} "
            f"| {_f(s['total_gain'], lambda v: f'${v:+,.0f}')} "
            f"| {_f(s['total_return'], lambda v: f'{v:+.1%}')} "
            f"| {_f(s['cagr'], lambda v: f'{v:+.1%}')} "
            f"| {_f(s['max_dd_pct'], lambda v: f'{v:.1%}')} "
            f"| {_f(s['max_dd_usd'], lambda v: f'${abs(v):,.0f}')} "
            f"| {_f(s['sharpe'], lambda v: f'{v:.3f}')} "
            f"| {int(s['n_trades'])} "
            f"| {_f(s['hit_rate'], lambda v: f'{v:.1%}')} "
            f"| {_f(s['profit_factor'], lambda v: f'{v:.2f}')} |"
        )

    lines += [
        "",
        "### Portfolio rule activity (blocks per risk level)",
        "",
        "| Risk | USD_CLUSTER_CAP | PORTFOLIO_RISK_CAP | Total Blocked |",
        "|------|-----------------|-------------------|---------------|",
    ]
    for risk in RISK_LEVELS:
        s = portfolio_stats[risk]
        lines.append(
            f"| {risk:.1%} "
            f"| {s['n_blocked_usd_cluster']} "
            f"| {s['n_blocked_risk_cap']} "
            f"| {s['n_blocked_total']} |"
        )

    # Year-by-year for each risk level
    lines += ["", "### Year-by-year — Full Portfolio", ""]
    for risk in RISK_LEVELS:
        s  = portfolio_stats[risk]
        yby = s.get("year_by_year", [])
        if not yby:
            continue
        lines += [
            f"#### Risk {risk:.1%}",
            "",
            "| Year | Start ($) | End ($) | Gain ($) | Return |",
            "|------|-----------|---------|----------|--------|",
        ]
        for row in yby:
            lines.append(
                f"| {row['year']} "
                f"| ${row['start']:>8,.0f} "
                f"| ${row['end']:>8,.0f} "
                f"| {row['gain_usd']:>+9,.0f} "
                f"| {row['return_pct']:>+.1%} |"
            )
        lines.append("")

    # Core pairs reference (base risk only)
    lines += [
        "---",
        "",
        "## Core Pairs Reference (USD_JPY + USD_CHF, independent, 0.5% risk)",
        "",
        "| | USD_JPY | USD_CHF |",
        "|-|---------|---------|",
    ]
    for label, key, fmt in [
        ("Final equity",     "final_equity",  lambda v: f"${v:,.0f}"),
        ("Total gain ($)",   "total_gain",    lambda v: f"${v:+,.0f}"),
        ("Total return",     "total_return",  lambda v: f"{v:+.1%}"),
        ("CAGR",             "cagr",          lambda v: f"{v:+.1%}"),
        ("Max drawdown (%)", "max_dd_pct",    lambda v: f"{v:.1%}"),
        ("Max drawdown ($)", "max_dd_usd",    lambda v: f"${abs(v):,.0f}"),
        ("Sharpe",           "sharpe",        lambda v: f"{v:.3f}"),
        ("N trades",         "n_trades",      lambda v: str(int(v))),
        ("Hit rate",         "hit_rate",      lambda v: f"{v:.1%}"),
        ("Avg win ($)",      "avg_win_usd",   lambda v: f"${v:,.0f}"),
        ("Avg loss ($)",     "avg_loss_usd",  lambda v: f"${v:,.0f}"),
    ]:
        vals = {}
        for pair in PAIRS_CORE:
            v = core_stats[pair].get(key, float("nan"))
            vals[pair] = _f(v, fmt)
        lines.append(f"| {label} | {vals['USD_JPY']} | {vals['USD_CHF']} |")

    lines += [
        "",
        "---",
        "",
        "_Generated by `scripts/run_profitability_5k.py`_",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info(f"Profitability — ${INITIAL_EQUITY:,.0f} starting capital")
    logger.info(f"Risk levels: {[f'{r:.1%}' for r in RISK_LEVELS]}")
    logger.info(f"Full portfolio: {ALL_PAIRS}")
    logger.info("=" * 70)

    cost_model = CostModel()

    # ── Step 1: Load data and build features for all 5 pairs ─────────────────
    feat_dfs: dict[str, pd.DataFrame] = {}
    for pair in ALL_PAIRS:
        logger.info(f"\nLoading {pair}...")
        h4_raw  = load_raw(pair, "H4")
        d_raw   = load_raw(pair, "D")
        feat_dfs[pair] = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feat_dfs[pair])} H4 bars")

    # ── Step 2: Generate walk-forward signals once (base risk) ───────────────
    # Portfolio engine recalculates units at entry time, so signal_df units
    # are irrelevant. Generate once with base risk to avoid repeated work.
    logger.info("\n" + "─" * 70)
    logger.info("Generating walk-forward signals...")
    signal_dfs: dict[str, pd.DataFrame] = {}
    for pair in ALL_PAIRS:
        logger.info(f"  {pair}...")
        signal_dfs[pair] = run_walk_forward(feat_dfs[pair], pair,
                                            equity=INITIAL_EQUITY,
                                            risk_pct=BASE_RISK_PCT)
        n_sig = int((signal_dfs[pair]["signal"] != 0).sum())
        logger.info(f"  {pair}: {len(signal_dfs[pair])} eval bars | {n_sig} signals")

    # ── Step 3: Full portfolio at each risk level ─────────────────────────────
    logger.info("\n" + "─" * 70)
    logger.info("Running full portfolio at 4 risk levels...")
    portfolio_stats: dict[float, dict] = {}

    for risk in RISK_LEVELS:
        logger.info(f"\n  Risk {risk:.1%}...")
        result = run_portfolio_backtest(signal_dfs, risk, cost_model, INITIAL_EQUITY)
        stats  = extract_portfolio_stats(result, INITIAL_EQUITY)
        portfolio_stats[risk] = stats
        logger.info(
            f"    ${INITIAL_EQUITY:,.0f} → ${stats['final_equity']:,.0f} "
            f"({stats['total_gain']:+,.0f} / {stats['total_return']:+.1%}) | "
            f"CAGR {stats['cagr']:+.1%} | Sharpe {stats['sharpe']:.3f} | "
            f"MaxDD {stats['max_dd_pct']:.1%} | Blocked {stats['n_blocked_total']}"
        )

    # ── Step 4: Core pairs individually (base risk, for reference) ───────────
    logger.info("\n" + "─" * 70)
    logger.info(f"Running core pairs individually at base risk {BASE_RISK_PCT:.1%}...")
    core_stats: dict[str, dict] = {}

    for pair in PAIRS_CORE:
        logger.info(f"  {pair}...")
        result = run_single_pair_backtest(pair, signal_dfs[pair], cost_model, INITIAL_EQUITY)
        core_stats[pair] = extract_single_pair_stats(result, INITIAL_EQUITY)
        s = core_stats[pair]
        logger.info(
            f"    ${INITIAL_EQUITY:,.0f} → ${s['final_equity']:,.0f} "
            f"({s['total_gain']:+,.0f}) | CAGR {s['cagr']:+.1%} | "
            f"Sharpe {s['sharpe']:.3f} | MaxDD {s['max_dd_pct']:.1%}"
        )

    # ── Step 5: Console summary ───────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"FULL PORTFOLIO — ${INITIAL_EQUITY:,.0f} starting capital")
    print(f"{'Risk':<8} {'Final':>9} {'Gain':>9} {'Return':>8} "
          f"{'CAGR':>7} {'Sharpe':>7} {'MaxDD':>7} {'N':>5}")
    print("-" * 78)
    for risk in RISK_LEVELS:
        s = portfolio_stats[risk]
        print(
            f"{risk:.1%}   "
            f"${s['final_equity']:>8,.0f}  "
            f"{s['total_gain']:>+8,.0f}  "
            f"{s['total_return']:>+7.1%}  "
            f"{s['cagr']:>+6.1%}  "
            f"{s['sharpe']:>6.3f}  "
            f"{s['max_dd_pct']:>6.1%}  "
            f"{int(s['n_trades']):>5}"
        )

    print("\n" + "─" * 78)
    print("CORE PAIRS (independent, 0.5% risk)")
    for pair in PAIRS_CORE:
        s = core_stats[pair]
        print(f"\n  {pair}: ${s['start_equity']:,.0f} → ${s['final_equity']:,.0f}"
              f"  ({s['total_gain']:+,.0f}  {s['total_return']:+.1%})"
              f"  CAGR {s['cagr']:+.1%}  Sharpe {s['sharpe']:.3f}  MaxDD {s['max_dd_pct']:.1%}")

    output_path = PROJECT_ROOT / "results" / "tf_profitability_5k.md"
    write_report(portfolio_stats, core_stats, output_path)
    print(f"\nReport: {output_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()
