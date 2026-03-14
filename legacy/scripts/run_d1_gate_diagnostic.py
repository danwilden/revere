"""
CHANGE 10 (v2.5): D1 gate mode diagnostic for USD_JPY H4.

Runs three parallel walk-forward backtests with the same data and parameters,
varying only the d1_gate_mode:

    "full"        — original v2.4 behaviour: D1 gate vetoes all contradicting
                    TRENDING/BREAKOUT signals, including ongoing positions.
    "entry_only"  — CHANGE 10 default: gate checked only at signal initiation;
                    open trades are not closed if D1 regime flips mid-trade.
    "disabled"    — no gate at all; all TRENDING/BREAKOUT signals pass.

Metrics compared per mode:
    - N signals generated (before and after gate)
    - N trades executed
    - Sharpe, Max DD, Hit rate
    - Signal-exit hit rate (win% for signal_exit trades specifically)
    - signal_exit_n vs stop_hit_n breakdown

Output: results/d1_gate_diagnostic.md

Run from project root:
    python scripts/run_d1_gate_diagnostic.py

Prerequisites:
    - Cached H4 and D raw parquet files in data/raw/ (from notebooks/01_data_pull)
    - pip install -e . with .env configured
"""

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_system.backtest.costs import CostModel
from forex_system.backtest.engine import VectorizedBacktester, PERIODS_PER_YEAR
from forex_system.config import settings
from forex_system.features.builders import FeaturePipeline
from forex_system.strategy.signals import RegimeRouter

# ── Parameters (match validate_v2.4.py) ───────────────────────────────────────

INSTRUMENT    = "USD_JPY"
GRAN          = "H4"
N_FOLDS       = 5
INITIAL_EQUITY = 10_000.0
RISK_PCT      = 0.005
PIP_SIZE      = 0.01   # JPY pair

D1_GATE_MODES = ["full", "entry_only", "disabled"]


# ── Data loading (identical to validate_v2.4.py) ──────────────────────────────

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


# ── Walk-forward signals with a specific router ────────────────────────────────

def build_signals_walkforward(
    instrument: str,
    feat_df: pd.DataFrame,
    router: RegimeRouter,
    n_folds: int = N_FOLDS,
    equity: float = INITIAL_EQUITY,
) -> pd.DataFrame:
    n = len(feat_df)
    fold_size = n // n_folds
    parts = []

    for fold_idx in range(n_folds):
        start = fold_idx * fold_size
        end   = (fold_idx + 1) * fold_size if fold_idx < n_folds - 1 else n
        fold_df = feat_df.iloc[start:end]
        if len(fold_df) < 100:
            continue
        sig_df = router.route(
            fold_df, instrument=instrument,
            pip_size=PIP_SIZE, equity=equity, risk_pct=RISK_PCT,
        )
        parts.append(sig_df)

    if not parts:
        raise RuntimeError(f"No valid folds generated for {instrument}")
    return pd.concat(parts)


# ── Per-mode metrics ───────────────────────────────────────────────────────────

def compute_mode_metrics(result, signal_df: pd.DataFrame) -> dict:
    trades_df = result.trades_df()
    n_signals  = int((signal_df["signal"] != 0).sum())
    n_trades   = len(trades_df)

    if trades_df.empty:
        return dict(
            n_signals=n_signals, n_trades=0,
            sharpe=float("nan"), max_drawdown=float("nan"),
            hit_rate=float("nan"), signal_exit_hit_rate=float("nan"),
            signal_exit_n=0, stop_hit_n=0,
        )

    signal_exits = trades_df[trades_df["exit_reason"] == "signal_exit"]
    stop_hits    = trades_df[trades_df["exit_reason"] == "stop_hit"]

    signal_exit_hit_rate = (
        float((signal_exits["pnl_pips"] > 0).sum() / len(signal_exits))
        if len(signal_exits) > 0 else float("nan")
    )

    return dict(
        n_signals         = n_signals,
        n_trades          = n_trades,
        sharpe            = result.metrics.get("sharpe",       float("nan")),
        max_drawdown      = result.metrics.get("max_drawdown", float("nan")),
        hit_rate          = result.metrics.get("hit_rate",     float("nan")),
        signal_exit_hit_rate = signal_exit_hit_rate,
        signal_exit_n     = len(signal_exits),
        stop_hit_n        = len(stop_hits),
    )


# ── Report writer ──────────────────────────────────────────────────────────────

def _fmt_pct(v: float) -> str:
    return f"{v:.1%}" if v == v else "n/a"   # nan-safe

def _fmt_f(v: float, dp: int = 3) -> str:
    return f"{v:.{dp}f}" if v == v else "n/a"

def _fmt_i(v) -> str:
    return str(int(v)) if v == v else "n/a"


def write_diagnostic_report(
    mode_metrics: dict[str, dict],
    output_path: Path,
) -> None:
    modes = D1_GATE_MODES
    rows: list[tuple[str, str]] = [
        ("N signals",               "n_signals"),
        ("N trades",                "n_trades"),
        ("Sharpe",                  "sharpe"),
        ("Max Drawdown",            "max_drawdown"),
        ("Overall hit rate",        "hit_rate"),
        ("Signal-exit hit rate",    "signal_exit_hit_rate"),
        ("Signal exits (n)",        "signal_exit_n"),
        ("Stop hits (n)",           "stop_hit_n"),
    ]

    # Format each cell
    def fmt(label: str, key: str, mode: str) -> str:
        v = mode_metrics[mode].get(key, float("nan"))
        if key in ("n_signals", "n_trades", "signal_exit_n", "stop_hit_n"):
            return _fmt_i(v)
        if key in ("max_drawdown", "hit_rate", "signal_exit_hit_rate"):
            return _fmt_pct(v)
        return _fmt_f(v)

    header   = "| Metric | full | entry_only | disabled |"
    divider  = "|--------|------|------------|----------|"
    table_rows = [header, divider]
    for label, key in rows:
        cells = " | ".join(fmt(label, key, m) for m in modes)
        table_rows.append(f"| {label} | {cells} |")

    # Auto recommendation
    sharpes = {m: mode_metrics[m].get("sharpe", float("nan")) for m in modes}
    best_mode = max((m for m in modes if sharpes[m] == sharpes[m]),
                    key=lambda m: sharpes[m], default="entry_only")

    entry_only_trades = mode_metrics["entry_only"].get("n_trades", 0)
    full_trades       = mode_metrics["full"].get("n_trades", 0)
    trades_gained     = entry_only_trades - full_trades

    if best_mode == "disabled":
        recommendation = (
            "disabled mode has highest Sharpe — investigate whether the D1 gate "
            "is systematically filtering good trades. Consider running on USD_CHF before removing."
        )
    elif best_mode == "entry_only" or (
        sharpes["entry_only"] >= sharpes["full"] - 0.05 and trades_gained > 0
    ):
        recommendation = (
            f"ADOPT entry_only — comparable or better Sharpe to full "
            f"({sharpes['entry_only']:.3f} vs {sharpes['full']:.3f}) with "
            f"{trades_gained:+d} additional trades from not closing on D1 flips."
        )
    else:
        recommendation = (
            f"KEEP full — full mode outperforms entry_only "
            f"({sharpes['full']:.3f} vs {sharpes['entry_only']:.3f}). "
            "D1 gate is doing useful work on ongoing positions."
        )

    lines = [
        "# D1 Gate Diagnostic — USD_JPY H4",
        "",
        "## Configuration",
        f"- Instrument: {INSTRUMENT}",
        f"- Granularity: {GRAN}",
        f"- Walk-forward folds: {N_FOLDS}",
        f"- Risk per trade: {RISK_PCT*100:.1f}% equity",
        "- Hysteresis: enabled (CHANGE 8)",
        "- Stop cap: 2.5×ATR (CHANGE 9)",
        "",
        "## Results",
        "",
        *table_rows,
        "",
        "## Signal-exit breakdown",
        "",
        "Signal-exit hit rate measures the quality of rule-based exits (strategy signal flips).",
        "A low hit rate means signal-exits are mostly premature losers — the minimum hold (CHANGE 8)",
        "and entry-only gate (CHANGE 10) should both improve this.",
        "",
        "## Recommendation",
        "",
        recommendation,
        "",
        "---",
        "*Generated by scripts/run_d1_gate_diagnostic.py*",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Diagnostic report written to {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info(f"D1 Gate Diagnostic: {INSTRUMENT} {GRAN}")
    logger.info(f"Modes: {D1_GATE_MODES}")
    logger.info("=" * 70)

    cost_model = CostModel()
    backtester = VectorizedBacktester(initial_equity=INITIAL_EQUITY, cost_model=cost_model)

    # Load data once
    logger.info(f"Loading {INSTRUMENT} H4 + D raw data...")
    try:
        h4_raw = load_raw(INSTRUMENT, "H4")
        d_raw  = load_raw(INSTRUMENT, "D")
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info(f"Building features for {INSTRUMENT}...")
    feat_df = build_features(INSTRUMENT, h4_raw, d_raw)
    logger.info(f"Feature matrix: {len(feat_df)} rows")

    # Run one backtest per mode
    mode_metrics: dict[str, dict] = {}
    for mode in D1_GATE_MODES:
        logger.info(f"\n--- d1_gate_mode={mode!r} ---")
        router    = RegimeRouter(d1_gate_mode=mode)
        signal_df = build_signals_walkforward(INSTRUMENT, feat_df, router)
        result    = backtester.run(INSTRUMENT, GRAN, signal_df)
        metrics   = compute_mode_metrics(result, signal_df)
        mode_metrics[mode] = metrics

        m = metrics
        logger.info(
            f"{mode}: signals={m['n_signals']} | trades={m['n_trades']} | "
            f"sharpe={_fmt_f(m['sharpe'])} | maxDD={_fmt_pct(m['max_drawdown'])} | "
            f"hit_rate={_fmt_pct(m['hit_rate'])} | "
            f"sig_exit_hit_rate={_fmt_pct(m['signal_exit_hit_rate'])} "
            f"({m['signal_exit_n']} signal_exits, {m['stop_hit_n']} stop_hits)"
        )

    # Write report
    output_path = PROJECT_ROOT / "results" / "d1_gate_diagnostic.md"
    write_diagnostic_report(mode_metrics, output_path)

    # Print summary table to stdout
    print("\n" + "=" * 70)
    print(f"D1 Gate Diagnostic — {INSTRUMENT} {GRAN}")
    print("=" * 70)
    print(f"{'Mode':<14} {'Signals':>8} {'Trades':>7} {'Sharpe':>8} {'MaxDD':>8} {'HitRate':>9} {'SigExitHR':>10}")
    print("-" * 70)
    for mode in D1_GATE_MODES:
        m = mode_metrics[mode]
        print(
            f"{mode:<14} "
            f"{_fmt_i(m['n_signals']):>8} "
            f"{_fmt_i(m['n_trades']):>7} "
            f"{_fmt_f(m['sharpe']):>8} "
            f"{_fmt_pct(m['max_drawdown']):>8} "
            f"{_fmt_pct(m['hit_rate']):>9} "
            f"{_fmt_pct(m['signal_exit_hit_rate']):>10}"
        )
    print("=" * 70)
    print(f"\nFull report: {output_path}\n")


if __name__ == "__main__":
    main()
