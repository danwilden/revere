"""
Signal Review Page — Today's signals across all major pairs.

Loads the latest H1 features + ML model for each pair and displays
current signals (ML + rule-based) with confidence levels.
"""

from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

from forex_system.config import settings  # noqa: E402
from forex_system.data.candles import CandleFetcher  # noqa: E402
from forex_system.features.builders import FeaturePipeline  # noqa: E402
from forex_system.models.inference import MLInferenceEngine  # noqa: E402
from forex_system.strategy.rules import (  # noqa: E402
    BreakoutStrategy,
    MeanReversionStrategy,
    TrendFollowStrategy,
)

st.set_page_config(page_title="Signal Review", layout="wide")
st.header("🔍 Signal Review")
st.caption("Latest signals computed from the most recent completed bars.")

# ── Controls ──────────────────────────────────────────────────────────────────
col1, col2 = st.columns([2, 1])
with col1:
    selected_pairs = st.multiselect(
        "Instruments",
        options=settings.major_pairs,
        default=settings.major_pairs[:4],
    )
with col2:
    granularity = st.selectbox("Granularity", ["H1", "H4", "D"], index=0)
    lookback_bars = st.number_input("Lookback bars", min_value=100, max_value=500, value=200)

if not selected_pairs:
    st.warning("Select at least one instrument.")
    st.stop()


# ── Signal generation ─────────────────────────────────────────────────────────
@st.cache_data(ttl=300)  # cache 5 minutes
def get_latest_signals(pairs: list, gran: str, n_bars: int) -> pd.DataFrame:
    fetcher = CandleFetcher()
    pipeline = FeaturePipeline(horizon=1)
    trend_strat = TrendFollowStrategy()
    mr_strat = MeanReversionStrategy()
    bo_strat = BreakoutStrategy()

    records = []
    for pair in pairs:
        try:
            # Fetch recent bars
            from datetime import datetime, timedelta
            end = datetime.utcnow()
            # Rough lookback: n_bars × granularity hours
            hours_back = {"H1": 1, "H4": 4, "D": 24}.get(gran, 1) * n_bars
            start = end - timedelta(hours=hours_back)

            raw = fetcher.fetch(pair, gran, start=start, end=end, use_cache=False)
            if raw.empty or len(raw) < 60:
                continue
            raw = raw[raw["complete"]]

            features = pipeline.build(raw, include_labels=False)
            trend_out = trend_strat.generate(raw)
            mr_out = mr_strat.generate(raw)
            bo_out = bo_strat.generate(raw)

            # Last completed bar
            last_trend = int(trend_out["signal"].iloc[-1])
            last_mr = int(mr_out["signal"].iloc[-1])
            last_bo = int(bo_out["signal"].iloc[-1])
            last_stop = float(trend_out["stop_distance"].iloc[-1])

            # Rule consensus
            vote = last_trend + last_mr + last_bo
            rule_signal = 1 if vote >= 2 else (-1 if vote <= -2 else 0)

            # ML signal (if model exists)
            ml_prob = None
            ml_signal = 0
            try:
                engine = MLInferenceEngine.load_latest(pair, gran)
                ml_out = engine.predict(features)
                ml_prob = float(ml_out["ml_prob"].iloc[-1])
                ml_signal = int(ml_out["ml_signal"].iloc[-1])
            except FileNotFoundError:
                pass

            records.append(
                {
                    "Instrument": pair,
                    "Trend": last_trend,
                    "Mean Rev.": last_mr,
                    "Breakout": last_bo,
                    "Rule Signal": rule_signal,
                    "ML Prob": round(ml_prob, 3) if ml_prob is not None else "—",
                    "ML Signal": ml_signal,
                    "Stop Dist.": round(last_stop, 5),
                    "Last Close": round(float(raw["close"].iloc[-1]), 5),
                    "Last Bar": raw.index[-1].strftime("%Y-%m-%d %H:%M"),
                }
            )
        except Exception as exc:
            records.append({"Instrument": pair, "Error": str(exc)})

    return pd.DataFrame(records)


with st.spinner("Fetching latest bars and computing signals..."):
    df_signals = get_latest_signals(selected_pairs, granularity, lookback_bars)

if df_signals.empty:
    st.warning("No signal data available.")
    st.stop()

# ── Display table ─────────────────────────────────────────────────────────────
SIGNAL_COLORS = {1: "🔼", -1: "🔽", 0: "▬"}


def fmt_signal(v):
    if isinstance(v, int):
        return SIGNAL_COLORS.get(v, str(v))
    return str(v)


df_display = df_signals.copy()
for col in ["Trend", "Mean Rev.", "Breakout", "Rule Signal", "ML Signal"]:
    if col in df_display.columns:
        df_display[col] = df_display[col].apply(fmt_signal)

st.dataframe(df_display, use_container_width=True, hide_index=True)

if st.button("🔄 Refresh Signals"):
    st.cache_data.clear()
    st.rerun()
