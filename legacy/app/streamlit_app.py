"""
OANDA FX Trading System — Streamlit Operations Interface

Entry point for the app. Handles:
- Page configuration
- Environment indicator (practice = safe, live = warning)
- Navigation sidebar

Run with:
    streamlit run app/streamlit_app.py
"""

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Load .env before any forex_system imports so settings picks up credentials
load_dotenv(Path(__file__).parents[1] / ".env", override=True)

from forex_system.config import settings  # noqa: E402

st.set_page_config(
    page_title="FX Trading System",
    page_icon="💱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar: environment indicator ────────────────────────────────────────────
with st.sidebar:
    if settings.oanda_env == "practice":
        st.success("**ENV: PRACTICE** (safe mode)", icon="✅")
    else:
        st.error("**ENV: LIVE** — real money at risk!", icon="⚠️")

    st.caption(f"Account: ...{settings.oanda_account_id[-6:]}")
    st.divider()
    st.markdown(
        "**Pages**\n"
        "- Dashboard\n"
        "- Signal Review\n"
        "- Order Staging\n"
        "- Positions & Risk\n"
        "- Run Logs"
    )

# ── Main landing page ─────────────────────────────────────────────────────────
st.title("OANDA FX Trading System")
st.caption(
    "Research-to-production systematic FX trading pipeline. "
    "Use the sidebar to navigate between pages."
)

col1, col2, col3 = st.columns(3)

with col1:
    st.info(
        "**Dashboard**\n\n"
        "Live account metrics: NAV, balance, margin, open positions.",
        icon="📊",
    )

with col2:
    st.info(
        "**Signal Review**\n\n"
        "Today's ML + rule-based signals across all major pairs.",
        icon="🔍",
    )

with col3:
    st.info(
        "**Order Staging**\n\n"
        "Size calculator + two-step confirm flow with risk checks.",
        icon="📋",
    )

col4, col5 = st.columns(2)

with col4:
    st.info(
        "**Positions & Risk**\n\n"
        "Open positions, margin usage, exposure guard status.",
        icon="🛡️",
    )

with col5:
    st.info(
        "**Run Logs**\n\n"
        "Structured JSONL audit trail of all signals, orders, and fills.",
        icon="📁",
    )
