Rebuild the Meta-Labeling Foundation
Restructure the training target. Instead of binary win/loss on the triple-barrier, train the meta-labeler to predict Expected Value of the rule strategy's signal: P(win) × PT_size − P(loss) × SL_size. This gives the model a continuous target that aligns with what you actually care about (profitability), avoids the sharp 0/1 boundary noise, and handles the asymmetric barrier more naturally.
Fix class imbalance properly. At 33.9% win rate, use scale_pos_weight in LightGBM set to (1−win_rate)/win_rate ≈ 1.95 rather than letting the model train on raw imbalanced data. Then apply Platt scaling (logistic regression on OOS fold predictions) to calibrate the output probabilities before applying any threshold. Your current thresholds (0.55–0.60) are being applied to uncalibrated probabilities, which is meaningless.
Per-pair meta-labelers. EUR/USD and GBP/USD behave structurally differently from USD/JPY in terms of volatility clustering, session effects, and carry dynamics. A single pooled meta-labeler averages these away. Train separate models per pair, or at minimum cluster pairs by behavior (commodity pairs: AUD, NZD, CAD vs. funding pairs: JPY, CHF vs. majors: EUR, GBP) and train cluster-level models.
Phase 3 — Feature Surgery
Your 23 features have significant redundancy and several dead columns. Here's what to do:
Kill or fix the broken features first. trend_regime_50d returning NaN and carry_diff returning 0 means two of your regime features are noise. Either fix the data pipeline to properly pass daily_df and instrument parameters, or drop them entirely. A constant feature wastes a degree of freedom and can confuse tree splits.
Measure information coefficient (IC) for every feature. For each feature, compute the Spearman rank correlation between the feature value at signal bars and the subsequent trade outcome (win=1/loss=0), calculated on OOS folds only. Anything with |IC| < 0.02 across all pairs is statistical noise and should be dropped. This is the single most important diagnostic step — it tells you which features have any edge at all before you do anything else.
Add features that are structurally orthogonal to what you have. Your current set is almost entirely technical-indicator derived. Consider:

COT positioning: Commitments of Traders data for EUR, GBP, JPY — commercial vs. speculative net positioning is a genuine sentiment signal unavailable from price alone
Economic surprise index: Citi's economic surprise indices are public and capture whether macro data is beating/missing expectations, which drives medium-term FX trends
Realized vs. historical vol ratio: Not just ATR z-score but the ratio of current 10-day realized vol to 60-day realized vol — a vol compression/expansion signal that precedes breakouts

Restructure the calendar features. hour_of_day and ny_overlap are a start, but encode them more carefully. Create: london_open (first 2 hours of London session), ny_close (last hour before NY close), pre_nfp (48 hours before NFP release), post_central_bank (24 hours after major CB decision). These are known structural liquidity events in FX.
Phase 4 — Model Architecture Overhaul
Replace single LightGBM with a proper weak learner ensemble. This is the actual Simons-style step. Build five separate weak learners, each trained on a different feature pod, then stack them:
Pod 1: Returns + Volatility features → LightGBM (captures vol regimes)
Pod 2: Trend + Momentum features → LightGBM (captures directional persistence)  
Pod 3: Calendar + Session features → Logistic Regression (simple, interpretable)
Pod 4: Regime features (vol_regime, trend_regime, atr_zscore) → LightGBM
Pod 5: Microstructure (autocorr, carry, spread) → Ridge Regression

Stacker: Logistic Regression on [P1_prob, P2_prob, P3_prob, P4_prob, P5_prob]
         trained on OOS predictions from each pod (never on in-sample)
The stacker uses logistic regression deliberately — you want a simple, regularized combiner that doesn't overfit the stacking layer. The key constraint is that each pod's OOS predictions become the stacker's training features, so you need enough OOS data to train the stacker meaningfully (this is why you need more walk-forward splits, not fewer).
Switch from binary classification to probability ranking. Rather than classifying win/loss, rank signals by their estimated probability and only trade the top quartile. This works better with weak learners because you're not relying on any single threshold being calibrated correctly — you're using relative ranking, which is more robust.
Phase 5 — Bet Sizing and Portfolio Construction
Once ML is adding value (Sharpe improvement over rule-only), the last layer is sizing. This is where the real edge compounds.
Fractional Kelly by signal confidence. For each signal, the Kelly fraction is f = (p×b − q) / b where p is calibrated win probability, q = 1−p, and b = PT_size/SL_size = 2.0. Use 25% Kelly (quarter-Kelly) to account for model error. This means high-confidence signals get larger positions, low-confidence signals get smaller ones, rather than binary on/off.
Cross-pair correlation management. EUR/USD, GBP/USD, and AUD/USD are highly correlated. When all three fire simultaneously, your effective exposure is much larger than it appears. Cap combined exposure to correlated pairs: if EUR/USD and GBP/USD both signal simultaneously, size each at 60% of normal rather than 100%.