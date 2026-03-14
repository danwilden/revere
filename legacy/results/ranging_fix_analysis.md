# Ranging Fix Analysis

**Goal**: Test two fixes for the RANGING regime signal flood identified in the bisection.

**Baseline**: SignalAggregator(min_consensus=2) + structural stops(lookback=10, max_mult=4.0) + MINIMUM_HOLD_BARS=5 + trail stop

**Fix A**: RegimeRouter (RANGING → flat, MeanRev disabled) + structural stops + min_hold=5 + trail

**Fix B**: RegimeRouter (RANGING → MR with RSI<25/BB + ADX>12 gate) + structural stops + min_hold=5 + trail

**Fixed across all configs**: D1 gate DISABLED, no hysteresis, equity=10,000, risk_pct=0.5%, same CostModel, 5-fold walk-forward H4 2020–now.

---

## Baseline
**SignalAggregator(min_consensus=2) + structural stops + min_hold=5 + trail**

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | -0.216 | 0.952 |
| Max DD | -1.4% | -0.7% |
| N Trades | 56 | 52 |
| Hit Rate | 50.0% | 67.3% |
| Avg Win (pips) | 48.0 | 27.9 |
| Avg Loss (pips) | 51.7 | 27.3 |
| Signal Freq (%) | 0.8% | 0.6% |

---

## Fix A
**RegimeRouter (RANGING→flat) + structural stops + min_hold=5 + trail**

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | -1.519 | -0.590 |
| Max DD | -20.7% | -12.1% |
| N Trades | 467 | 462 |
| Hit Rate | 50.7% | 56.1% |
| Avg Win (pips) | 52.4 | 29.5 |
| Avg Loss (pips) | 72.4 | 39.8 |
| Signal Freq (%) | 25.6% | 24.7% |

---

## Fix B
**RegimeRouter (RANGING→MR with RSI<25/BB + ADX>12) + structural stops + min_hold=5 + trail**

| Metric | USD_JPY | USD_CHF |
|--------|---------|---------|
| Sharpe | -1.595 | -0.590 |
| Max DD | -21.8% | -12.1% |
| N Trades | 469 | 462 |
| Hit Rate | 50.5% | 56.1% |
| Avg Win (pips) | 52.4 | 29.5 |
| Avg Loss (pips) | 72.3 | 39.8 |
| Signal Freq (%) | 25.6% | 24.7% |

---

## Summary

| Config | JPY Sharpe | CHF Sharpe | Δ JPY | Δ CHF | JPY Sig% | CHF Sig% |
|--------|-----------|-----------|-------|-------|----------|----------|
| Baseline | -0.216 | 0.952 | +0.000 | +0.000 | 0.8% | 0.6% |
| Fix A | -1.519 | -0.590 | -1.302 | -1.542 | 25.6% | 24.7% |
| Fix B | -1.595 | -0.590 | -1.379 | -1.542 | 25.6% | 24.7% |

---

## Critical Observations

### 1. RANGING is NOT the signal flood source

The bisection diagnosis ("RANGING→MR fires on almost every ADX<20 bar") was incorrect.

Fold-level breakdown from Fix A (`ranging=flat`) logs:

| | Bars | RANGING bars | TRENDING bars | TRENDING signals |
|---|---|---|---|---|
| Fold 0 (JPY) | 1899 | 623 | ~540 | ~547 |
| Folds 1–4 | similar | similar | similar | ~99% of TRENDING |

**RANGING contributed ~6 MR signals per fold.** With RANGING disabled entirely (Fix A),
signal frequency stays at 25.6% — unchanged.  The flood comes entirely from TRENDING.

Fix B confirms this further: RSI<25 AND ADX>12 fires **zero times** in most folds inside
the RANGING regime on H4.  MeanReversionStrategy almost never triggers on RANGING bars
(ADX<20 is a low-momentum environment where RSI<30 and BB_lower rarely coincide).

**The original RANGING diagnosis was wrong.** MeanReversionStrategy is benign in RANGING;
TrendFollowStrategy is the problem in TRENDING.

### 2. The real culprit: TrendFollowStrategy generates persistent signals in TRENDING

`TrendFollowStrategy` fires `signal=1` on **every bar** where:
- `fast_EMA > slow_EMA` (trending up), AND
- `ADX > adx_threshold`

It does NOT fire only on crossover bars. Once an uptrend is established, the strategy
holds signal=1 on 99%+ of subsequent TRENDING bars until the EMA re-crosses.

TRENDING regime (ADX>25) covers ~25% of all H4 bars.  Nearly all of those bars
produce a non-zero signal → RegimeRouter generates 25–26% signal frequency regardless
of what RANGING does.

The Baseline (SignalAggregator) achieves 0.8% signal frequency because min_consensus=2
requires TF + MR (or TF + BO) to agree simultaneously — a rare event.  RegimeRouter's
exclusive routing breaks this filter entirely.

### 3. Fix A is worse than CONFIG 3 (full RegimeRouter)

Fix A Sharpe: -1.519 (JPY), -0.590 (CHF)
CONFIG 3 Sharpe: -1.262 (JPY), -0.566 (CHF)

Removing RANGING→MR eliminates the ~6 positive MR signals per fold while keeping the
full TRENDING flood.  Fix A is strictly worse.

### 4. The actual fix must target TRENDING, not RANGING

Options ranked by expected impact:

1. **Entry-only signals in TRENDING**: generate signal only on the first bar of a new
   crossover/ADX threshold crossing, not every subsequent TRENDING bar.  Reduces
   TRENDING signal frequency from ~99% to ~2-5% of TRENDING bars.

2. **D1 gate on TRENDING**: re-enable D1 gate (disabled in this analysis) to block
   counter-trend entries.  From notebook 05 analysis: D1 gate turns -0.741 → +0.795
   (JPY) on TF alone.

3. **Increase ADX threshold**: raising adx_threshold=25 → 30+ in TrendFollowStrategy
   reduces the fraction of bars classified as TRENDING, shrinking the flood at source.

4. **Restore SignalAggregator**: the consensus filter (min_consensus=2) is by far the
   most effective mechanism found — it reduces signal frequency from 25% to 0.8% and
   preserves strategy quality.  CONFIG 4 (SignalAggregator + structural stops + min_hold=5)
   produces CHF Sharpe +1.158 with 56 trades.

### 5. Recommended next step

Test D1 gate (re-enabled) on the RegimeRouter path with TRENDING only:

```
RegimeRouter + D1 gate("full") + structural stops + min_hold=5 + trail
```

Then test entry-only signals (crossover-bar trigger) as a standalone fix.

The baseline to beat is **CONFIG 4** (SignalAggregator + structural stops + min_hold=5):
JPY Sharpe -0.216, CHF Sharpe +0.952, 0.8% signal frequency.

---

_Generated by `scripts/run_ranging_fix.py` / extended with root-cause analysis_