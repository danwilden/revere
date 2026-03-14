"""
Run Logs Page — Browse JSONL audit logs from data/reports/.

Displays backtest tear sheets, paper trade logs, and execution session logs.
Each .jsonl file in data/reports/ represents one run session.
"""

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env", override=True)

from forex_system.config import settings  # noqa: E402
from forex_system.monitoring.reporting import TearSheetWriter  # noqa: E402

st.set_page_config(page_title="Run Logs", layout="wide")
st.header("📁 Run Logs")

# ── Available reports ─────────────────────────────────────────────────────────
writer = TearSheetWriter()
reports = writer.list_reports()

if not reports:
    st.info(
        "No reports found yet. Run a backtest notebook (05_walk_forward_backtest.ipynb) "
        "to generate reports."
    )
    st.stop()

# ── Select report ─────────────────────────────────────────────────────────────
selected_report = st.selectbox("Select Report", reports, index=0)

try:
    metrics = writer.load_metrics(selected_report)
except Exception as exc:
    st.error(f"Could not load report: {exc}")
    st.stop()

st.divider()

# ── Metrics summary ────────────────────────────────────────────────────────────
st.subheader(f"Metrics: `{selected_report}`")

m_col1, m_col2, m_col3, m_col4 = st.columns(4)
m_col1.metric("CAGR", f"{metrics.get('cagr', 0):.1%}")
m_col2.metric("Sharpe", f"{metrics.get('sharpe', 0):.2f}")
m_col3.metric("Max Drawdown", f"{metrics.get('max_drawdown', 0):.1%}")
m_col4.metric("Total Return", f"{metrics.get('total_return', 0):.1%}")

m_col5, m_col6, m_col7, m_col8 = st.columns(4)
m_col5.metric("Hit Rate", f"{metrics.get('hit_rate', 0):.1%}")
m_col6.metric("Profit Factor", f"{metrics.get('profit_factor', 0):.2f}")
m_col7.metric("Payoff Ratio", f"{metrics.get('payoff_ratio', 0):.2f}")
m_col8.metric("N Trades", metrics.get("n_trades", metrics.get("n_periods", "—")))

st.divider()

# ── Equity curve ───────────────────────────────────────────────────────────────
equity_path = settings.data_reports / f"{selected_report}_equity.csv"
if equity_path.exists():
    st.subheader("Equity Curve")
    eq_df = pd.read_csv(equity_path, index_col=0, parse_dates=True)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=eq_df.index,
            y=eq_df["equity"],
            mode="lines",
            name="Equity",
            line=dict(color="steelblue", width=2),
        )
    )
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Equity (USD)",
        height=350,
        margin=dict(t=10, b=40, l=60, r=10),
    )
    st.plotly_chart(fig, use_container_width=True)

# ── Trades table ───────────────────────────────────────────────────────────────
trades_path = settings.data_reports / f"{selected_report}_trades.csv"
if trades_path.exists():
    st.subheader("Trade Log")
    trades_df = pd.read_csv(trades_path)
    if not trades_df.empty:
        trades_df["pnl"] = trades_df["pnl"].apply(
            lambda v: f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"
        )
        st.dataframe(trades_df, use_container_width=True, height=300)

# ── Raw metrics JSON ───────────────────────────────────────────────────────────
with st.expander("Raw Metrics JSON"):
    st.json(metrics)

# ── JSONL session logs ─────────────────────────────────────────────────────────
st.divider()
st.subheader("Session Logs (JSONL)")

jsonl_files = sorted(settings.data_reports.glob("*.jsonl"))
if jsonl_files:
    selected_log = st.selectbox(
        "Log file", [f.name for f in jsonl_files], index=0
    )
    log_path = settings.data_reports / selected_log

    n_lines = st.slider("Lines to show", 10, 200, 50)
    lines = []
    try:
        with open(log_path) as fh:
            for i, line in enumerate(fh):
                if i >= n_lines:
                    break
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    lines.append({"raw": line.strip()})
    except Exception as exc:
        st.error(f"Could not read log: {exc}")

    if lines:
        st.dataframe(pd.json_normalize(lines), use_container_width=True, height=300)
else:
    st.info("No session logs found. Logs are written when running the paper trading notebook.")
