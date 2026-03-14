"""
Order Staging Page — Position sizing calculator + two-step execution.

Two-step flow:
    Step 1: Fill in order parameters → see risk preview
    Step 2: Review and confirm → order sent to PortfolioManager

All orders go through PortfolioManager.check_and_place() which enforces
all exposure and margin limits before execution.
"""

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

from forex_system.config import settings  # noqa: E402
from forex_system.data.instruments import registry  # noqa: E402
from forex_system.execution.orders import OrderRequest  # noqa: E402
from forex_system.execution.portfolio import PortfolioManager  # noqa: E402
from forex_system.risk.sizing import calculate_units  # noqa: E402

st.set_page_config(page_title="Order Staging", layout="wide")
st.header("📋 Order Staging")

# Safety banner for live mode
if settings.oanda_env == "live":
    st.error("⚠️ **LIVE MODE** — Orders will execute with real money!")
else:
    st.success("✅ Practice mode — safe to test")

st.divider()

# ── Step 1: Order Parameters ───────────────────────────────────────────────────
st.subheader("Step 1: Configure Order")

col1, col2, col3 = st.columns(3)
with col1:
    instrument = st.selectbox("Instrument", settings.major_pairs)
    direction = st.radio("Direction", ["Long", "Short"], horizontal=True)

with col2:
    current_price = st.number_input("Current Price", min_value=0.0001, value=1.0850, step=0.0001, format="%.5f")
    stop_distance_pips = st.number_input("Stop Distance (pips)", min_value=1.0, value=20.0, step=1.0)

with col3:
    equity = st.number_input("Account Equity (USD)", min_value=100.0, value=10_000.0, step=100.0)
    risk_pct = st.slider("Risk % per trade", min_value=0.1, max_value=1.0, value=0.5, step=0.1) / 100

# ── Position sizing preview ────────────────────────────────────────────────────
st.divider()
st.subheader("Sizing Preview")

try:
    meta = registry.get(instrument)
    pip_size = meta.pip_size
    stop_distance = stop_distance_pips * pip_size

    units = calculate_units(
        equity=equity,
        risk_pct=risk_pct,
        stop_distance=stop_distance,
        instrument=instrument,
        current_price=current_price,
    )
    stop_price = (
        current_price - stop_distance
        if direction == "Long"
        else current_price + stop_distance
    )
    risk_amount = equity * risk_pct
    notional = units * current_price
    effective_leverage = notional / equity

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Units", f"{units:,}")
    c2.metric("$ at Risk", f"${risk_amount:.2f}")
    c3.metric("Stop Price", f"{stop_price:.5f}")
    c4.metric("Notional", f"${notional:,.0f}")

    st.info(
        f"Effective leverage: **{effective_leverage:.1f}×** | "
        f"Stop: {stop_distance_pips:.0f} pips ({stop_distance:.5f} price units)"
    )

except Exception as exc:
    st.error(f"Sizing error: {exc}")
    st.stop()

# ── Step 2: Confirm and Execute ────────────────────────────────────────────────
st.divider()
st.subheader("Step 2: Review and Execute")

with st.expander("⚠️ Order confirmation required — click to expand"):
    st.write(
        f"**Order Summary:**\n"
        f"- Instrument: `{instrument}`\n"
        f"- Direction: `{direction}`\n"
        f"- Units: `{units:,}`\n"
        f"- Stop Loss: `{stop_price:.5f}`\n"
        f"- Risk: `${risk_amount:.2f}` ({risk_pct:.1%} of equity)\n"
        f"- Environment: `{settings.oanda_env.upper()}`"
    )

    col_confirm, col_cancel = st.columns(2)

    with col_confirm:
        confirmed = st.button(
            f"✅ CONFIRM — Place {direction.upper()} {units:,} {instrument}",
            type="primary",
        )

    with col_cancel:
        if st.button("❌ Cancel"):
            st.info("Order cancelled.")

    if confirmed:
        signed_units = units if direction == "Long" else -units

        order = OrderRequest(
            instrument=instrument,
            units=signed_units,
            order_type="MARKET",
            stop_loss_price=stop_price,
            client_order_id=f"app_{instrument}_{direction[:1]}",
        )

        with st.spinner("Placing order..."):
            try:
                pm = PortfolioManager()
                result = pm.check_and_place(order, new_notional=notional)

                if result is None:
                    st.error("Order blocked by risk guard. Check Positions & Risk page.")
                else:
                    fill = result.get("orderFillTransaction", {})
                    fill_price = fill.get("price", "—")
                    st.success(
                        f"✅ Order filled: {instrument} {signed_units:+,} units @ {fill_price}"
                    )
            except Exception as exc:
                st.error(f"Order failed: {exc}")
