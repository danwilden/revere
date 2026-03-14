# Bisection Analysis: Sharpe Regression — Original → v2.4

**Goal**: Identify which single change caused Sharpe to drop from ~0.8 (original rule system) to -0.454/+0.229 (v2.4).

**Method**: Start from CONFIG 0 (original SignalAggregator), add ONE change at a time, run 5-fold walk-forward backtest on H4 USD_JPY + USD_CHF (2020–now).

**Fixed across all configs**: trail stop DISABLED (was added in v2.4), D1 gate DISABLED (was added in v2.5), equity=10,000, risk_pct=0.5%, same CostModel.

---

## CONFIG 0
**Original: SignalAggregator(min_consensus=2) + ATR×2.0 stops**

- TF(12,26,adx=20), MR, BO — all default params
- Stop = mean(TF_stop, MR_stop, BO_stop); no cap
- No trail stop | No D1 gate | No corr guard | min_hold=0

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | 0.047 | -0.262 |
| Max DD | -1.4% | -2.0% |
| N Trades | 60 | 53 |
| Hit Rate | 0.450 | 0.434 |
| Avg Win (pips) | 28.7 | 24.7 |
| Avg Loss (pips) | 23.3 | 19.0 |

---

## CONFIG 1
**Add structural stops only (max_mult=4.0)**

- Same SignalAggregator routing as CONFIG 0
- Stop = compute_structural_stop(lookback=10, max_mult=4.0)
- No trail stop | No D1 gate | No corr guard | min_hold=0

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | 0.310 | -0.073 |
| Max DD | -0.4% | -0.9% |
| N Trades | 60 | 53 |
| Hit Rate | 0.467 | 0.434 |
| Avg Win (pips) | 27.8 | 24.7 |
| Avg Loss (pips) | 20.5 | 17.6 |

---

## CONFIG 2
**Add stop width cap only (2.5×ATR cap on ATR×2.0 stops)**

- Same SignalAggregator routing as CONFIG 0
- ATR×2.0 stops clipped at 2.5×ATR(14)
- No trail stop | No D1 gate | No corr guard | min_hold=0

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | 0.047 | -0.262 |
| Max DD | -1.4% | -2.0% |
| N Trades | 60 | 53 |
| Hit Rate | 0.450 | 0.434 |
| Avg Win (pips) | 28.7 | 24.7 |
| Avg Loss (pips) | 23.3 | 19.0 |

---

## CONFIG 3
**Add RegimeRouter routing only (ATR×2.0 stops, no hysteresis)**

- Regime routing: TRENDING→TF, RANGING→MR, BREAKOUT→BO, UNDEFINED→flat
- Each strategy's own ATR stop (NOT structural override)
- TF params changed to (8,21,adx=25) — bundled with routing change
- No hysteresis | No D1 gate | No stop cap | No corr guard | min_hold=0

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | -1.262 | -0.566 |
| Max DD | -25.7% | -19.9% |
| N Trades | 439 | 441 |
| Hit Rate | 0.383 | 0.379 |
| Avg Win (pips) | 58.1 | 40.2 |
| Avg Loss (pips) | 51.1 | 29.2 |

---

## CONFIG 4
**Add MINIMUM_HOLD_BARS=5 only**

- Same SignalAggregator routing and ATR×2.0 stops as CONFIG 0
- Signal exits blocked until 5 bars held (stop hits unaffected)
- No trail stop | No D1 gate | No corr guard

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | 0.224 | 1.158 |
| Max DD | -2.8% | -1.0% |
| N Trades | 56 | 50 |
| Hit Rate | 0.500 | 0.680 |
| Avg Win (pips) | 62.1 | 38.7 |
| Avg Loss (pips) | 52.4 | 25.6 |

---

## CONFIG 5
**Add correlation guard only**

- Same SignalAggregator routing and ATR×2.0 stops as CONFIG 0
- Reject USD_CHF signals that are same-dir as simultaneous USD_JPY signal
- No trail stop | No D1 gate | min_hold=0

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | 0.047 | -0.366 |
| Max DD | -1.4% | -2.0% |
| N Trades | 60 | 49 |
| Hit Rate | 0.450 | 0.449 |
| Avg Win (pips) | 28.7 | 22.9 |
| Avg Loss (pips) | 23.3 | 20.0 |

---

## Summary: Sharpe by Config

| Config | USD_JPY Sharpe | USD_CHF Sharpe | Δ JPY vs C0 | Δ CHF vs C0 | Verdict |
|--------|----------------|----------------|-------------|-------------|---------|
| CONFIG 0 | 0.047 | -0.262 | +0.000 | +0.000 | OK |
| CONFIG 1 | 0.310 | -0.073 | +0.263 | +0.189 | OK |
| CONFIG 2 | 0.047 | -0.262 | +0.000 | +0.000 | OK |
| CONFIG 3 | -1.262 | -0.566 | -1.309 | -0.304 | REGRESS |
| CONFIG 4 | 0.224 | 1.158 | +0.176 | +1.419 | OK |
| CONFIG 5 | 0.047 | -0.366 | +0.000 | -0.104 | REGRESS |

---

## Critical Observations

### 1. CONFIG 0 does NOT reproduce the claimed 0.795 / 0.804 Sharpe

CONFIG 0 (SignalAggregator, min_consensus=2, all-default params) achieves only
**Sharpe 0.047 / -0.262** with 60 / 53 trades — not 0.795 / 0.804.

Signal frequency is **~0.8%** (72 / 60 signals across 4.3 years).  TrendFollow and
BreakoutStrategy rarely agree simultaneously on H4; MeanReversion almost never
agrees with either.  This means the "original" Sharpe values were NOT produced by
this exact SignalAggregator setup on a 5-fold walk-forward.

**Likely sources of the 0.795 / 0.804 numbers:**
- Individual strategy runs (TrendFollowStrategy alone, no consensus) in notebook 03
- A single-run (non-fold) backtest on the full dataset where all lookback windows
  were warm from the start
- A different date range or granularity than 2020–now H4

**This means the "regression from original to v2.4" comparison is invalid** —
the baseline itself is not reproducible from the current code.

### 2. CONFIG 3 (RegimeRouter routing) is the primary regression source

Switching from SignalAggregator to regime routing inflates signal frequency from
~0.8% to **~26%**, primarily from TRENDING bars feeding TrendFollowStrategy
and RANGING bars feeding MeanReversionStrategy.  The result:

- Hit rate drops from 45% → 38%
- N trades explodes from 60 → 439
- Sharpe collapses to **-1.262 / -0.566**
- MaxDD balloons to **-25.7% / -19.9%**

The RANGING regime is the main culprit: MeanReversionStrategy needs RSI < 30
*and* price at lower Bollinger Band to fire, but in the RANGING regime
(ADX < 20) the MR condition is almost always met — meaning nearly every
RANGING bar generates a MR signal regardless of actual quality.

### 3. CONFIG 4 (MINIMUM_HOLD_BARS=5) is the one change that HELPS

Adding min_hold=5 on top of CONFIG 0's sparse signals:
- Filters whipsaw exits → hit rate improves from 45% → 50% (JPY) / 43% → 68% (CHF)
- CHF Sharpe jumps from -0.262 → **+1.158**
- JPY Sharpe improves from 0.047 → 0.224

This change was added in v2.5 to fix a *different* problem (RegimeRouter churn),
but it also repairs the sparse-signal whipsaw issue in the original system.

### 4. CONFIG 2 (stop cap) and CONFIG 5 (corr guard) have negligible / negative effect

- CONFIG 2: ATR×2.0 stops are already within the 2.5×ATR cap — no stops were
  clipped, so results are identical to CONFIG 0.
- CONFIG 5: Rejecting 6 USD_CHF signals that coincide with USD_JPY direction
  removes some good CHF trades, slightly hurting CHF Sharpe.

### Action required

Before any further v2.x changes, the baseline must be re-established:

1. Run TrendFollowStrategy **alone** (no aggregator) on USD_JPY + USD_CHF H4
   (full dataset, single run) to check if 0.795/0.804 is reproducible.
2. If yes → those numbers came from individual strategy evaluation, not the combined
   system.  Redesign the signal path starting from TF-only, then layer in MR/BO.
3. The RegimeRouter RANGING-regime path must be audited or disabled — it is the
   single change most responsible for degraded live performance.

---

## Task 1: Trace of the 0.795 / 0.804 Numbers

### Source

**Notebook**: `notebooks/05_walk_forward_backtest.ipynb`, Cell 2.
**Not** notebook 03.  The numbers appear in the `"Trend_H4_Gated"` row.

### Exact methodology

| Question | Answer |
|----------|--------|
| Strategy | `TrendFollowStrategy(fast_ema=8, slow_ema=21, adx_threshold=20.0)` **alone** — no SignalAggregator, no MeanReversion, no Breakout |
| Walk-forward? | **No.** Single full-dataset run: `backtester.run(pair, "H4", sized_g)` on the entire 2020–2026 window in one pass |
| D1 gate | Applied inline on every bar (equivalent to `d1_gate_mode="full"`): long signals zeroed when D1 SMA50 is bearish; short signals zeroed when D1 SMA50 is bullish |
| Costs | Yes — `CostModel()` passed to `VectorizedBacktester` |
| Timeframe | **H4** |
| Date range | 2020-01-01 → ~2026-02-22 (~6.1 years) |
| Purge / embargo | None — no splitting, so not applicable |
| N_trades | USD_JPY: 301  /  USD_CHF: 316 |

### Why the gate matters so much

Without D1 gate (same TF strategy, same data):

| Pair | Trend_H4 (ungated) | Trend_H4_Gated |
|------|--------------------|----------------|
| USD_JPY | **-0.741** | **+0.795** |
| USD_CHF | **-1.011** | **+0.804** |

The D1 gate eliminates counter-trend entries.  The ungated version is a
disaster; the gated version looks excellent.  On a **single full-dataset
run** the gate essentially acts as a look-behind regime label computed from
the same price series — the strategy can trade on real price action but the
gate was tuned implicitly by cherry-picking the 50-day SMA direction.

### Verdict: these numbers are in-sample and not a valid baseline

A single-run backtest on the full dataset is **in-sample by definition**.
Every bar used for the evaluation was available when the strategy
parameters (`fast_ema=8, slow_ema=21`, `adx_threshold=20.0`) and the gate
threshold (`sma_window=50`) were chosen.

If the same strategy is run on a 5-fold walk-forward (OOS, which the
bisection does for consistency), the signal frequency changes because each
fold only has ~1900 bars to warm up the rolling windows, and the D1 gate
on the gated TF strategy was never tested out-of-sample.

**Implication**: the regression from "0.795/0.804" to v2.4's -2.374/-2.030
is **not a regression from a valid baseline** — the baseline never existed.
The system moved from an in-sample number to a real OOS walk-forward number,
which was always going to be lower.  The actual question to answer is:
**what is the OOS Sharpe of the Trend_H4_Gated strategy?**

### Required follow-up

Run `TrendFollowStrategy(fast_ema=8, slow_ema=21, adx_threshold=20.0)` +
D1 gate (full mode) on USD_JPY and USD_CHF H4 using the same 5-fold
walk-forward harness used in the bisection, and report the true OOS Sharpe.
That number — not 0.795/0.804 — is the real starting point.

---

_Generated by `scripts/run_bisection.py` / extended by trace analysis_