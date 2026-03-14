"""
CHANGE 7 (v2.4): Walk-forward validation of USD_JPY + USD_CHF H4 with all
v2.4 changes active. Outputs results/v2.4_validation.md.

Run from project root:
    python scripts/validate_v2.4.py

Prerequisites:
    - Cached H4 and D raw parquet files in data/raw/ (from notebooks/01_data_pull)
    - pip install -e . with .env configured

PASS criteria to proceed to paper trading:
    - Both pairs: Sharpe > 0.5
    - Both pairs: Max drawdown < 12%
    - Both pairs: N_trades > 150
    - Both pairs: Hit rate > 34%
    - Portfolio combined Sharpe > 0.6 (accounting for correlation guard)

v2.4 changes validated here:
    CHANGE 1: Only USD_JPY + USD_CHF
    CHANGE 2: Structural stops + trail stop in engine
    CHANGE 3: RegimeRouter (no consensus voting; regime-selected strategy)
    CHANGE 5: ML disabled (signal path is rule-only)
    CHANGE 6: Correlation guard applied at signal level pre-backtest
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

# Ensure project root is on path when running as script
PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import VectorizedBacktester, PERIODS_PER_YEAR
from forex_system.backtest.metrics import full_tearsheet
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.strategy.signals import RegimeRouter

# ── Validation parameters ──────────────────────────────────────────────────────

PAIRS         = ["USD_JPY", "USD_CHF"]
GRAN          = "H4"
N_FOLDS       = 5
INITIAL_EQUITY = 10_000.0
RISK_PCT      = 0.005          # 0.5% per trade

PIP_SIZES = {"USD_JPY": 0.01, "USD_CHF": 0.0001}

# PASS criteria
PASS_SHARPE           = 0.5
PASS_MAX_DD           = 0.12
PASS_N_TRADES         = 150
PASS_HIT_RATE         = 0.34
PASS_PORTFOLIO_SHARPE = 0.6

# ── Data loading ───────────────────────────────────────────────────────────────

def load_raw(instrument: str, granularity: str) -> pd.DataFrame:
    """Load cached raw OHLCV parquet from data/raw/."""
    path = settings.data_raw / f"{instrument}_{granularity}_2020-01-01_now.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Raw data not found: {path}\n"
            "Run notebooks/01_data_pull.ipynb first to cache raw candles."
        )
    df = pd.read_parquet(path)
    # Ensure DatetimeIndex in UTC
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    # Filter incomplete bars
    if "complete" in df.columns:
        df = df[df["complete"]].copy()
    return df


def build_features(instrument: str, h4_df: pd.DataFrame, d_df: pd.DataFrame) -> pd.DataFrame:
    """Build v2.2 features + v2.4 labels using FeaturePipeline."""
    pipeline = FeaturePipeline(horizon=1)
    feat = pipeline.build(
        h4_df,
        include_labels=False,   # no labels needed for rule-based backtest
        filter_incomplete=False,  # already filtered above
        daily_df=d_df,
        instrument=instrument,
    )
    feat = feat.dropna(subset=["adx_14", "vol_ratio_10_60", "atr_14"])
    # Deduplicate index (keep last) so reindex does not raise on duplicate labels
    if feat.index.duplicated().any():
        feat = feat[~feat.index.duplicated(keep="last")]
    ohlc = h4_df[["open", "high", "low", "close"]].copy()
    if ohlc.index.duplicated().any():
        ohlc = ohlc[~ohlc.index.duplicated(keep="last")]
    # RegimeRouter.route() expects open, high, low, close on the dataframe
    ohlc = ohlc.reindex(feat.index)
    for col in ["open", "high", "low", "close"]:
        feat[col] = ohlc[col]
    return feat


# ── Walk-forward signal generation ────────────────────────────────────────────

def build_signals_walkforward(
    instrument: str,
    feat_df: pd.DataFrame,
    n_folds: int = N_FOLDS,
    equity: float = INITIAL_EQUITY,
) -> pd.DataFrame:
    """
    Generate signals using RegimeRouter across 5 sequential OOS folds.

    Since RegimeRouter is purely rule-based (no ML training), walk-forward
    here means sequential OOS evaluation — each fold tests on unseen data,
    and fold boundaries match the 5-fold split used in training notebooks.

    Returns:
        Full-length signal DataFrame with out-of-sample signals only.
    """
    pip_size = PIP_SIZES.get(instrument, 0.0001)
    router   = RegimeRouter()

    n = len(feat_df)
    fold_size = n // n_folds
    all_signal_dfs = []

    for fold_idx in range(n_folds):
        start_idx = fold_idx * fold_size
        end_idx   = (fold_idx + 1) * fold_size if fold_idx < n_folds - 1 else n
        fold_df   = feat_df.iloc[start_idx:end_idx]

        if len(fold_df) < 100:
            logger.warning(f"Fold {fold_idx} too small ({len(fold_df)} bars), skipping")
            continue

        signal_df = router.route(
            fold_df,
            instrument=instrument,
            pip_size=pip_size,
            equity=equity,
            risk_pct=RISK_PCT,
        )
        all_signal_dfs.append(signal_df)
        logger.info(
            f"Fold {fold_idx}/{n_folds-1} | {instrument} | "
            f"bars={len(fold_df)} | "
            f"signals={int((signal_df['signal'] != 0).sum())}"
        )

    if not all_signal_dfs:
        raise RuntimeError(f"No valid folds generated for {instrument}")

    return pd.concat(all_signal_dfs)


# ── Correlation guard at signal level ─────────────────────────────────────────

def apply_correlation_guard_signals(
    signal_jpydf: pd.DataFrame,
    signal_chfdf: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    CHANGE 6: Apply correlation guard at signal level before backtesting.

    Where USD_JPY and USD_CHF have the same direction simultaneously,
    reject the USD_CHF signal (keep USD_JPY as primary). This is the
    conservative approximation; the live PortfolioManager handles this
    dynamically based on actual open positions.

    Returns:
        Modified (signal_jpy_df, signal_chf_df) with rejections applied.
    """
    # Align on shared index
    shared_idx = signal_jpydf.index.intersection(signal_chfdf.index)

    jpy_sig = signal_jpydf["signal"].reindex(shared_idx)
    chf_sig = signal_chfdf["signal"].reindex(shared_idx)

    # Find bars where both are non-zero and same direction
    both_active     = (jpy_sig != 0) & (chf_sig != 0)
    same_direction  = jpy_sig == chf_sig
    rejected_mask   = both_active & same_direction

    n_rejected = int(rejected_mask.sum())
    if n_rejected > 0:
        logger.info(
            f"Correlation guard (signal-level): rejected {n_rejected} USD_CHF "
            "signals that were same-direction as simultaneous USD_JPY signal"
        )

    # Zero out CHF signals where guard triggers
    chf_sig_filtered = chf_sig.copy()
    chf_sig_filtered[rejected_mask] = 0

    jpy_out = signal_jpydf.copy()
    chf_out = signal_chfdf.copy()
    chf_out.loc[rejected_mask, "signal"] = 0
    chf_out.loc[rejected_mask, "units"]  = 0

    return jpy_out, chf_out


# ── Portfolio Sharpe from combined equity ─────────────────────────────────────

def portfolio_sharpe(
    eq_jpy: pd.Series,
    eq_chf: pd.Series,
    ppy: int = PERIODS_PER_YEAR["H4"],
) -> float:
    """
    Equal-weight portfolio Sharpe from two equity curves.
    Combines bar-level percentage returns before computing Sharpe.
    """
    ret_jpy = eq_jpy.pct_change().dropna()
    ret_chf = eq_chf.pct_change().dropna()

    # Align to shared index
    combined = pd.concat([ret_jpy, ret_chf], axis=1).dropna()
    if combined.empty:
        return float("nan")

    port_returns = combined.mean(axis=1)   # equal-weight

    if port_returns.std() == 0:
        return float("nan")
    return float(port_returns.mean() / port_returns.std() * np.sqrt(ppy))


# ── Results report ─────────────────────────────────────────────────────────────

def write_report(
    pair_results: dict,
    port_sharpe: float,
    all_pass: bool,
    output_path: Path,
) -> None:
    """Write v2.4_validation.md report."""
    lines = [
        "# v2.4 Walk-Forward Validation — USD_JPY + USD_CHF",
        "",
        "## System",
        "- Strategy: RegimeRouter (TRENDING→Trend, RANGING→MeanRev, BREAKOUT→Breakout)",
        "- Stops: Structural (swing low/high ±0.5×ATR), clamped [1.5×ATR, 4.0×ATR]",
        "- Trail: activates at +1.5×ATR profit, trails at 1.0×ATR behind best close",
        "- D1 gate: applied to TRENDING + BREAKOUT signals only",
        "- ML: DISABLED (shadow mode logging active)",
        "- Correlation guard: same-direction USD_JPY + USD_CHF rejected at signal level",
        f"- Walk-forward folds: {N_FOLDS}",
        f"- Risk per trade: {RISK_PCT*100:.1f}% equity",
        "",
        "## Pass Criteria",
        f"| Metric | Threshold | Notes |",
        f"|--------|-----------|-------|",
        f"| Sharpe (per pair) | > {PASS_SHARPE} | |",
        f"| Max Drawdown | < {PASS_MAX_DD*100:.0f}% | |",
        f"| N Trades | > {PASS_N_TRADES} | signal frequency check |",
        f"| Hit Rate | > {PASS_HIT_RATE*100:.0f}% | no regression allowed |",
        f"| Portfolio Sharpe | > {PASS_PORTFOLIO_SHARPE} | |",
        "",
        "## Per-Pair Results",
        "",
        "| Metric | USD_JPY | USD_CHF | Pass? |",
        "|--------|---------|---------|-------|",
    ]

    metrics_to_show = [
        ("sharpe",       "Sharpe",     lambda v: f"{v:.3f}",  lambda v: v > PASS_SHARPE),
        ("max_drawdown", "Max DD",     lambda v: f"{v:.1%}",  lambda v: abs(v) < PASS_MAX_DD),
        ("n_trades",     "N Trades",   lambda v: str(int(v)), lambda v: v > PASS_N_TRADES),
        ("hit_rate",     "Hit Rate",   lambda v: f"{v:.1%}",  lambda v: v > PASS_HIT_RATE),
    ]

    for key, label, fmt, check in metrics_to_show:
        jpy_val = pair_results["USD_JPY"]["metrics"].get(key, float("nan"))
        chf_val = pair_results["USD_CHF"]["metrics"].get(key, float("nan"))
        both_pass = check(jpy_val) and check(chf_val)
        pass_str = "PASS ✓" if both_pass else "FAIL ✗"
        lines.append(
            f"| {label} | {fmt(jpy_val)} | {fmt(chf_val)} | {pass_str} |"
        )

    port_pass = port_sharpe > PASS_PORTFOLIO_SHARPE
    lines += [
        "",
        "## Portfolio",
        f"| Metric | Value | Pass? |",
        f"|--------|-------|-------|",
        f"| Portfolio Sharpe | {port_sharpe:.3f} | {'PASS ✓' if port_pass else 'FAIL ✗'} |",
        "",
    ]

    if all_pass:
        lines += [
            "## VERDICT",
            "",
            "**READY FOR PAPER TRADING — USD_JPY + USD_CHF**",
            "",
            "All validation criteria met. Proceed to notebooks/07_paper_trading_validation.ipynb",
            "and paper-trade for a meaningful period before live promotion.",
            "",
        ]
    else:
        lines += [
            "## VERDICT",
            "",
            "**VALIDATION FAILED**",
            "",
            "Failing metrics:",
        ]
        for key, label, fmt, check in metrics_to_show:
            for pair in PAIRS:
                val = pair_results[pair]["metrics"].get(key, float("nan"))
                if not check(val):
                    lines.append(f"  - {pair} {label}: {fmt(val)} (threshold: {key})")
        if not port_pass:
            lines.append(f"  - Portfolio Sharpe: {port_sharpe:.3f} (threshold: > {PASS_PORTFOLIO_SHARPE})")
        lines += [
            "",
            "Do NOT paper trade. Review signal frequency, stop parameters, or",
            "ADX thresholds (if signal frequency < 5%, lower to 22/18).",
        ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report written to {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("v2.4 Walk-Forward Validation: USD_JPY + USD_CHF H4")
    logger.info("=" * 70)

    cost_model = CostModel()   # same conservative spreads as v2.3
    backtester = VectorizedBacktester(initial_equity=INITIAL_EQUITY, cost_model=cost_model)

    # ── 1. Load data and build features ───────────────────────────────────────
    feature_dfs: dict[str, pd.DataFrame] = {}
    for pair in PAIRS:
        logger.info(f"Loading {pair} H4 + D raw data...")
        try:
            h4_raw = load_raw(pair, "H4")
            d_raw  = load_raw(pair, "D")
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)

        logger.info(f"Building features for {pair}...")
        feature_dfs[pair] = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair} feature matrix: {len(feature_dfs[pair])} rows")

    # ── 2. Generate walk-forward signals per pair ─────────────────────────────
    signal_dfs: dict[str, pd.DataFrame] = {}
    for pair in PAIRS:
        logger.info(f"Running RegimeRouter walk-forward for {pair}...")
        signal_dfs[pair] = build_signals_walkforward(pair, feature_dfs[pair])

    # ── 3. Correlation guard at signal level ──────────────────────────────────
    logger.info("Applying correlation guard at signal level (USD_JPY/USD_CHF)...")
    signal_dfs["USD_JPY"], signal_dfs["USD_CHF"] = apply_correlation_guard_signals(
        signal_dfs["USD_JPY"], signal_dfs["USD_CHF"]
    )

    # ── 4. Run backtest for each pair ─────────────────────────────────────────
    results: dict[str, any] = {}
    for pair in PAIRS:
        logger.info(f"Backtesting {pair}...")
        result = backtester.run(pair, GRAN, signal_dfs[pair])
        results[pair] = result

        m = result.metrics
        logger.info(
            f"{pair} | Sharpe={m.get('sharpe', float('nan')):.3f} | "
            f"MaxDD={m.get('max_drawdown', float('nan')):.1%} | "
            f"CAGR={m.get('cagr', float('nan')):.1%} | "
            f"N_trades={m.get('n_trades', 0)} | "
            f"HitRate={m.get('hit_rate', float('nan')):.1%}"
        )

    # ── 5. Portfolio Sharpe ────────────────────────────────────────────────────
    port_sharpe_val = portfolio_sharpe(
        results["USD_JPY"].equity_curve,
        results["USD_CHF"].equity_curve,
    )
    logger.info(f"Portfolio combined Sharpe: {port_sharpe_val:.3f}")

    # ── 6. Check PASS criteria ────────────────────────────────────────────────
    def check_pair(pair: str) -> list[str]:
        """Return list of failing metrics for a pair (empty = all pass)."""
        m = results[pair].metrics
        failures = []
        if m.get("sharpe", -999) <= PASS_SHARPE:
            failures.append(f"Sharpe {m.get('sharpe', float('nan')):.3f} <= {PASS_SHARPE}")
        if abs(m.get("max_drawdown", -999)) >= PASS_MAX_DD:
            failures.append(f"MaxDD {m.get('max_drawdown', float('nan')):.1%} >= {PASS_MAX_DD:.0%}")
        if m.get("n_trades", 0) <= PASS_N_TRADES:
            failures.append(f"N_trades {m.get('n_trades', 0)} <= {PASS_N_TRADES}")
        if m.get("hit_rate", 0) <= PASS_HIT_RATE:
            failures.append(f"HitRate {m.get('hit_rate', float('nan')):.1%} <= {PASS_HIT_RATE:.0%}")
        return failures

    pair_failures: dict[str, list[str]] = {}
    for pair in PAIRS:
        pair_failures[pair] = check_pair(pair)

    port_pass = port_sharpe_val > PASS_PORTFOLIO_SHARPE

    all_pass = (
        all(len(f) == 0 for f in pair_failures.values())
        and port_pass
    )

    # ── 7. Build pair_results for report ──────────────────────────────────────
    pair_results_for_report: dict[str, dict] = {
        pair: {"metrics": results[pair].metrics} for pair in PAIRS
    }

    # ── 8. Write report ───────────────────────────────────────────────────────
    output_path = PROJECT_ROOT / "results" / "v2.4_validation.md"
    write_report(pair_results_for_report, port_sharpe_val, all_pass, output_path)

    # ── 9. Final verdict ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    if all_pass:
        print("READY FOR PAPER TRADING — USD_JPY + USD_CHF")
        print(f"Report: {output_path}")
    else:
        print("VALIDATION FAILED")
        for pair in PAIRS:
            if pair_failures[pair]:
                for failure in pair_failures[pair]:
                    print(f"  {pair}: {failure}")
        if not port_pass:
            print(f"  Portfolio Sharpe {port_sharpe_val:.3f} <= {PASS_PORTFOLIO_SHARPE}")
        print(f"\nSee full report: {output_path}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
