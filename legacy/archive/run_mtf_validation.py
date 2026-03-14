"""
Phase 1E: Multi-Timeframe Walk-Forward Validation — USD_JPY + USD_CHF.

H4 signals (RegimeRouter, unchanged from v2.4/v2.5) with H1 trade management
via MultiTimeframeBacktester. Compare results to single-timeframe baseline.

Run from project root:
    python scripts/run_mtf_validation.py

Prerequisites:
    - Cached H4, H1, and D raw parquet files in data/raw/ (from notebooks/01_data_pull)
    - pip install -e . with .env configured

PASS criteria (identical to validate_v2.4.py):
    - Both pairs: Sharpe > 0.5
    - Both pairs: Max drawdown < 12%
    - Both pairs: N_trades > 150
    - Both pairs: Hit rate > 34%
    - Portfolio combined Sharpe > 0.6
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import PERIODS_PER_YEAR
from forex_system.backtest.mtf_engine import MultiTimeframeBacktester
from forex_system.backtest.metrics import full_tearsheet
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.strategy.signals import RegimeRouter

# ── Validation parameters ───────────────────────────────────────────────────────

PAIRS          = ["USD_JPY", "USD_CHF"]
N_FOLDS        = 5
INITIAL_EQUITY = 10_000.0
RISK_PCT       = 0.005

PIP_SIZES = {"USD_JPY": 0.01, "USD_CHF": 0.0001}

# MTF-specific trade management params (per pivot plan)
PARTIAL_EXIT_ATR_MULT = 1.5
PARTIAL_EXIT_FRACTION = 0.33
TRAIL_ATR_MULT        = 1.5

# PASS criteria (same as v2.4)
PASS_SHARPE           = 0.5
PASS_MAX_DD           = 0.12
PASS_N_TRADES         = 150
PASS_HIT_RATE         = 0.34
PASS_PORTFOLIO_SHARPE = 0.6


# ── Data loading ─────────────────────────────────────────────────────────────────


def load_raw(instrument: str, granularity: str) -> pd.DataFrame:
    """Load cached raw OHLCV parquet from data/raw/."""
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
    """Build feature matrix from H4 + D data (same as validate_v2.4.py)."""
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


# ── Walk-forward signal generation ───────────────────────────────────────────────


def build_signals_walkforward(
    instrument: str,
    feat_df: pd.DataFrame,
    n_folds: int = N_FOLDS,
    equity: float = INITIAL_EQUITY,
) -> pd.DataFrame:
    """
    Generate H4 signals via RegimeRouter across N sequential OOS folds.
    Identical to validate_v2.4.py — signal path is unchanged for MTF.
    """
    pip_size = PIP_SIZES.get(instrument, 0.0001)
    router = RegimeRouter()

    n = len(feat_df)
    fold_size = n // n_folds
    all_signal_dfs = []

    for fold_idx in range(n_folds):
        start_idx = fold_idx * fold_size
        end_idx = (fold_idx + 1) * fold_size if fold_idx < n_folds - 1 else n
        fold_df = feat_df.iloc[start_idx:end_idx]

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


# ── Correlation guard ─────────────────────────────────────────────────────────────


def apply_correlation_guard_signals(
    signal_jpydf: pd.DataFrame,
    signal_chfdf: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Same-direction USD_JPY + USD_CHF simultaneous signals → reject USD_CHF.
    Mirrors validate_v2.4.py exactly.
    """
    shared_idx = signal_jpydf.index.intersection(signal_chfdf.index)
    jpy_sig = signal_jpydf["signal"].reindex(shared_idx)
    chf_sig = signal_chfdf["signal"].reindex(shared_idx)

    both_active = (jpy_sig != 0) & (chf_sig != 0)
    same_direction = jpy_sig == chf_sig
    rejected_mask = both_active & same_direction

    n_rejected = int(rejected_mask.sum())
    if n_rejected > 0:
        logger.info(
            f"Correlation guard: rejected {n_rejected} USD_CHF signals "
            "(same-direction as simultaneous USD_JPY)"
        )

    chf_out = signal_chfdf.copy()
    chf_out.loc[rejected_mask, "signal"] = 0
    chf_out.loc[rejected_mask, "units"] = 0
    return signal_jpydf.copy(), chf_out


# ── Portfolio Sharpe ──────────────────────────────────────────────────────────────


def portfolio_sharpe(
    eq_jpy: pd.Series,
    eq_chf: pd.Series,
    ppy: int = PERIODS_PER_YEAR["H4"],
) -> float:
    """Equal-weight portfolio Sharpe from two H4-resolution equity curves."""
    ret_jpy = eq_jpy.pct_change().dropna()
    ret_chf = eq_chf.pct_change().dropna()
    combined = pd.concat([ret_jpy, ret_chf], axis=1).dropna()
    if combined.empty:
        return float("nan")
    port_returns = combined.mean(axis=1)
    if port_returns.std() == 0:
        return float("nan")
    return float(port_returns.mean() / port_returns.std() * np.sqrt(ppy))


# ── Load baseline results for comparison ──────────────────────────────────────────


def load_baseline_metrics(pairs: list[str]) -> dict[str, dict]:
    """
    Attempt to parse key metrics from results/v2.4_validation.md for comparison.
    Returns empty dicts if file not found or parse fails.
    """
    baseline_path = PROJECT_ROOT / "results" / "v2.4_validation.md"
    baseline: dict[str, dict] = {p: {} for p in pairs}
    if not baseline_path.exists():
        return baseline

    try:
        text = baseline_path.read_text()
        for line in text.splitlines():
            if line.startswith("| Sharpe |"):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                # parts: ['Sharpe', jpy_val, chf_val, 'PASS ✓' or 'FAIL ✗']
                if len(parts) >= 3:
                    for i, pair in enumerate(pairs):
                        try:
                            baseline[pair]["sharpe"] = float(parts[1 + i])
                        except (ValueError, IndexError):
                            pass
            elif line.startswith("| Max DD |"):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    for i, pair in enumerate(pairs):
                        try:
                            val = parts[1 + i].rstrip("%")
                            baseline[pair]["max_drawdown"] = -abs(float(val)) / 100.0
                        except (ValueError, IndexError):
                            pass
    except Exception as exc:
        logger.warning(f"Could not parse baseline metrics: {exc}")

    return baseline


# ── Report writing ────────────────────────────────────────────────────────────────


def write_report(
    pair_results: dict,
    port_sharpe: float,
    all_pass: bool,
    baseline: dict[str, dict],
    output_path: Path,
) -> None:
    """Write v2.4b_phase1_mtf.md."""
    lines = [
        "# Phase 1E — Multi-Timeframe Validation (v2.4b)",
        "",
        "## System",
        "- Signal: H4 RegimeRouter (unchanged from v2.4/v2.5)",
        "- Trade management: H1 bars (partial exit, trail, stop checks)",
        f"- Partial exit: +{PARTIAL_EXIT_ATR_MULT}×H4_ATR profit → close {PARTIAL_EXIT_FRACTION:.0%} of position",
        f"- Trail: {TRAIL_ATR_MULT}×H4_ATR distance (H4 ATR fixed at entry)",
        "- ATR reference: H4 ATR at entry — never recalculated during trade",
        "- Bar alignment: H1 bars within H4 entry bar excluded (no lookahead)",
        f"- Walk-forward folds: {N_FOLDS}",
        f"- Risk per trade: {RISK_PCT*100:.1f}% equity",
        "- Correlation guard: same-direction USD_JPY + USD_CHF rejected",
        "",
        "## Pass Criteria",
        "| Metric | Threshold |",
        "|--------|-----------|",
        f"| Sharpe (per pair) | > {PASS_SHARPE} |",
        f"| Max Drawdown | < {PASS_MAX_DD*100:.0f}% |",
        f"| N Trades | > {PASS_N_TRADES} |",
        f"| Hit Rate | > {PASS_HIT_RATE*100:.0f}% |",
        f"| Portfolio Sharpe | > {PASS_PORTFOLIO_SHARPE} |",
        "",
        "## Per-Pair Results",
        "",
        "| Metric | USD_JPY (MTF) | USD_CHF (MTF) | Baseline USD_JPY | Baseline USD_CHF | Pass? |",
        "|--------|---------------|---------------|------------------|------------------|-------|",
    ]

    has_baseline = any(baseline[p] for p in PAIRS)

    metrics_to_show = [
        ("sharpe",          "Sharpe",    lambda v: f"{v:.3f}",  lambda v: v > PASS_SHARPE),
        ("max_drawdown",    "Max DD",    lambda v: f"{v:.1%}",  lambda v: abs(v) < PASS_MAX_DD),
        ("n_trades",        "N Trades",  lambda v: str(int(v)), lambda v: v > PASS_N_TRADES),
        ("hit_rate",        "Hit Rate",  lambda v: f"{v:.1%}",  lambda v: v > PASS_HIT_RATE),
        ("avg_win_pips",    "Avg Win",   lambda v: f"{v:.1f}p", lambda _: True),
        ("avg_loss_pips",   "Avg Loss",  lambda v: f"{v:.1f}p", lambda _: True),
        ("payoff_ratio",    "Payoff R",  lambda v: f"{v:.2f}",  lambda _: True),
        ("cagr",            "CAGR",      lambda v: f"{v:.1%}",  lambda _: True),
        ("avg_h1_bars_in_trade", "H1 bars/trade", lambda v: f"{v:.1f}", lambda _: True),
    ]

    for key, label, fmt, check in metrics_to_show:
        jpy_val = pair_results["USD_JPY"]["metrics"].get(key, float("nan"))
        chf_val = pair_results["USD_CHF"]["metrics"].get(key, float("nan"))

        jpy_base = baseline["USD_JPY"].get(key, float("nan"))
        chf_base = baseline["USD_CHF"].get(key, float("nan"))

        def safe_fmt(v, formatter):
            if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                return "—"
            return formatter(v)

        both_pass = check(jpy_val) and check(chf_val)
        pass_str = "PASS ✓" if both_pass else ("—" if not (check.__doc__ or True) else "FAIL ✗")
        # Simpler: only show PASS/FAIL for the 4 gate metrics
        gate_metrics = {"sharpe", "max_drawdown", "n_trades", "hit_rate"}
        if key in gate_metrics:
            pass_str = "PASS ✓" if both_pass else "FAIL ✗"
        else:
            pass_str = ""

        base_jpy_str = safe_fmt(jpy_base, fmt) if has_baseline else "—"
        base_chf_str = safe_fmt(chf_base, fmt) if has_baseline else "—"

        lines.append(
            f"| {label} | {safe_fmt(jpy_val, fmt)} | {safe_fmt(chf_val, fmt)} "
            f"| {base_jpy_str} | {base_chf_str} | {pass_str} |"
        )

    port_pass = port_sharpe > PASS_PORTFOLIO_SHARPE
    lines += [
        "",
        "## Portfolio",
        "| Metric | Value | Pass? |",
        "|--------|-------|-------|",
        f"| Portfolio Sharpe (MTF) | {port_sharpe:.3f} | {'PASS ✓' if port_pass else 'FAIL ✗'} |",
        "",
        "## Trade Distribution",
        "",
    ]

    for pair in PAIRS:
        trades_df = pair_results[pair].get("trades_df")
        if trades_df is not None and not trades_df.empty and "exit_reason" in trades_df.columns:
            n = len(trades_df)
            partial_n = int((trades_df["exit_reason"] == "partial_tp").sum())
            stop_n = int((trades_df["exit_reason"] == "stop_hit").sum())
            signal_n = int((trades_df["exit_reason"] == "signal_exit").sum())
            eod_n = int((trades_df["exit_reason"] == "end_of_data").sum())
            lines += [
                f"**{pair}** (n={n} trade records):",
                f"- partial_tp: {partial_n} ({partial_n/n:.0%})",
                f"- stop_hit:   {stop_n} ({stop_n/n:.0%})",
                f"- signal_exit: {signal_n} ({signal_n/n:.0%})",
                f"- end_of_data: {eod_n} ({eod_n/n:.0%})",
                "",
            ]

    if all_pass:
        lines += [
            "## VERDICT",
            "",
            "**MTF VALIDATION PASSED — USD_JPY + USD_CHF**",
            "",
            "H1 trade management improves exit quality. Compare avg_win_pips and "
            "payoff_ratio to single-timeframe baseline.",
            "",
        ]
    else:
        lines += [
            "## VERDICT",
            "",
            "**MTF VALIDATION FAILED**",
            "",
            "Failing metrics:",
        ]
        for key, label, fmt, check in metrics_to_show[:4]:   # gate metrics only
            for pair in PAIRS:
                val = pair_results[pair]["metrics"].get(key, float("nan"))
                if not check(val):
                    lines.append(f"  - {pair} {label}: {fmt(val)}")
        if not port_pass:
            lines.append(f"  - Portfolio Sharpe: {port_sharpe:.3f} (need > {PASS_PORTFOLIO_SHARPE})")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Report written to {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("=" * 70)
    logger.info("Phase 1E: Multi-Timeframe Validation — USD_JPY + USD_CHF")
    logger.info("=" * 70)

    cost_model = CostModel()
    backtester = MultiTimeframeBacktester(
        initial_equity=INITIAL_EQUITY,
        cost_model=cost_model,
        partial_exit_atr_mult=PARTIAL_EXIT_ATR_MULT,
        partial_exit_fraction=PARTIAL_EXIT_FRACTION,
        trail_atr_mult=TRAIL_ATR_MULT,
    )

    # ── 1. Load data and build H4 features ────────────────────────────────────
    feature_dfs: dict[str, pd.DataFrame] = {}
    h1_dfs: dict[str, pd.DataFrame] = {}

    for pair in PAIRS:
        logger.info(f"Loading {pair} H4 + H1 + D raw data...")
        try:
            h4_raw = load_raw(pair, "H4")
            h1_raw = load_raw(pair, "H1")
            d_raw  = load_raw(pair, "D")
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)

        logger.info(f"Building H4 features for {pair}...")
        feature_dfs[pair] = build_features(pair, h4_raw, d_raw)
        h1_dfs[pair] = h1_raw
        logger.info(
            f"{pair}: {len(feature_dfs[pair])} H4 bars, {len(h1_raw)} H1 bars"
        )

    # ── 2. Generate walk-forward H4 signals ───────────────────────────────────
    signal_dfs: dict[str, pd.DataFrame] = {}
    for pair in PAIRS:
        logger.info(f"Running RegimeRouter walk-forward for {pair}...")
        signal_dfs[pair] = build_signals_walkforward(pair, feature_dfs[pair])

    # ── 3. Correlation guard ───────────────────────────────────────────────────
    logger.info("Applying correlation guard (USD_JPY/USD_CHF)...")
    signal_dfs["USD_JPY"], signal_dfs["USD_CHF"] = apply_correlation_guard_signals(
        signal_dfs["USD_JPY"], signal_dfs["USD_CHF"]
    )

    # ── 4. Run MTF backtest for each pair ──────────────────────────────────────
    results: dict[str, object] = {}
    for pair in PAIRS:
        logger.info(f"Running MTF backtest for {pair}...")
        result = backtester.run(pair, signal_dfs[pair], h1_dfs[pair])
        results[pair] = result

        m = result.metrics
        logger.info(
            f"{pair} | Sharpe={m.get('sharpe', float('nan')):.3f} | "
            f"MaxDD={m.get('max_drawdown', float('nan')):.1%} | "
            f"CAGR={m.get('cagr', float('nan')):.1%} | "
            f"N_trades={m.get('n_trades', 0)} | "
            f"HitRate={m.get('hit_rate', float('nan')):.1%} | "
            f"AvgWin={m.get('avg_win_pips', float('nan')):.1f}p | "
            f"AvgLoss={m.get('avg_loss_pips', float('nan')):.1f}p | "
            f"AvgH1bars={m.get('avg_h1_bars_in_trade', float('nan')):.1f}"
        )

    # ── 5. Portfolio Sharpe ────────────────────────────────────────────────────
    port_sharpe_val = portfolio_sharpe(
        results["USD_JPY"].equity_curve,
        results["USD_CHF"].equity_curve,
    )
    logger.info(f"Portfolio combined Sharpe (MTF): {port_sharpe_val:.3f}")

    # ── 6. Check PASS criteria ─────────────────────────────────────────────────
    def check_pair(pair: str) -> list[str]:
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

    pair_failures: dict[str, list[str]] = {p: check_pair(p) for p in PAIRS}
    port_pass = port_sharpe_val > PASS_PORTFOLIO_SHARPE
    all_pass = all(len(f) == 0 for f in pair_failures.values()) and port_pass

    # ── 7. Load baseline for comparison ───────────────────────────────────────
    baseline = load_baseline_metrics(PAIRS)

    # ── 8. Build pair_results for report ──────────────────────────────────────
    pair_results_for_report = {
        pair: {
            "metrics": results[pair].metrics,
            "trades_df": results[pair].trades_df(),
        }
        for pair in PAIRS
    }

    # ── 9. Write report ────────────────────────────────────────────────────────
    output_path = PROJECT_ROOT / "results" / "v2.4b_phase1_mtf.md"
    write_report(pair_results_for_report, port_sharpe_val, all_pass, baseline, output_path)

    # ── 10. Final verdict ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    if all_pass:
        print("MTF VALIDATION PASSED — USD_JPY + USD_CHF")
    else:
        print("MTF VALIDATION FAILED")
        for pair in PAIRS:
            for failure in pair_failures[pair]:
                print(f"  {pair}: {failure}")
        if not port_pass:
            print(f"  Portfolio Sharpe {port_sharpe_val:.3f} <= {PASS_PORTFOLIO_SHARPE}")
    print(f"\nReport: {output_path}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
