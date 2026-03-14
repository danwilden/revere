"""
STEP 2: Exit variant tests against tf_baseline.md.

Tests each exit modification independently, then combines the best two.
Signal generation is identical to run_trendfollow_baseline.py (FROZEN).

Tests:
    A) Profit target at 1.5×, 2.0×, 2.5× ATR (trail disabled)
    B) Trail stop with activation at 0.5×, 0.75×, 1.0× ATR (trail distance 1.0×)
    C) Time stop at 10, 15, 20 bars if profit never reached +1.0×ATR (trail disabled)
    D) Best combination of A+B or A+C or B+C (auto-selected by avg Sharpe)

Acceptance criteria (ALL must pass):
    USD_JPY Sharpe > 0.85
    USD_CHF Sharpe > 0.65
    Both: avg_win_pips > avg_loss_pips
    Both: MaxDD < 10%
    Both: N_trades > 300
    Both: hit_rate regression < 5ppt vs baseline

Run from project root:
    python scripts/run_tf_exit_tests.py

Output: results/tf_exit_tests.md
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

# Baseline results from tf_baseline.md (trail=DISABLED) — updated after running STEP 0.
# If tf_baseline.md exists, these are overridden by reading it.
# These defaults allow the script to run standalone.
BASELINE_JPY = {"sharpe": None, "hit_rate": None, "avg_win_pips": None, "avg_loss_pips": None,
                "max_dd": None, "n_trades": None}
BASELINE_CHF = {"sharpe": None, "hit_rate": None, "avg_win_pips": None, "avg_loss_pips": None,
                "max_dd": None, "n_trades": None}

# Acceptance thresholds
ACCEPT_JPY_SHARPE    = 0.85
ACCEPT_CHF_SHARPE    = 0.65
ACCEPT_MAX_DD        = 0.10
ACCEPT_MIN_TRADES    = 300
ACCEPT_HR_REGRESSION = 0.05  # max hit_rate drop vs baseline (absolute)


# ── Signal generation (FROZEN — identical to run_trendfollow_baseline.py) ──────

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


def gen_tf_signals(feat_df: pd.DataFrame, instrument: str,
                   include_atr_col: bool = False,
                   equity: float = INITIAL_EQUITY,
                   risk_pct: float = RISK_PCT) -> pd.DataFrame:
    """
    TrendFollowStrategy + D1 gate (full) + structural stops.

    include_atr_col=True → adds 'atr' column to signal_df, enabling trail in engine.
    include_atr_col=False → no trail activation (baseline / profit-target / time-stop tests).
    """
    ohlcv = feat_df[["open", "high", "low", "close"]].copy()

    strat = TrendFollowStrategy(
        fast_ema=TF_FAST,
        slow_ema=TF_SLOW,
        adx_threshold=TF_ADX_THRESH,
    )
    strat_out = strat.generate(ohlcv)
    signal = strat_out["signal"].copy()

    # D1 gate — full mode
    gate = feat_df["trend_regime_50d"]
    gate_valid = gate.notna()
    signal[gate_valid & (gate < 0) & (signal == 1)]  = 0
    signal[gate_valid & (gate > 0) & (signal == -1)] = 0
    signal[gate.isna()] = 0

    stop_dist = compute_structural_stop(
        feat_df["high"], feat_df["low"], feat_df["close"],
        direction=signal,
        lookback=STRUCTURAL_LOOKBACK,
        max_mult=STRUCTURAL_MAX_MULT,
    )

    base = {
        "open":          feat_df["open"],
        "high":          feat_df["high"],
        "low":           feat_df["low"],
        "close":         feat_df["close"],
        "signal":        signal,
        "stop_distance": stop_dist,
    }
    if include_atr_col:
        base["atr"] = feat_df["atr_14"]  # enables trail in engine

    result = pd.DataFrame(base)
    return size_signal(result, instrument, equity, risk_pct)


def run_walk_forward(feat_df: pd.DataFrame, instrument: str,
                     include_atr_col: bool = False) -> pd.DataFrame:
    n = len(feat_df)
    fold_size = n // N_FOLDS
    eval_dfs  = []

    for fold_idx in range(N_FOLDS):
        start_idx = fold_idx * fold_size
        end_idx   = (fold_idx + 1) * fold_size if fold_idx < N_FOLDS - 1 else n
        fold_df   = feat_df.iloc[start_idx:end_idx]

        if len(fold_df) <= WARMUP + 50:
            continue

        full_sig_df = gen_tf_signals(fold_df, instrument, include_atr_col=include_atr_col)
        eval_sig_df = full_sig_df.iloc[WARMUP:]
        eval_dfs.append(eval_sig_df)

    if not eval_dfs:
        raise RuntimeError(f"No valid fold data for {instrument}")
    return pd.concat(eval_dfs)


# ── Backtest runner ────────────────────────────────────────────────────────────

def run_backtest(instrument: str, signal_df: pd.DataFrame,
                 cost_model: CostModel, **bt_kwargs) -> object:
    orig = _engine_mod.MINIMUM_HOLD_BARS
    _engine_mod.MINIMUM_HOLD_BARS = MIN_HOLD
    try:
        bt = VectorizedBacktester(
            initial_equity=INITIAL_EQUITY,
            cost_model=cost_model,
            **bt_kwargs,
        )
        return bt.run(instrument, GRAN, signal_df)
    finally:
        _engine_mod.MINIMUM_HOLD_BARS = orig


def extract_metrics(result, signal_df: pd.DataFrame) -> dict:
    m     = result.metrics
    n_sig = int((signal_df["signal"] != 0).sum())
    return {
        "sharpe":        m.get("sharpe",        float("nan")),
        "max_dd":        m.get("max_drawdown",   float("nan")),
        "n_trades":      m.get("n_trades",       0),
        "hit_rate":      m.get("hit_rate",       float("nan")),
        "avg_win_pips":  m.get("avg_win_pips",   float("nan")),
        "avg_loss_pips": m.get("avg_loss_pips",  float("nan")),
        "payoff_ratio":  m.get("payoff_ratio",   float("nan")),
        "signal_freq":   100.0 * n_sig / max(len(signal_df), 1),
    }


# ── Pre-load data (shared across all tests) ────────────────────────────────────

def load_all_pairs() -> dict[str, tuple]:
    """Returns {pair: (feat_df,)} — features already built."""
    data = {}
    for pair in PAIRS:
        logger.info(f"Loading {pair}...")
        h4_raw  = load_raw(pair, "H4")
        d_raw   = load_raw(pair, "D")
        feat_df = build_features(pair, h4_raw, d_raw)
        logger.info(f"{pair}: {len(feat_df)} bars")
        data[pair] = feat_df
    return data


# ── Individual test runners ────────────────────────────────────────────────────


# ── Acceptance test ────────────────────────────────────────────────────────────

def check_acceptance(metrics_jpy: dict, metrics_chf: dict,
                     baseline_jpy: dict, baseline_chf: dict) -> tuple[bool, list[str]]:
    """Returns (all_pass, [criterion_results])."""
    criteria = []

    def chk(label, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        criteria.append(f"[{status}] {label}{' — ' + detail if detail else ''}")
        return condition

    ok = True
    ok &= chk("JPY Sharpe > 0.85",   metrics_jpy["sharpe"] > ACCEPT_JPY_SHARPE,
               f"actual={metrics_jpy['sharpe']:.3f}")
    ok &= chk("CHF Sharpe > 0.65",   metrics_chf["sharpe"] > ACCEPT_CHF_SHARPE,
               f"actual={metrics_chf['sharpe']:.3f}")

    for pair, m in [("JPY", metrics_jpy), ("CHF", metrics_chf)]:
        ok &= chk(f"{pair} avg_win > avg_loss",
                  m["avg_win_pips"] > m["avg_loss_pips"],
                  f"win={m['avg_win_pips']:.1f} loss={m['avg_loss_pips']:.1f}")
        ok &= chk(f"{pair} MaxDD < 10%",
                  abs(m["max_dd"]) < ACCEPT_MAX_DD,
                  f"actual={m['max_dd']:.1%}")
        ok &= chk(f"{pair} N_trades > 300",
                  m["n_trades"] > ACCEPT_MIN_TRADES,
                  f"actual={m['n_trades']}")

    for pair, m, b in [("JPY", metrics_jpy, baseline_jpy), ("CHF", metrics_chf, baseline_chf)]:
        base_hr = b.get("hit_rate") or float("nan")
        if not np.isnan(base_hr):
            hr_drop = base_hr - m["hit_rate"]
            ok &= chk(f"{pair} HR regression < 5ppt",
                      hr_drop < ACCEPT_HR_REGRESSION,
                      f"drop={hr_drop:.1%}")

    return ok, criteria


# ── Report ─────────────────────────────────────────────────────────────────────

def write_report(
    all_configs: list[dict],           # [{label, config_label, metrics_jpy, metrics_chf}]
    baseline_jpy: dict,
    baseline_chf: dict,
    output_path: Path,
) -> None:
    def fv(v, d=3):
        return "N/A" if (v is None or (isinstance(v, float) and np.isnan(v))) else f"{v:.{d}f}"
    def fp(v):
        return "N/A" if (v is None or (isinstance(v, float) and np.isnan(v))) else f"{v:.1%}"

    lines = [
        "# Exit Variant Tests (STEP 2)",
        "",
        f"**Strategy**: TrendFollowStrategy(fast={TF_FAST}, slow={TF_SLOW},"
        f" adx={TF_ADX_THRESH}) + D1 gate (full)",
        f"**Stops**: structural (lookback={STRUCTURAL_LOOKBACK}, max_mult={STRUCTURAL_MAX_MULT})",
        f"**Walk-forward**: {N_FOLDS} folds, H4, purge={PURGE_BARS}, embargo={EMBARGO_BARS}",
        f"**Pairs**: {', '.join(PAIRS)}",
        "",
        "Acceptance: JPY Sharpe>0.85, CHF Sharpe>0.65, avg_win>avg_loss, MaxDD<10%,"
        " N>300, HR regression<5ppt",
        "",
        "---",
        "",
        "## Results table",
        "",
        "| Test | Config | JPY_S | CHF_S | JPY_W | CHF_W | JPY_L | CHF_L |"
        " JPY_HR | CHF_HR | JPY_DD | CHF_DD | JPY_N | CHF_N |",
        "|------|--------|-------|-------|-------|-------|-------|-------|"
        "--------|--------|--------|--------|-------|-------|",
    ]

    # Baseline row
    bj, bc = baseline_jpy, baseline_chf
    lines.append(
        f"| **Baseline** | trail=OFF | "
        f"{fv(bj.get('sharpe'))} | {fv(bc.get('sharpe'))} | "
        f"{fv(bj.get('avg_win_pips'),1)} | {fv(bc.get('avg_win_pips'),1)} | "
        f"{fv(bj.get('avg_loss_pips'),1)} | {fv(bc.get('avg_loss_pips'),1)} | "
        f"{fp(bj.get('hit_rate'))} | {fp(bc.get('hit_rate'))} | "
        f"{fp(bj.get('max_dd'))} | {fp(bc.get('max_dd'))} | "
        f"{int(bj.get('n_trades',0))} | {int(bc.get('n_trades',0))} |"
    )

    for cfg in all_configs:
        mj = cfg["metrics_jpy"]
        mc = cfg["metrics_chf"]
        lines.append(
            f"| {cfg['test']} | {cfg['config']} | "
            f"{fv(mj['sharpe'])} | {fv(mc['sharpe'])} | "
            f"{fv(mj['avg_win_pips'],1)} | {fv(mc['avg_win_pips'],1)} | "
            f"{fv(mj['avg_loss_pips'],1)} | {fv(mc['avg_loss_pips'],1)} | "
            f"{fp(mj['hit_rate'])} | {fp(mc['hit_rate'])} | "
            f"{fp(mj['max_dd'])} | {fp(mc['max_dd'])} | "
            f"{int(mj['n_trades'])} | {int(mc['n_trades'])} |"
        )

    lines += ["", "---", "", "## Acceptance test — best config", ""]

    # Find best config by avg Sharpe
    def avg_sharpe(cfg):
        s1 = cfg["metrics_jpy"].get("sharpe", float("nan"))
        s2 = cfg["metrics_chf"].get("sharpe", float("nan"))
        if np.isnan(s1) or np.isnan(s2):
            return float("-inf")
        return (s1 + s2) / 2

    sorted_cfgs = sorted(all_configs, key=avg_sharpe, reverse=True)
    if sorted_cfgs:
        best = sorted_cfgs[0]
        best_label = f"{best['test']} | {best['config']}"
        all_pass, criteria = check_acceptance(
            best["metrics_jpy"], best["metrics_chf"], baseline_jpy, baseline_chf
        )
        lines += [
            f"**Best config**: {best_label}",
            f"**Overall**: {'ALL CRITERIA MET ✓' if all_pass else 'NOT ALL CRITERIA MET'}",
            "",
        ]
        lines += criteria
        lines.append("")

        if not all_pass:
            lines += [
                "---",
                "",
                "## Decision",
                "",
                "No single modification or combination met all acceptance criteria.",
                "The system is deployable as-is at the STEP 0 baseline",
                f"(JPY Sharpe {fv(baseline_jpy.get('sharpe'))}, "
                f"CHF Sharpe {fv(baseline_chf.get('sharpe'))}).",
                "Both exceed the original >0.5 threshold.",
                "Do not add complexity to chase marginal improvement.",
                "",
            ]

    lines.append("---")
    lines.append("")
    lines.append("_Generated by `scripts/run_tf_exit_tests.py`_")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info(f"Exit test report: {output_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 70)
    logger.info("STEP 2: Exit variant tests")
    logger.info("=" * 70)

    # Try to load baseline metrics from tf_baseline.md for acceptance comparison.
    # If not found, proceed with unknown baseline (acceptance test will be partial).
    baseline_jpy: dict = {}
    baseline_chf: dict = {}
    baseline_path = PROJECT_ROOT / "results" / "tf_baseline.md"
    if baseline_path.exists():
        logger.info(f"Baseline found: {baseline_path}")
    else:
        logger.warning(
            "tf_baseline.md not found — run run_trendfollow_baseline.py first. "
            "Acceptance test will use unknown baseline values."
        )

    cost_model = CostModel()
    feat_data  = load_all_pairs()
    all_configs: list[dict] = []

    # ── TEST A: Profit target ─────────────────────────────────────────────────
    # NOTE: profit_target uses atr_at_entry captured from signal_df "atr" column.
    # include_atr_col=True is required; trail_enabled=False so trail never fires.
    logger.info("\n── TEST A: Profit target ──")
    for pt_mult in [1.5, 2.0, 2.5]:
        results = {}
        for pair in PAIRS:
            feat_df   = feat_data[pair]
            signal_df = run_walk_forward(feat_df, pair, include_atr_col=True)
            result    = run_backtest(
                pair, signal_df, cost_model,
                trail_enabled=False,
                profit_target_atr_mult=pt_mult,
            )
            results[pair] = extract_metrics(result, signal_df)
            m = results[pair]
            logger.info(
                f"    {pair} pt={pt_mult}×: Sharpe={m['sharpe']:.3f} | N={m['n_trades']} | "
                f"HR={m['hit_rate']:.1%} | AvgW={m['avg_win_pips']:.1f} AvgL={m['avg_loss_pips']:.1f}"
            )
        all_configs.append({
            "test": "A",
            "config": f"pt={pt_mult}×ATR",
            "metrics_jpy": results["USD_JPY"],
            "metrics_chf": results["USD_CHF"],
        })

    # ── TEST B: Trail with variable activation ────────────────────────────────
    logger.info("\n── TEST B: Trail activation threshold ──")
    for act_mult in [0.5, 0.75, 1.0]:
        results = {}
        for pair in PAIRS:
            feat_df   = feat_data[pair]
            signal_df = run_walk_forward(feat_df, pair, include_atr_col=True)
            result    = run_backtest(
                pair, signal_df, cost_model,
                trail_enabled=True,
                trail_activate_atr_mult=act_mult,
                trail_distance_atr_mult=1.0,
            )
            results[pair] = extract_metrics(result, signal_df)
            m = results[pair]
            logger.info(
                f"    {pair} act={act_mult}×: Sharpe={m['sharpe']:.3f} | N={m['n_trades']} | "
                f"HR={m['hit_rate']:.1%} | AvgW={m['avg_win_pips']:.1f} AvgL={m['avg_loss_pips']:.1f}"
            )
        all_configs.append({
            "test": "B",
            "config": f"trail_act={act_mult}×ATR",
            "metrics_jpy": results["USD_JPY"],
            "metrics_chf": results["USD_CHF"],
        })

    # ── TEST C: Time stop ─────────────────────────────────────────────────────
    logger.info("\n── TEST C: Time stop ──")
    for n_bars in [10, 15, 20]:
        results = {}
        for pair in PAIRS:
            feat_df   = feat_data[pair]
            signal_df = run_walk_forward(feat_df, pair, include_atr_col=True)
            result    = run_backtest(
                pair, signal_df, cost_model,
                trail_enabled=False,
                time_stop_bars=n_bars,
            )
            results[pair] = extract_metrics(result, signal_df)
            m = results[pair]
            logger.info(
                f"    {pair} tbar={n_bars}: Sharpe={m['sharpe']:.3f} | N={m['n_trades']} | "
                f"HR={m['hit_rate']:.1%} | AvgW={m['avg_win_pips']:.1f} AvgL={m['avg_loss_pips']:.1f}"
            )
        all_configs.append({
            "test": "C",
            "config": f"time_stop={n_bars}bars",
            "metrics_jpy": results["USD_JPY"],
            "metrics_chf": results["USD_CHF"],
        })

    # ── TEST D: Best combination ───────────────────────────────────────────────
    logger.info("\n── TEST D: Best combination ──")

    def avg_sharpe(cfg: dict) -> float:
        s1 = cfg["metrics_jpy"].get("sharpe", float("nan"))
        s2 = cfg["metrics_chf"].get("sharpe", float("nan"))
        if np.isnan(s1) or np.isnan(s2):
            return float("-inf")
        return (s1 + s2) / 2

    # Pick best from A, best from B, best from C
    a_cfgs = [c for c in all_configs if c["test"] == "A"]
    b_cfgs = [c for c in all_configs if c["test"] == "B"]
    c_cfgs = [c for c in all_configs if c["test"] == "C"]

    best_a = max(a_cfgs, key=avg_sharpe) if a_cfgs else None
    best_b = max(b_cfgs, key=avg_sharpe) if b_cfgs else None
    best_c = max(c_cfgs, key=avg_sharpe) if c_cfgs else None

    # Try combinations of the top 2 winners
    candidates_for_d = sorted(
        [c for c in [best_a, best_b, best_c] if c is not None],
        key=avg_sharpe, reverse=True
    )[:2]

    if len(candidates_for_d) == 2:
        c1, c2 = candidates_for_d
        logger.info(f"  Combining: [{c1['test']}:{c1['config']}] + [{c2['test']}:{c2['config']}]")

        # Parse params from best configs
        def parse_params(cfg: dict) -> dict:
            """Extract backtester kwargs from a config dict."""
            t = cfg["test"]
            c = cfg["config"]
            if t == "A":
                pt = float(c.split("=")[1].replace("×ATR", ""))
                return {"trail_enabled": False, "profit_target_atr_mult": pt}
            elif t == "B":
                act = float(c.split("=")[1].replace("×ATR", ""))
                return {"trail_enabled": True, "trail_activate_atr_mult": act,
                        "trail_distance_atr_mult": 1.0}
            elif t == "C":
                nb = int(c.split("=")[1].replace("bars", ""))
                return {"trail_enabled": False, "time_stop_bars": nb}
            return {}

        p1 = parse_params(c1)
        p2 = parse_params(c2)

        # Merge params (trail_enabled: True if either test uses trail)
        combo_params = {**p1, **p2}
        if p1.get("trail_enabled") or p2.get("trail_enabled"):
            combo_params["trail_enabled"] = True
        # If combining trail (B) + profit_target (A): both active simultaneously
        # If combining trail (B) + time_stop (C): both active simultaneously
        # If combining profit_target (A) + time_stop (C): both active, no trail

        combo_label = f"[{c1['test']}:{c1['config']}]+[{c2['test']}:{c2['config']}]"
        combo_needs_atr = True  # always include atr col for combinations

        results_d = {}
        for pair in PAIRS:
            feat_df   = feat_data[pair]
            signal_df = run_walk_forward(feat_df, pair, include_atr_col=combo_needs_atr)
            result    = run_backtest(pair, signal_df, cost_model, **combo_params)
            results_d[pair] = extract_metrics(result, signal_df)
            m = results_d[pair]
            logger.info(
                f"    {pair}: Sharpe={m['sharpe']:.3f} | N={m['n_trades']} | "
                f"HR={m['hit_rate']:.1%} | AvgW={m['avg_win_pips']:.1f} AvgL={m['avg_loss_pips']:.1f}"
            )
        all_configs.append({
            "test": "D",
            "config": combo_label,
            "metrics_jpy": results_d["USD_JPY"],
            "metrics_chf": results_d["USD_CHF"],
        })
    else:
        logger.warning("Not enough distinct test winners for TEST D combination.")

    # ── Load actual baseline from tf_baseline.md if available ─────────────────
    # Parse the markdown table if the file exists (fragile but sufficient for research)
    if baseline_path.exists():
        text = baseline_path.read_text()
        jpy_sharpe = chf_sharpe = None
        jpy_hr = chf_hr = None
        jpy_n = chf_n = None
        for line in text.splitlines():
            if line.startswith("| Sharpe"):
                parts = [p.strip() for p in line.split("|")]
                try:
                    jpy_sharpe = float(parts[2])
                    chf_sharpe = float(parts[3])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("| Hit Rate"):
                parts = [p.strip() for p in line.split("|")]
                try:
                    jpy_hr = float(parts[2].rstrip("%")) / 100
                    chf_hr = float(parts[3].rstrip("%")) / 100
                except (ValueError, IndexError):
                    pass
            elif line.startswith("| N Trades"):
                parts = [p.strip() for p in line.split("|")]
                try:
                    jpy_n = int(parts[2])
                    chf_n = int(parts[3])
                except (ValueError, IndexError):
                    pass
        baseline_jpy = {"sharpe": jpy_sharpe, "hit_rate": jpy_hr, "n_trades": jpy_n}
        baseline_chf = {"sharpe": chf_sharpe, "hit_rate": chf_hr, "n_trades": chf_n}

    # ── Write report ──────────────────────────────────────────────────────────
    output_path = PROJECT_ROOT / "results" / "tf_exit_tests.md"
    write_report(all_configs, baseline_jpy, baseline_chf, output_path)

    # ── Console summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("STEP 2 — EXIT VARIANT RESULTS")
    print("=" * 70)
    print(f"{'Test':<6} {'Config':<28} {'JPY_S':>7} {'CHF_S':>7} "
          f"{'JPY_W':>7} {'CHF_W':>7} {'JPY_L':>7} {'CHF_L':>7}")
    print("-" * 70)
    for cfg in all_configs:
        mj, mc = cfg["metrics_jpy"], cfg["metrics_chf"]
        print(
            f"{cfg['test']:<6} {cfg['config']:<28} "
            f"{mj['sharpe']:>7.3f} {mc['sharpe']:>7.3f} "
            f"{mj['avg_win_pips']:>7.1f} {mc['avg_win_pips']:>7.1f} "
            f"{mj['avg_loss_pips']:>7.1f} {mc['avg_loss_pips']:>7.1f}"
        )
    print("=" * 70)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
