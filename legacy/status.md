# Medallion Trading System — Status & Failure Analysis
**Date:** 2026-02-25
**Version:** v2.3 (regression pivot)
**Status: FAILING — not ready for paper or live trading**

---

## Executive Summary

The system has been through three complete research cycles. The verdict is clear:

| Approach | Mean Sharpe | Verdict |
|----------|-------------|---------|
| Rule-based (H4, regime-gated) | +0.357 | Least bad — marginal edge on 2/7 pairs |
| ML regression (walk-forward OOS) | −1.151 | Completely failed |
| Hybrid (rule gated by ML EV) | −0.430 | Worse than rules alone |

The rule baseline shows real edge only on USD_JPY (Sharpe 0.795) and USD_CHF (Sharpe 0.804). All other pairs are near zero or negative. The ML layer has never improved outcomes — it consistently makes things worse. Portfolio equity (H1, hybrid): $10,000 → $9,560 after 3+ years, Sharpe −0.49.

---

## 1. Data & Infrastructure

**Source:** OANDA practice API via oandapyV20
**Pairs:** EUR_USD, GBP_USD, USD_JPY, USD_CHF, AUD_USD, NZD_USD, USD_CAD
**Timeframes:** H1, H4, D (daily)
**History:** 2020-01-01 to present
**Volume:**
- H1: ~38,200 bars per pair
- H4: ~9,577 bars per pair
- D: ~1,597 bars per pair
- Total: ~345,785 rows across 21 datasets

**Pipeline:** `CandleFetcher` uses `InstrumentsCandlesFactory` (5000-candle pagination) → filters `complete==True` → parquet cache at `data/raw/`. Features saved to `data/processed/` as versioned parquet (e.g., `EUR_USD_H4_v2.2.parquet`).

---

## 2. Feature Engineering (v2.2 — 20 Features)

Feature version tag: `FEATURE_VERSION = "v2.2"`, `MAX_LOOKBACK = 252 bars`

### Returns (2)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `log_ret_1` | `log(close[t] / close[t-1])` | Single-bar momentum |
| `log_ret_4` | `log(close[t] / close[t-4])` | 4-bar (1 day on H4) momentum |

### Volatility (6)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `rvol_10` | `std(log_ret_1, 10) × √252` | Short-term realized vol, annualized |
| `rvol_20` | `std(log_ret_1, 20) × √252` | Medium-term realized vol |
| `atr_pct_14` | `ATR(14) / close` | Volatility normalized to price level |
| `bb_width_20` | `(upper − lower) / middle` (20-period, 2σ) | Squeeze/expansion detection |
| `bb_pos_20` | `(close − lower) / (upper − lower)` | Price position within band (0=bottom, 1=top) |
| `vol_ratio_10_60` | `std(10) / std(60)` | Vol compression (<0.7) vs expansion (>1.5) |

### Trend (5)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `ema_spread_12_26` | `(EMA12 − EMA26) / close` | Classic MACD spread, normalized |
| `ema_spread_5_20` | `(EMA5 − EMA20) / close` | Faster trend signal |
| `macd_hist` | `MACD_line − Signal_line` | Momentum change |
| `adx_14` | `ADX(14)` 0–100 | Trend strength (>20 = trending) |
| `adx_ratio_20` | `ADX / rolling_mean(ADX, 20)` | Trend strength relative to recent baseline |

### Momentum (1)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `roc_12` | `(close[t] − close[t-12]) / close[t-12]` | 12-bar rate of change |

### Regime (2)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `vol_regime_60` | `1 if ATR > 1.25×mean, -1 if ATR < 0.75×mean, 0 else` (60-bar lookback) | High/low vol regime flag |
| `trend_regime_50d` | `1 if D1_close > SMA(50), -1 otherwise` | Bullish/bearish macro regime (forward-filled to H4) |

### Calendar / Session (2)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `london_open` | `1 if hour==8 UTC else 0` | London session open bar |
| `ny_close` | `1 if hour==16 UTC else 0` | NY session close bar |

### Microstructure (2)
| Feature | Formula | Rationale |
|---------|---------|-----------|
| `ret_autocorr_1_20` | `Pearson corr(log_ret, log_ret.shift(1), window=20)` | Momentum (+) vs mean-reversion (−) |
| `atr_zscore_252` | `(ATR − rolling_mean) / rolling_std` (window=252) | ATR level relative to 1-year history |

**Dropped features (IC < 0.02):** `carry_diff` (constant per pair, no signal), `day_of_week` (dead across all pairs), `ny_overlap` (replaced by cleaner london_open + ny_close).

---

## 3. Label Construction

Labels are computed with forward-looking data — only used for training and evaluation, never in live inference.

**FeaturePipeline configuration:**
```
horizon = 1 bar
pt_multiplier = 2.0 × ATR(14)  ← profit target distance
sl_multiplier = 1.0 × ATR(14)  ← stop loss distance
max_holding = 20 bars (5 trading days on H4)
```

### Label Types

**`label_direction`** — binary, `1 if forward_return > 0 else 0`
Near-useless: 50.1% up / 49.9% down by definition on a random walk.

**`label_triple_barrier`** — categorical
- `1` if price hits `close + 2×ATR` first (profit target)
- `0` if price hits `close − 1×ATR` first (stop loss)
- `NaN` if neither hit within 20 bars (excluded from training)

**Observed balance: 33% TP / 65% SL / 2% timeout**

This is not random. The asymmetry is structural: the profit target is set at 2×ATR but the stop is at 1×ATR. Price must travel twice as far to win as to lose. In the mean-reverting FX regime most bars inhabit, sustained directional moves of 2×ATR within 20 bars are the minority event.

**`label_ev`** — continuous (v2.3 regression target)
- `+2.0` if PT hit first
- `−1.0` if SL hit first
- `clip((close[t+20] − close[t]) / ATR, −1.0, +2.0)` if timeout

Range: approximately [−1, +2]. Mean ≈ −0.10 (slightly negative expected value, consistent with costs).

---

## 4. Rule-Based Strategies

Three strategies are implemented and can be used independently or aggregated.

### TrendFollowStrategy
```
fast_ema = 12 (or 8 on H4 variant)
slow_ema = 26 (or 21 on H4 variant)
adx_window = 14
adx_threshold = 20.0
stop = ATR(14) × 2.0

Long:  EMA_fast > EMA_slow  AND  ADX > 20
Short: EMA_fast < EMA_slow  AND  ADX > 20
Flat:  ADX ≤ 20 (non-trending market — sits out)
```

### MeanReversionStrategy
```
rsi_window = 14
rsi_oversold = 30.0
rsi_overbought = 70.0
bb_window = 20
bb_std = 2.0
stop = ATR(14) × 1.5

Long:  RSI < 30  AND  close ≤ BB_lower  (oversold + at band)
Short: RSI > 70  AND  close ≥ BB_upper  (overbought + at band)
Flat:  otherwise
```

### BreakoutStrategy
```
channel_window = 20
atr_expansion_factor = 1.1
stop = ATR(14) × 2.0

Long:  close > max(high[t-20:t-1])  AND  ATR > mean(ATR, 20) × 1.1
Short: close < min(low[t-20:t-1])   AND  ATR > mean(ATR, 20) × 1.1
Flat:  no breakout OR vol not expanding
```

### SignalAggregator
```
min_consensus = 2   (at least 2 of 3 strategies must agree)

vote_sum = trend_signal + mr_signal + breakout_signal

combined = +1  if vote_sum ≥ +2
combined = −1  if vote_sum ≤ −2
combined =  0  otherwise (conflict or insufficient consensus)
```

### Regime Gate (H4 variant)
Applied on top of any rule signal: kill the trade if the D1 SMA50 disagrees with direction.
```
Kill longs  if trend_regime_50d < 0  (price below daily SMA50)
Kill shorts if trend_regime_50d > 0  (price above daily SMA50)
```

### Rule-Only Results (H4, regime-gated, 5-year backtest)

| Pair | Sharpe | CAGR | Max DD | N Trades | Hit Rate |
|------|--------|------|--------|----------|----------|
| USD_CHF | **0.804** | 4.98% | −6.0% | 316 | 39.4% |
| USD_JPY | **0.795** | 4.40% | −6.4% | 301 | 38.8% |
| USD_CAD | 0.332 | 2.03% | −10.8% | 306 | 36.4% |
| AUD_USD | 0.183 | 1.15% | −9.3% | 298 | 37.5% |
| EUR_USD | 0.160 | 0.73% | −13.5% | 313 | 34.8% |
| GBP_USD | 0.056 | 0.44% | −13.4% | 322 | 36.6% |
| NZD_USD | 0.020 | −0.27% | −9.9% | 307 | 35.1% |

Target: Sharpe > 0.5 on ≥5/7 pairs. Actual: 2/7 pairs exceed 0.5.

---

## 5. ML Pipeline (v2.3 — Regression on label_ev)

The system pivoted from binary classification (v2.2) to regression predicting `label_ev` directly.

### Models
- **LGBMRegressor:** `n_estimators=300, lr=0.05, num_leaves=31, min_child_samples=50, subsample=0.8, colsample=0.8`
- **XGBRegressor:** `n_estimators=300, lr=0.05, max_depth=4, subsample=0.8, colsample=0.8, objective="reg:squarederror"`
- **Ridge:** `Pipeline([StandardScaler(), Ridge(alpha=1.0)])` — linear baseline

### Walk-Forward Training
```
n_splits = 5
purge_bars = 252   ← equal to MAX_LOOKBACK (prevent label contamination)
embargo_bars = 10  ← buffer between training end and test start

For fold k:
  effective_train_end = (k+1) × fold_size − 252
  test_start = (k+1) × fold_size + 10
  test_end = test_start + fold_size
```

### Pod Ensemble Architecture
Five semantic sub-models, each trained on a feature subset, stacked by Ridge:

```
Pod 1 (vol):      log_ret_1, log_ret_4, rvol_10, rvol_20, atr_pct_14, bb_width_20, vol_ratio_10_60
Pod 2 (trend):    ema_spread_12_26, ema_spread_5_20, macd_hist, adx_14, adx_ratio_20, roc_12
Pod 3 (calendar): london_open, ny_close, bb_pos_20
Pod 4 (regime):   vol_regime_60, trend_regime_50d, atr_zscore_252
Pod 5 (micro):    ret_autocorr_1_20, bb_width_20

Stacker: Ridge(alpha=1.0) trained on OOS pod predictions only (no in-sample leakage)
```

### Primary Metric
**IC (Information Coefficient)** = Spearman rank correlation between `pred_ev` and `label_ev`
- IC > 0.05 = strong
- IC > 0.03 = marginally tradeable
- IC < 0.02 = dead signal

### ML Training Results (all pairs, LightGBM, H4)

| Pair | Mean OOS IC | Mean R² | Mean MSE |
|------|-------------|---------|---------|
| USD_JPY | 0.0512 | 0.0245 | 0.3012 |
| USD_CHF | 0.0498 | 0.0198 | 0.3089 |
| EUR_USD | 0.0445 | 0.0167 | 0.3195 |
| USD_CAD | 0.0431 | 0.0156 | 0.3145 |
| NZD_USD | 0.0401 | 0.0134 | 0.3278 |
| AUD_USD | 0.0367 | 0.0071 | 0.3623 |
| GBP_USD | 0.0389 | 0.0102 | 0.3456 |
| **Mean** | **0.0434** | **0.0153** | **0.3257** |

IC is above the 0.03 threshold — models are "technically tradeable." But the backtest results are catastrophically negative, which tells us IC is not a sufficient signal quality gate.

### Signal Generation from ML
```python
pred_ev = model.predict(features)          # continuous, range ≈ [−1, +2]
ml_signal = +1  if pred_ev >  +0.05       # ev_threshold
ml_signal = −1  if pred_ev <  −0.05
ml_signal =  0  otherwise
```

Signal frequency at various thresholds (EUR_USD H4 example):
- ±0.02: ~19% of bars get a signal
- ±0.05: ~6% of bars (used as default)
- ±0.10: ~1% of bars

### ML-Only Walk-Forward Backtest Results

| Pair | Sharpe | CAGR | Max DD | N Trades |
|------|--------|------|--------|----------|
| NZD_USD | −1.850 | −12.5% | −98.3% | — |
| EUR_USD | −1.465 | −9.68% | −95.6% | — |
| AUD_USD | −1.381 | −8.48% | −93.6% | — |
| GBP_USD | −1.121 | −7.72% | −91.7% | — |
| USD_CAD | −0.917 | −5.75% | −83.8% | — |
| USD_CHF | −0.708 | −4.25% | −75.6% | — |
| USD_JPY | −0.615 | −4.27% | −76.0% | — |
| **Mean** | **−1.151** | — | — | — |

The ML model is not just failing — it is reliably predicting the wrong direction on enough bars to produce near-total drawdowns.

---

## 6. Hybrid Approach

The hybrid approach uses the rule signal for direction and the ML model to filter: only trade when ML confirms the rule's direction.

```
Long:  rule_signal == +1  AND  pred_ev >  +ev_threshold
Short: rule_signal == −1  AND  pred_ev <  −ev_threshold
Flat:  all other cases (rule or ML disagree, or ML conviction too low)
```

EV thresholds tested: 0.02, 0.03, 0.05, 0.07, 0.10

### Hybrid Results (best threshold per pair)

| Pair | Best Threshold | Sharpe | CAGR | Max DD |
|------|---------------|--------|------|--------|
| USD_JPY | 0.02 | −0.014 | −0.034% | −8.74% |
| USD_CHF | 0.10 | −0.147 | −0.206% | −12.7% |
| AUD_USD | 0.02 | −0.315 | −0.444% | −15.5% |
| USD_CAD | 0.10 | −0.382 | −0.467% | −14.0% |
| EUR_USD | 0.05 | −0.640 | −0.842% | −25.3% |
| GBP_USD | 0.07 | −0.701 | −0.901% | −25.3% |
| NZD_USD | 0.02 | −0.813 | −1.038% | −27.3% |
| **Mean** | — | **−0.430** | — | — |

The ML gate does not improve any pair. On the best pair (USD_JPY), it achieves near-breakeven by suppressing most trades. On weak pairs, it makes things worse by killing the few good rule signals.

### Three-Way Comparison (winner per pair)

| Pair | Rule Sharpe | ML Sharpe | Hybrid Sharpe | Winner |
|------|-------------|-----------|---------------|--------|
| USD_CHF | **0.804** | −0.708 | −0.147 | Rule |
| USD_JPY | **0.795** | −0.615 | −0.014 | Rule |
| USD_CAD | **0.332** | −0.917 | −0.382 | Rule |
| AUD_USD | **0.183** | −1.381 | −0.315 | Rule |
| EUR_USD | **0.160** | −1.465 | −0.640 | Rule |
| GBP_USD | **0.056** | −1.121 | −0.701 | Rule |
| NZD_USD | **0.020** | −1.850 | −0.813 | Rule |

Rules win every single pair. The system should not include ML in its current form.

---

## 7. Backtest Mechanics — How We Interact with the Market

Understanding the exact execution model is essential for diagnosing issues.

### Entry
- Signal computed at bar `t` close using all available data to that point (no look-ahead)
- Entry executed at bar `t+1` open
- Position size computed at entry using current equity and ATR-derived stop

### Stop Loss
- Computed at entry: `stop_price = entry_price − ATR(14) × 2.0` (long) or `+ ATR(14) × 2.0` (short)
- Checked on every subsequent bar: if `low < stop_price` (long) or `high > stop_price` (short), trade exits
- Exit assumed to occur at the stop price (not the bar close) — favorable assumption vs reality

### Exit Conditions
1. Stop hit (bar's low/high breaches stop_price)
2. Signal flip (direction reverses to opposite)
3. End of data (closed at last close)

Costs are deducted on both entry and exit legs.

### Cost Model
```
EUR_USD: spread=1.0 pip,  slippage=0.5 pip  → 1.5 pips one-way, 3.0 round-trip
GBP_USD: spread=1.5 pip,  slippage=0.5 pip  → 2.0 pips one-way, 4.0 round-trip
USD_JPY: spread=1.2 pip,  slippage=0.5 pip  → 1.7 pips one-way, 3.4 round-trip
USD_CHF: spread=1.5 pip,  slippage=0.5 pip  → 2.0 pips one-way, 4.0 round-trip
AUD_USD: spread=1.5 pip,  slippage=0.5 pip  → 2.0 pips one-way, 4.0 round-trip
NZD_USD: spread=2.0 pip,  slippage=0.5 pip  → 2.5 pips one-way, 5.0 round-trip
USD_CAD: spread=1.5 pip,  slippage=0.5 pip  → 2.0 pips one-way, 4.0 round-trip
```

### Position Sizing
```
units = (equity × risk_pct) / (stop_distance_pips × pip_value_per_unit)

risk_pct = 0.005  (0.5% of equity at risk per trade)

EUR_USD example:
  Equity:   $10,000
  Risk $:   $50
  ATR:      0.001 (10 pips on EUR_USD)
  Stop:     ATR × 2 = 0.002 = 20 pips
  pip_value = 0.0001 USD/pip/unit
  units = 50 / (20 × 0.0001) = 25,000 units
```

### Sample Trades (EUR_USD H1)
```
Date        Dir  Units   Entry    Exit     Stop     P&L USD  Pips   Reason
2021-01-13  Long  31,799  1.21598  1.21441  1.21441  −54.77  −17.2  STOP HIT
2021-01-13  Short 30,392  1.21614  1.21548  1.21449  −24.62   −8.1  SIGNAL EXIT
2021-02-16  Long  41,128  1.21069  1.20947  1.20947  −56.17  −13.7  STOP HIT
2021-02-17  Short 43,501  1.20900  1.20922  1.20785    3.05    0.7  SIGNAL EXIT
```

The pattern is clear: stops trigger at −13 to −17 pips, wins close at 0.5 to 2 pips. With hit rate 36%, this math cannot produce profits.

---

## 8. Actual Results — All Metrics

### H1 Portfolio (hybrid/ML, 5-year walk-forward OOS)

| Pair | CAGR | Total Return | Sharpe | Sortino | Max DD | Hit Rate | Profit Factor | N Trades |
|------|------|-------------|--------|---------|--------|----------|---------------|----------|
| EUR_USD | −0.07% | −8.43% | −0.27 | −0.31 | −8.43% | 36% | 0.44 | 125 |
| GBP_USD | −0.004% | −0.56% | −0.09 | −0.11 | −0.56% | 0% | 0.00 | **1** |
| USD_JPY | −0.02% | −2.28% | −0.07 | −0.08 | −5.36% | 46% | 0.70 | 54 |
| USD_CHF | −0.05% | −6.68% | −0.26 | −0.30 | −6.84% | 39% | 0.38 | 84 |
| AUD_USD | −0.02% | −1.95% | −0.18 | −0.21 | −1.95% | 17% | 0.01 | **6** |
| NZD_USD | −0.07% | −8.88% | −0.31 | −0.35 | −9.04% | 39% | 0.27 | 59 |
| USD_CAD | −0.02% | −2.00% | −0.18 | −0.21 | −2.00% | 33% | 0.12 | **9** |
| **Portfolio** | **−0.04%** | **−4.40%** | **−0.49** | **−0.55** | **−4.51%** | ~36% | **0.38** | ~338 |

### H4 Hybrid Results

| Pair | CAGR | Total Return | Sharpe | Max DD | Hit Rate | Profit Factor |
|------|------|-------------|--------|--------|----------|---------------|
| EUR_USD | −0.84% | −22.77% | −0.64 | −25.28% | 2.4% | 0.65 |
| GBP_USD | −0.90% | −24.18% | −0.70 | −25.25% | 2.3% | 0.62 |
| USD_JPY | −0.03% | −1.03% | −0.01 | −8.74% | 2.6% | 0.99 |
| AUD_USD | −0.44% | −12.71% | −0.31 | −15.48% | 2.4% | 0.80 |

Note the H4 hit rates: 2.4%. That means 97.6% of bars generate a flat signal, and essentially every trade loses. This is not a trading system — it's a slow bleed.

---

## 9. Why Are We Failing — Root Cause Analysis

### A. Stop Placement Is the Primary Killer

**Evidence:**
- EUR_USD: avg win ≈ 5 pips, avg loss ≈ 13 pips, hit rate 36%
- Expected value per trade: `(0.36 × 5) − (0.64 × 13) = 1.8 − 8.32 = −6.52 pips`
- Over 125 trades that's −815 pips, matching the −8.43% total loss

**Why it's wrong:**
- ATR × 2.0 as a stop sounds reasonable in theory, but on H1 it's too tight
- H1 ATR is small — a 2×ATR stop is within the normal noise of 1–2 candles
- Price hits the stop and then resumes in the original direction constantly (whipsaw)
- The stop is not tied to a structural level (support/resistance, prior swing), just a volatility multiple

**The math that breaks it:**
Round-trip cost is 3.0 pips (EUR_USD). Average winning trade is 5 pips before costs → 2 pips net. But with 36% hit rate, even a 1:1 R:R system needs ≥50% win rate to break even. We're achieving 36% with a 0.38 payoff ratio. This requires a hit rate of `1 / (1 + 0.38) = 72%` to break even — more than double what we have.

### B. Signal Sparsity — Consensus Is Too Tight

**Evidence:**
- GBP_USD H1: **1 trade** over 31,808 bars (3+ years)
- AUD_USD H1: **6 trades** over 31,805 bars
- USD_CAD H1: **9 trades** over 31,807 bars

With 1 trade over 3 years, there is no statistical basis for any conclusion. The signal generator is so conservative that these pairs effectively never trade.

**Why it happens:**
The `min_consensus = 2` requirement seems sensible but interacts badly with the chosen strategy mix. Trend and mean-reversion are philosophically opposed — when one fires, the other often disagrees or is silent. Breakout requires ATR expansion, which further narrows the trigger window. All three agree on ≥2 only in a specific market structure (trending AND overbought/oversold AND breaking channel), which is rare.

**The compound problem:** The regime gate (D1 SMA50) eliminates another chunk. By the time all filters clear, nothing is left to trade.

### C. Strategy Type Conflict

Trend, mean reversion, and breakout are designed to work in mutually exclusive regimes:

- **Trend** profits from sustained directional moves
- **Mean reversion** profits from price returning to a range center
- **Breakout** profits from volatility expansion after compression

When trend fires (fast EMA > slow EMA + ADX trending), this is exactly the regime where mean reversion should not fire (RSI often elevated). Requiring 2-of-3 across contradictory strategies is not consensus — it's paralysis. These strategies should be run independently per regime, not voted on together.

### D. ML is Contra-Predictive in Practice

**The IC number is misleading:**
- Mean OOS IC = 0.0434
- R² = 0.0153
- This means ML explains 1.5% of variance in `label_ev`

An IC of 0.04 sounds positive. But in a walk-forward backtest, the model generates predictions bar-by-bar and those predictions drive real trades. The problem: **the model's errors are not random — they are systematically correlated with loss events**.

**Why the model is biased toward bad calls:**
The training data has 65% SL outcomes (label_ev = −1.0) and only 33% TP outcomes (+2.0). The model learns to predict mild negative values as a prior. When the true direction is strongly positive (+2.0), the model underpredicts the magnitude. The EV threshold (0.05) gates signals by pred_ev > 0.05 — but the model's distribution of pred_ev is centered near zero with most predictions falling in [−0.10, +0.10], making the threshold nearly symmetric around a slightly negative mean.

**The purge problem:**
- purge_bars = 252 (equal to MAX_LOOKBACK = 252)
- On H4 with ~9,577 total bars: each fold has ~1,596 bars
- Purging 252 bars from training end removes 16% of each fold's training data
- With 5 folds in expanding window, the first fold has very little training data after purge

**The gate effect:**
When used as a gate on rule signals, ML kills ~94% of signals (only 6% of bars get a pred_ev above ±0.05). Of the signals killed, some were profitable — the model can't distinguish. The surviving signals have no better hit rate than the original rule signals. Net effect: fewer trades, no better quality.

### E. Label Construction Bias

**The 65% SL imbalance is structural, not fixable by resampling:**

Triple-barrier with PT=2×ATR and SL=1×ATR requires price to travel 2× further to win than lose. On H4, the typical ATR for EUR_USD is ~0.0050 (50 pips). PT = 100 pips, SL = 50 pips within 20 bars (80 hours). FX does not often sustain 100-pip directional moves over 3 trading days; it frequently oscillates back and hits the 50-pip stop.

This creates a paradox: we set 2:1 reward-to-risk to make winners "bigger" per trade, but the lower frequency of TP events (33%) relative to SL events (65%) means expected value is negative before the model even runs:
```
Expected EV = (0.33 × +2.0) + (0.65 × −1.0) + (0.02 × 0.0)
            = 0.66 − 0.65
            = +0.01
```
Barely positive, and that's before transaction costs. The label itself carries almost no positive expectancy.

### F. Cost Drag at H4

H4 generates far fewer trades, which should reduce cost drag. But the hybrid approach's signal frequency collapsed to 2.4% hit rate (where "hit rate" here means profitable trades / total trades, not "signals per bar"). The result: every signal costs 3-5 pips round-trip, and with H4 bars being rare and often marginal, the cost erodes nearly all profit.

**Minimum profitable pip movement (before costs):**
- EUR_USD H4: need >3.0 pips net to break even → need >2×ATR move to 100-pip target in 80 hours
- This requires sustained institutional buying/selling, not just a technical signal

### G. No Correlation Awareness

The 7 pairs traded simultaneously are not independent. Major correlations:
- USD_JPY and USD_CHF: both USD-base pairs, both trend with USD strength
- AUD_USD and NZD_USD: commodity currencies, correlation ~0.85
- EUR_USD and GBP_USD: European pairs, correlation ~0.75

When USD strengthens, USD_JPY and USD_CHF both signal short (buy USD), both stop out simultaneously, both entries lose on the same day. The portfolio drawdown is not 7 independent strategies — it's 7 correlated expressions of the same underlying USD trend. No position-level correlation check exists in the current backtester.

### H. Timeframe Mismatch in Feature Windows

Some features are calibrated for daily charts but applied to H4/H1:
- `trend_regime_50d`: 50-day SMA on daily — this is correct and forward-filled appropriately
- `atr_zscore_252` on H4: 252 bars × 4 hours = 42 trading days. Appropriate.
- `ret_autocorr_1_20` on H4: 20-bar window = 80 hours = 3.3 days. Too short to be regime-stable; captures intraday noise, not structural autocorrelation.
- `vol_ratio_10_60` on H1: 10-bar std = 10 hours (1.25 trading days), 60-bar std = 60 hours (7.5 days). The ratio is noisy at H1.
- `rvol_10` on H1: 10-hour realized vol. On H4 this would be 40 hours. Neither reflects the regime-level volatility it claims to measure.

The features were likely conceived for a daily timeframe and scaled down without verifying their informational content at H1/H4.

---

## 10. Diagnostic Numbers Summary

| Metric | Value | Interpretation |
|--------|-------|----------------|
| label_triple_barrier balance | 33% TP / 65% SL | Negative-biased label |
| Mean OOS IC (all pairs, H4) | +0.0434 | Above 0.03 threshold — technically tradeable |
| IS vs OOS IC gap | 0.0125 | Small — overfitting not the primary issue |
| ML R² (best pair) | 0.0245 | 2.5% of variance explained |
| EUR_USD round-trip cost | 3.0 pips | Equals 60% of avg winning trade |
| EUR_USD stop distance | ~20 pips (ATR×2) | Normal noise range on H1 |
| EUR_USD avg win | ~5 pips | After costs: ~2 pips |
| EUR_USD avg loss | ~13 pips | After costs: ~16 pips |
| Hit rate needed to break even | ~72% | Required given 0.38 payoff ratio |
| Actual hit rate | 36–46% | Half of what's needed |
| GBP_USD H1 total trades (3yr) | 1 | Consensus too tight to generate signals |
| H4 hybrid hit rate | 2.4% | 97.6% flat — essentially never trades profitably |
| ML signal frequency at ±0.05 | ~6% of bars | Only 6 in 100 bars cross the threshold |
| Portfolio H1 total return | −4.40% | 5 years of work → losing money |

---

## 11. What Would Need to Change

This section is diagnostic, not a plan. These are the minimum conditions for the system to have a viable edge:

**Stop placement:** Stops must be placed at structurally meaningful levels (prior swing lows/highs, support/resistance) or widened substantially. A 20-pip stop on EUR_USD H1 does not survive normal market noise. A 40–50 pip stop with proportionally smaller position size would reduce whipsaw frequency but requires tighter cost control.

**Signal generation:** Trend and mean reversion strategies should not be aggregated by vote — they should be selected by regime. In trending markets: trend signal only. In ranging markets: mean reversion only. A regime classifier (e.g., ADX-based or volatility-based) decides which strategy runs.

**Label construction:** The 2:1 PT:SL ratio produces 65% SL labels. Either widen PT to 3× ATR (to increase TP frequency), tighten SL to 0.5× ATR (to increase SL frequency but reduce per-trade loss), or use simple forward returns (horizon=5 bars) as the label — cleaner and unbiased.

**ML approach:** IC of 0.04 on H4 is marginal. The model needs to be evaluated on actual trade-level profitability, not IC. A model can have positive IC and negative trading performance if it's right on low-magnitude predictions and wrong on high-magnitude ones (where the real money is).

**Cost awareness:** Any strategy must demonstrate positive expected value after 3–5 pip round-trip cost before it warrants further development. Currently, expected value per trade before costs is approximately −6.5 pips (EUR_USD). No amount of ML tuning fixes a −6.5 pip raw edge.

**Pair selection:** USD_JPY and USD_CHF have demonstrated the strongest rule-based edge. Concentrated research on these two pairs with a proper risk model is more likely to produce results than spreading effort across 7 correlated pairs.

---

## 12. System Health Scorecard

| Component | Status | Issue |
|-----------|--------|-------|
| Data pipeline | PASS | Clean, complete, no leakage |
| Feature engineering | MARGINAL | Reasonable features, timeframe calibration off |
| Label construction | FAIL | 65% SL bias, near-zero expected EV |
| Rule signals | MARGINAL | Real edge on 2/7 pairs, too sparse on 5/7 |
| Consensus logic | FAIL | Min_consensus=2 of contradictory strategies → no signals |
| ML training | MARGINAL | IC technically positive but insufficient |
| ML backtesting | FAIL | All Sharpe ratios negative, MaxDD up to −98% |
| Hybrid gating | FAIL | Worse than rules on every pair |
| Stop placement | FAIL | Too tight for timeframe, whipsawed constantly |
| Cost model | PASS | Realistic estimates (may be optimistic on news) |
| Position sizing | PASS | Correct formula, sensible 0.5% risk per trade |
| Correlation management | FAIL | No filter; correlated losses compound |
| Portfolio statistics | FAIL | Sharpe −0.49, total return −4.40% (5yr H1) |

**Do not deploy to paper trading in current form.**
