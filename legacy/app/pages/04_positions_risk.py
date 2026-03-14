"""
Positions & Risk Page — Open positions, margin status, and exposure guard.

Shows:
    - Open positions with unrealized P&L
    - Margin utilization gauge
    - ExposureGuard status checks
    - Close position controls
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

from forex_system.config import settings  # noqa: E402
from forex_system.execution.broker import OandaBroker  # noqa: E402
from forex_system.execution.portfolio import PortfolioManager  # noqa: E402
from forex_system.risk.leverage import DrawdownThrottler, ExposureGuard  # noqa: E402

st.set_page_config(page_title="Positions & Risk", layout="wide")
st.header("🛡️ Positions & Risk")


@st.cache_data(ttl=15)
def get_state() -> tuple[dict, list]:
    broker = OandaBroker()
    return broker.get_account_summary(), broker.get_open_positions()


# ── Account state ─────────────────────────────────────────────────────────────
try:
    summary, positions = get_state()
except Exception as exc:
    st.error(f"Cannot fetch account state: {exc}")
    st.stop()

# Margin gauge
total_margin = summary["margin_used"] + summary["margin_available"]
margin_pct = summary["margin_used"] / total_margin if total_margin > 0 else 0

col_l, col_r = st.columns([2, 1])

with col_l:
    st.subheader("Margin Usage")
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=margin_pct * 100,
            number={"suffix": "%"},
            title={"text": "Margin Used"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "steelblue"},
                "steps": [
                    {"range": [0, 25], "color": "#d4edda"},
                    {"range": [25, 35], "color": "#fff3cd"},
                    {"range": [35, 100], "color": "#f8d7da"},
                ],
                "threshold": {
                    "line": {"color": "red", "width": 4},
                    "thickness": 0.75,
                    "value": settings.max_margin_usage_pct * 100,
                },
            },
        )
    )
    fig.update_layout(height=250, margin=dict(t=20, b=20, l=20, r=20))
    st.plotly_chart(fig, use_container_width=True)

with col_r:
    st.subheader("Account Summary")
    st.metric("NAV", f"${summary['nav']:,.2f}")
    st.metric("Open Positions", summary["open_position_count"])
    st.metric("Margin Available", f"${summary['margin_available']:,.2f}")

st.divider()

# ── Open positions ─────────────────────────────────────────────────────────────
st.subheader("Open Positions")

if positions:
    df_pos = pd.DataFrame(positions)
    df_pos["direction"] = df_pos["net_units"].apply(
        lambda u: "Long" if u > 0 else "Short"
    )
    df_pos["pnl_fmt"] = df_pos["unrealized_pnl"].apply(
        lambda v: f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"
    )

    st.dataframe(
        df_pos[["instrument", "direction", "net_units", "avg_price", "pnl_fmt"]].rename(
            columns={
                "net_units": "Units",
                "avg_price": "Avg Price",
                "pnl_fmt": "Unrealized P&L",
                "direction": "Direction",
                "instrument": "Instrument",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # Close individual position
    st.subheader("Close Position")
    instruments_open = [p["instrument"] for p in positions]
    close_instrument = st.selectbox("Select instrument to close", instruments_open)
    close_side = st.radio("Close side", ["ALL", "LONG", "SHORT"], horizontal=True)

    if st.button(f"Close {close_instrument} ({close_side})", type="secondary"):
        if settings.oanda_env == "live":
            st.warning("You are in LIVE mode. This will close a real position.")
        try:
            broker = OandaBroker()
            broker.close_position(close_instrument, close_side)
            st.success(f"Closed {close_instrument} {close_side}")
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Close failed: {exc}")
else:
    st.info("No open positions.")

st.divider()

# ── Risk guard status ─────────────────────────────────────────────────────────
st.subheader("Risk Guard Status")

current_notional = sum(abs(p["net_units"]) for p in positions)
guard = ExposureGuard()

notional_cap = summary["nav"] * guard.max_gross_multiple
notional_pct = current_notional / notional_cap if notional_cap > 0 else 0

c1, c2, c3 = st.columns(3)
c1.metric(
    "Gross Exposure",
    f"${current_notional:,.0f}",
    delta=f"Cap: ${notional_cap:,.0f}",
)
c2.metric(
    "Notional / Equity",
    f"{current_notional / summary['nav']:.2f}×" if summary["nav"] > 0 else "—",
    delta=f"Cap: {guard.max_gross_multiple}×",
)
c3.metric(
    "Margin %",
    f"{margin_pct:.1%}",
    delta=f"Cap: {guard.max_margin_pct:.0%}",
    delta_color="inverse",
)

if margin_pct > guard.max_margin_pct:
    st.error("⚠️ Margin usage ABOVE cap — new orders will be blocked.")
elif notional_pct > 0.9:
    st.warning("⚠️ Gross exposure approaching cap.")
else:
    st.success("✅ All risk guards within limits.")

if st.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()
