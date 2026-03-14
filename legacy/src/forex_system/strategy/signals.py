"""
Signal routing and data contracts.

v2.4: SignalAggregator replaced by RegimeRouter.
  The old majority-vote approach paralysed signals when trend and mean-reversion
  strategies disagreed (which they almost always do — they are designed for
  mutually exclusive market regimes). RegimeRouter solves this by running only
  one strategy per bar, selected by the detected market regime.

  SignalAggregator is preserved below in commented form for reference.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from loguru import logger


@dataclass
class SignalRecord:
    """Typed signal emitted by any strategy or ML model."""

    instrument: str
    timestamp: pd.Timestamp
    direction: int          # 1=long, -1=short, 0=flat
    strategy_name: str
    stop_distance: float
    confidence: float = 1.0   # Rules → 1.0; ML → predicted label_ev (ATR-normalized EV)
    source: str = "rule"       # "rule" | "ml"


# ── CHANGE 3 (v2.4): SignalAggregator commented out ───────────────────────────
# Replaced by RegimeRouter below. Reason: consensus voting with min_consensus=2
# silenced most signals because TrendFollowStrategy and MeanReversionStrategy
# almost always produce opposite directions (as designed — different regimes).
# The result was ~2.4% signal frequency vs the target 8-15%.
#
# class SignalAggregator:
#     """
#     Combines signals from multiple strategy outputs.
#
#     Phase 1: simple majority vote with configurable min_consensus.
#     Phase 2 (future): weighted consensus or meta-model filtering.
#
#     Usage:
#         agg = SignalAggregator(min_consensus=2)
#         combined = agg.aggregate({
#             "trend": trend_df,
#             "mr": mr_df,
#             "breakout": bo_df,
#         })
#         # combined has columns: signal, avg_stop_distance, n_strategies_agree
#     """
#
#     def __init__(self, min_consensus: int = 2) -> None:
#         self.min_consensus = min_consensus
#
#     def aggregate(
#         self, strategy_outputs: dict[str, pd.DataFrame]
#     ) -> pd.DataFrame:
#         if not strategy_outputs:
#             raise ValueError("strategy_outputs must not be empty")
#
#         signals = pd.DataFrame(
#             {name: df["signal"] for name, df in strategy_outputs.items()}
#         )
#         vote_sum = signals.sum(axis=1)
#         n_agree = (signals != 0).sum(axis=1)
#
#         combined = pd.Series(0, index=signals.index, name="signal")
#         combined[vote_sum >= self.min_consensus] = 1
#         combined[vote_sum <= -self.min_consensus] = -1
#
#         stops = pd.DataFrame(
#             {
#                 name: df["stop_distance"]
#                 for name, df in strategy_outputs.items()
#                 if "stop_distance" in df.columns
#             }
#         )
#         avg_stop = stops.mean(axis=1) if not stops.empty else pd.Series(
#             0.0, index=signals.index
#         )
#
#         return pd.DataFrame(
#             {
#                 "signal": combined,
#                 "avg_stop_distance": avg_stop,
#                 "n_strategies_agree": n_agree,
#             }
#         )
# ──────────────────────────────────────────────────────────────────────────────


# ── CHANGE 3 (v2.4): Regime classification and routing ────────────────────────

REGIME_TRENDING  = "TRENDING"
REGIME_RANGING   = "RANGING"
REGIME_BREAKOUT  = "BREAKOUT"
REGIME_UNDEFINED = "UNDEFINED"

# ── CHANGE 8 (v2.5): Hysteresis thresholds ────────────────────────────────────
# Entry thresholds live in classify_regime() (adx_trending_thresh=25, adx_ranging_thresh=20).
# Exit thresholds are wider, creating a dead-zone that prevents oscillation around the
# boundary ADX values (e.g., ADX bouncing between 24 and 26 no longer churns the regime).
_TRENDING_EXIT_THRESH: float = 18.0  # exit TRENDING only when ADX drops below 18
_RANGING_EXIT_THRESH:  float = 23.0  # exit RANGING only when ADX rises above 23
_REGIME_CONFIRM_BARS:  int   = 2     # consecutive bars required before regime switches


def classify_regime(
    adx: pd.Series,
    vol_ratio: pd.Series,
    raw_atr: pd.Series,
    adx_trending_thresh: float = 25.0,
    adx_ranging_thresh: float = 20.0,
    vol_expansion_factor: float = 1.15,
) -> pd.Series:
    """
    CHANGE 3 (v2.4): Classify each bar into one of 4 market regimes.

    TRENDING:     ADX > adx_trending_thresh AND vol_ratio_10_60 > 0.9
                  → TrendFollowStrategy
    RANGING:      ADX < adx_ranging_thresh  AND vol_ratio_10_60 < 1.2
                  → MeanReversionStrategy
    BREAKOUT:     ADX crossing up through adx_ranging_thresh
                  AND ATR > mean_ATR(20) × vol_expansion_factor
                  → BreakoutStrategy
    UNDEFINED:    all other bars → no trade

    Args:
        adx:                  ADX(14) series (from FeaturePipeline, column adx_14).
        vol_ratio:            vol_ratio_10_60 (short/long vol ratio).
        raw_atr:              ATR(14) series.
        adx_trending_thresh:  ADX threshold for trending regime (default 25).
        adx_ranging_thresh:   ADX threshold for ranging / breakout boundary (default 20).
        vol_expansion_factor: ATR expansion multiplier for breakout detection (default 1.15).

    Returns:
        pd.Series of regime strings, same index as adx.

    Tuning note:
        If signal frequency is below 5%, lower adx_trending_thresh to 22 and
        adx_ranging_thresh to 18 and re-run.
    """
    mean_atr_20 = raw_atr.rolling(20).mean()
    adx_cross_up = (adx >= adx_ranging_thresh) & (adx.shift(1) < adx_ranging_thresh)

    regime = pd.Series(REGIME_UNDEFINED, index=adx.index, dtype=object)

    # Apply in priority order (BREAKOUT overrides RANGING if both match)
    ranging_mask  = (adx < adx_ranging_thresh) & (vol_ratio < 1.2)
    trending_mask = (adx > adx_trending_thresh) & (vol_ratio > 0.9)
    breakout_mask = adx_cross_up & (raw_atr > mean_atr_20 * vol_expansion_factor)

    regime[ranging_mask]  = REGIME_RANGING
    regime[trending_mask] = REGIME_TRENDING
    regime[breakout_mask] = REGIME_BREAKOUT   # breakout takes highest priority

    return regime


class RegimeStateTracker:
    """
    CHANGE 8 (v2.5): Apply hysteresis to a raw regime series.

    The raw ``classify_regime()`` labels each bar independently using hard ADX
    thresholds — a single bar crossing 25 flips to TRENDING even if it reverts
    immediately.  This class wraps the raw output with two-sided hysteresis bands
    and a confirmation requirement so that regime switches only occur after the
    evidence has been consistent for ``_REGIME_CONFIRM_BARS`` consecutive bars.

    Hysteresis thresholds:
        TRENDING:  enter when raw==TRENDING (ADX>25); exit only when ADX < 18
        RANGING:   enter when raw==RANGING  (ADX<20); exit only when ADX > 23
        BREAKOUT:  always immediate — no confirmation required
        UNDEFINED: default until a regime is confirmed

    The dead-zone between the entry and exit thresholds (e.g., ADX 18-25 for
    TRENDING) is the "sticky" band where the current regime persists.
    """

    def apply(self, raw: pd.Series, adx: pd.Series) -> pd.Series:
        """
        Args:
            raw: output of classify_regime() — series of REGIME_* strings.
            adx: raw ADX(14) values aligned to the same index.

        Returns:
            Hysteresis-smoothed regime series (same index as raw).
        """
        confirmed = pd.Series(REGIME_UNDEFINED, index=raw.index, dtype=object)
        current = REGIME_UNDEFINED
        pending = REGIME_UNDEFINED
        count = 0

        for i in range(len(raw)):
            raw_i  = raw.iloc[i]
            adx_i  = float(adx.iloc[i])

            # BREAKOUT: always confirmed immediately; resets any pending state
            if raw_i == REGIME_BREAKOUT:
                current = REGIME_BREAKOUT
                pending = REGIME_UNDEFINED
                count   = 0
                confirmed.iloc[i] = current
                continue

            # Determine candidate regime given current state + hysteresis thresholds
            if current == REGIME_TRENDING:
                # Stay TRENDING until ADX falls below the exit threshold
                candidate = raw_i if adx_i < _TRENDING_EXIT_THRESH else REGIME_TRENDING
            elif current == REGIME_RANGING:
                # Stay RANGING until ADX rises above the exit threshold
                candidate = raw_i if adx_i > _RANGING_EXIT_THRESH else REGIME_RANGING
            elif current == REGIME_BREAKOUT:
                # Exit BREAKOUT to wherever the raw classifier points
                candidate = raw_i
            else:
                # UNDEFINED: enter via raw entry conditions (ADX>25 or ADX<20)
                candidate = raw_i

            # Confirmation: require _REGIME_CONFIRM_BARS consecutive bars before switching
            if candidate != current:
                if candidate == pending:
                    count += 1
                else:
                    pending = candidate
                    count   = 1
                if count >= _REGIME_CONFIRM_BARS:
                    current = pending
                    pending = REGIME_UNDEFINED
                    count   = 0
            else:
                pending = REGIME_UNDEFINED
                count   = 0

            confirmed.iloc[i] = current

        return confirmed


# ── CHANGE 9 (v2.5): Maximum stop width ───────────────────────────────────────
MAX_STOP_ATR: float = 2.5  # trades with stop > this × ATR are skipped entirely


class RegimeRouter:
    """
    CHANGE 3 (v2.4): Route each bar to a single strategy based on market regime.

    Replaces SignalAggregator. Only one strategy type runs per bar:
        TRENDING  → TrendFollowStrategy
        RANGING   → MeanReversionStrategy
        BREAKOUT  → BreakoutStrategy
        UNDEFINED → flat (no trade)

    Stop distances from individual strategies are overridden with structural
    stops (CHANGE 2) anchored to recent swing highs/lows.

    The D1 regime gate (trend_regime_50d) is applied to TRENDING and BREAKOUT
    signals only — mean reversion trades intentionally run counter to daily trend.

    Target signal frequency: 8-15% of bars. If < 5%, lower ADX thresholds:
        adx_trending_thresh → 22, adx_ranging_thresh → 18, re-backtest.

    Usage:
        from forex_system.strategy.signals import RegimeRouter
        router = RegimeRouter()
        signal_df = router.route(
            feature_df,         # output of FeaturePipeline.build()
            instrument="USD_JPY",
            pip_size=0.01,
            equity=10_000.0,
        )
        # signal_df has: open, high, low, close, signal, stop_distance, atr, units
    """

    def __init__(
        self,
        adx_trending_thresh: float = 25.0,
        adx_ranging_thresh: float = 20.0,
        structural_lookback: int = 10,
        use_hysteresis: bool = True,          # CHANGE 8: False reproduces v2.4 behaviour
        max_stop_atr_mult: float = MAX_STOP_ATR,  # CHANGE 9: skip trades with wider stop
        d1_gate_mode: str = "entry_only",     # CHANGE 10: "full"|"entry_only"|"disabled"
        # Phase 1 pivot additions
        adx_floor: float | None = None,       # Phase 1C: hard noise floor (e.g. 15.0)
        cost_gate_enabled: bool = False,       # Phase 1B: skip when ATR < 4× round-trip cost
        cost_gate_pips: dict | None = None,    # Phase 1B: {"USD_JPY": 13.6, "USD_CHF": 16.0}
    ) -> None:
        if d1_gate_mode not in ("full", "entry_only", "disabled"):
            raise ValueError(
                f"d1_gate_mode must be 'full', 'entry_only', or 'disabled'; got {d1_gate_mode!r}"
            )
        self.adx_trending_thresh = adx_trending_thresh
        self.adx_ranging_thresh  = adx_ranging_thresh
        self.structural_lookback = structural_lookback
        self.use_hysteresis      = use_hysteresis
        self.max_stop_atr_mult   = max_stop_atr_mult
        self.d1_gate_mode        = d1_gate_mode
        self.adx_floor           = adx_floor
        self.cost_gate_enabled   = cost_gate_enabled
        self.cost_gate_pips      = cost_gate_pips or {}
        # Track filter counts per route() call for notebook inspection
        self.n_adx_filtered:  int = 0
        self.n_cost_filtered: int = 0

    def route(
        self,
        df: pd.DataFrame,
        instrument: str,
        pip_size: float = 0.0001,
        equity: float = 10_000.0,
        risk_pct: float = 0.005,
    ) -> pd.DataFrame:
        """
        Generate a signal DataFrame from pre-built feature matrix.

        Args:
            df:         Output of FeaturePipeline.build() — must contain OHLCV
                        columns (open, high, low, close) plus adx_14, atr_14,
                        vol_ratio_10_60, and optionally trend_regime_50d.
            instrument: OANDA instrument string (e.g. "USD_JPY"). Used for
                        pip value calculation in position sizing.
            pip_size:   Pip size in price units (0.0001 for most, 0.01 for JPY).
            equity:     Current account equity in USD (for unit sizing).
            risk_pct:   Fraction of equity to risk per trade (default 0.5%).

        Returns:
            DataFrame (same index as df) with columns:
                open, high, low, close  — OHLCV pass-through
                signal                  — 1=long, -1=short, 0=flat
                stop_distance           — structural stop in price units
                atr                     — ATR(14) for trail stop in engine
                units                   — integer position size
        """
        from forex_system.strategy.rules import (
            BreakoutStrategy,
            MeanReversionStrategy,
            TrendFollowStrategy,
        )
        from forex_system.risk.stops import compute_structural_stop
        from forex_system.risk.sizing import calculate_units, pip_value_per_unit_usd

        # --- Strategy instances (use TF params that worked in v2.3 notebook) ---
        trend_strat = TrendFollowStrategy(fast_ema=8, slow_ema=21,
                                          adx_window=14, adx_threshold=self.adx_trending_thresh)
        mr_strat    = MeanReversionStrategy()
        bo_strat    = BreakoutStrategy()

        # --- Pre-computed feature columns ---
        adx_col       = df["adx_14"]
        vol_ratio_col = df["vol_ratio_10_60"]
        raw_atr       = df["atr_14"]

        # --- Regime classification ---
        regime = classify_regime(
            adx_col, vol_ratio_col, raw_atr,
            adx_trending_thresh=self.adx_trending_thresh,
            adx_ranging_thresh=self.adx_ranging_thresh,
        )

        # --- CHANGE 8: Apply hysteresis smoothing to raw regime series ---
        if self.use_hysteresis:
            raw_regime = regime.copy()
            tracker = RegimeStateTracker()
            regime = tracker.apply(raw_regime, adx_col)
            n_changed = int((regime != raw_regime).sum())
            logger.debug(
                f"RegimeRouter | {instrument} | hysteresis changed {n_changed} bars"
            )

        # --- Generate individual strategy signals on the raw OHLCV data ---
        ohlcv = df[["open", "high", "low", "close"]].copy()
        # Volume column optional; strategies only use open/high/low/close
        trend_out = trend_strat.generate(ohlcv)
        mr_out    = mr_strat.generate(ohlcv)
        bo_out    = bo_strat.generate(ohlcv)

        # --- Route signal by regime ---
        signal = pd.Series(0, index=df.index, dtype=int)
        signal[regime == REGIME_TRENDING] = trend_out["signal"][regime == REGIME_TRENDING]
        signal[regime == REGIME_RANGING]  = mr_out["signal"][regime == REGIME_RANGING]
        signal[regime == REGIME_BREAKOUT] = bo_out["signal"][regime == REGIME_BREAKOUT]
        # REGIME_UNDEFINED bars remain 0

        # --- Phase 1C: ADX floor — hard noise floor before any regime/gate logic ---
        # ADX < 15 means insufficient directional energy to cover round-trip costs.
        # Runs BEFORE cost gate so cost gate skip rate is measured against post-floor signals.
        if self.adx_floor is not None:
            adx_too_low = (adx_col < self.adx_floor) & (signal != 0)
            self.n_adx_filtered = int(adx_too_low.sum())
            signal[adx_too_low] = 0
            if self.n_adx_filtered > 0:
                logger.info(
                    f"RegimeRouter | {instrument} | ADX_FLOOR<{self.adx_floor}: "
                    f"{self.n_adx_filtered} signals filtered"
                )

        # --- Phase 1B: Cost gate — skip when ATR(14) < 4× round-trip cost ---
        # Expected move proxy = ATR(14) × 1.0 in pips. Gate: atr_pips < cost_gate_pips[pair].
        # Denominator uses post-ADX-floor count (captured BEFORE zeroing) so skip rate is clean.
        if self.cost_gate_enabled and self.cost_gate_pips:
            gate_pips = self.cost_gate_pips.get(instrument, 0.0)
            if gate_pips > 0:
                atr_pips  = raw_atr / pip_size
                cost_mask = (atr_pips < gate_pips) & (signal != 0)
                total_pre_gate = int((signal != 0).sum())   # count BEFORE zeroing
                self.n_cost_filtered = int(cost_mask.sum())
                signal[cost_mask] = 0
                logger.info(
                    f"RegimeRouter | {instrument} | COST_GATE_FILTERED: "
                    f"{self.n_cost_filtered}/{total_pre_gate} signals "
                    f"(ATR < {gate_pips} pips)"
                )
                if total_pre_gate > 0 and self.n_cost_filtered / total_pre_gate > 0.40:
                    logger.warning(
                        f"RegimeRouter | {instrument} | cost gate skip rate "
                        f"{self.n_cost_filtered/total_pre_gate:.1%} > 40% — "
                        "ATR consistently too low for this timeframe"
                    )

        # --- CHANGE 10: D1 regime gate (mode-aware) ---
        # RANGING signals intentionally skip the gate — mean reversion runs counter-trend.
        if "trend_regime_50d" in df.columns and self.d1_gate_mode != "disabled":
            d1_gate_vals = df["trend_regime_50d"].fillna(0).astype(int)
            is_trend_or_bo = regime.isin([REGIME_TRENDING, REGIME_BREAKOUT])

            if self.d1_gate_mode == "full":
                # Original v2.4 behaviour: veto every contradicting bar, including
                # ongoing positions.
                mask = is_trend_or_bo & (signal != 0) & (signal != d1_gate_vals)
            else:  # "entry_only"
                # CHANGE 10: only check the gate at the bar where a new signal begins.
                # "new signal" = any bar where signal transitions from 0 or a different
                # direction (catches 0→±1 entries and ±1→∓1 reversals).
                signal_changed = signal != signal.shift(1).fillna(0)
                new_entry = (signal != 0) & signal_changed
                mask = is_trend_or_bo & new_entry & (signal != d1_gate_vals)

            signal[mask] = 0
            if mask.any():
                logger.debug(
                    f"D1 gate [{self.d1_gate_mode}] | {instrument} | "
                    f"filtered {int(mask.sum())} TRENDING/BREAKOUT signals"
                )
        elif "trend_regime_50d" not in df.columns:
            logger.warning("trend_regime_50d not in df — D1 regime gate skipped")

        # --- Structural stop (CHANGE 2) — overrides strategy ATR-based stops ---
        stop_dist = compute_structural_stop(
            df["high"], df["low"], df["close"],
            direction=signal,
            lookback=self.structural_lookback,
        )

        # --- CHANGE 9: Skip trades where structural stop > max_stop_atr_mult × ATR ---
        stop_cap     = self.max_stop_atr_mult * raw_atr
        stop_too_wide = (signal != 0) & (stop_dist > stop_cap)
        n_pre     = int((signal != 0).sum())
        n_skipped = int(stop_too_wide.sum())
        if n_skipped > 0:
            signal[stop_too_wide] = 0
            logger.info(
                f"RegimeRouter | {instrument} | STOP_TOO_WIDE: "
                f"{n_skipped}/{n_pre} signals skipped "
                f"(stop > {self.max_stop_atr_mult}×ATR)"
            )
            if n_pre > 0 and n_skipped / n_pre > 0.30:
                logger.warning(
                    f"RegimeRouter | {instrument} | STOP_TOO_WIDE skip rate "
                    f"{n_skipped/n_pre:.1%} > 30% — structural_lookback="
                    f"{self.structural_lookback} may be too long. "
                    "Consider reducing to 7 and re-running."
                )

        # --- Position sizing ---
        units_arr = np.zeros(len(df), dtype=int)
        close_arr = df["close"].to_numpy()
        stop_arr  = stop_dist.to_numpy()
        sig_arr   = signal.to_numpy()

        for i in range(len(df)):
            if sig_arr[i] == 0:
                continue
            if stop_arr[i] <= 0:
                continue
            units_arr[i] = int(
                calculate_units(
                    equity,
                    risk_pct,
                    float(stop_arr[i]),
                    instrument,
                    float(close_arr[i]),
                )
            )

        # --- Log signal frequency ---
        n_signals = int((signal != 0).sum())
        freq_pct  = 100.0 * n_signals / max(len(df), 1)
        logger.info(
            f"RegimeRouter | {instrument} | signals={n_signals}/{len(df)} "
            f"({freq_pct:.1f}%) | "
            f"trend={int((regime==REGIME_TRENDING).sum())} "
            f"ranging={int((regime==REGIME_RANGING).sum())} "
            f"breakout={int((regime==REGIME_BREAKOUT).sum())} "
            f"undefined={int((regime==REGIME_UNDEFINED).sum())}"
        )
        if freq_pct < 5.0:
            logger.warning(
                f"RegimeRouter | signal frequency {freq_pct:.1f}% < 5% target. "
                "Consider lowering adx_trending_thresh to 22 and "
                "adx_ranging_thresh to 18 and re-running."
            )

        return pd.DataFrame(
            {
                "open":          df["open"],
                "high":          df["high"],
                "low":           df["low"],
                "close":         df["close"],
                "signal":        signal.astype(int),
                "stop_distance": stop_dist,
                "atr":           raw_atr,   # passed to engine for trail stop (CHANGE 2)
                "units":         units_arr,
            },
            index=df.index,
        )
