"""
Dashboard Page — Live account metrics and open positions.

Refreshes every 30 seconds via st.cache_data(ttl=30).
Shows NAV, balance, margin used, and open position table.
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

from forex_system.execution.broker import OandaBroker  # noqa: E402

st.set_page_config(page_title="Dashboard", layout="wide")
st.header("📊 Dashboard")


@st.cache_data(ttl=30)
def get_account_summary() -> dict:
    return OandaBroker().get_account_summary()


@st.cache_data(ttl=30)
def get_open_positions() -> list[dict]:
    return OandaBroker().get_open_positions()


# ── Account metrics ────────────────────────────────────────────────────────────
try:
    summary = get_account_summary()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("NAV", f"${summary['nav']:,.2f}")
    col2.metric("Balance", f"${summary['balance']:,.2f}")
    col3.metric(
        "Unrealized P&L",
        f"${summary['unrealized_pnl']:,.2f}",
        delta=f"${summary['unrealized_pnl']:,.2f}",
    )
    col4.metric("Margin Used", f"${summary['margin_used']:,.2f}")
    col5.metric("Open Positions", summary["open_position_count"])

    # Margin usage bar
    total_margin = summary["margin_used"] + summary["margin_available"]
    margin_pct = summary["margin_used"] / total_margin if total_margin > 0 else 0
    st.progress(margin_pct, text=f"Margin used: {margin_pct:.1%}")

except Exception as exc:
    st.error(f"Could not fetch account data: {exc}")
    st.stop()

st.divider()

# ── Open positions table ───────────────────────────────────────────────────────
st.subheader("Open Positions")

positions = get_open_positions()
if positions:
    df_pos = pd.DataFrame(positions)
    df_pos["direction"] = df_pos["net_units"].apply(
        lambda u: "🔼 Long" if u > 0 else "🔽 Short"
    )
    df_pos["unrealized_pnl"] = df_pos["unrealized_pnl"].apply(
        lambda v: f"${v:,.2f}"
    )
    st.dataframe(
        df_pos[
            [
                "instrument",
                "direction",
                "net_units",
                "avg_price",
                "unrealized_pnl",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No open positions.")

# ── Auto-refresh ───────────────────────────────────────────────────────────────
st.caption("Auto-refreshes every 30 seconds. Reload page for latest data.")
if st.button("🔄 Refresh Now"):
    st.cache_data.clear()
    st.rerun()
