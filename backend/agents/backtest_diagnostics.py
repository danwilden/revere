"""BacktestDiagnostics agent node — interprets backtest outcomes and recommends mutations.

Makes a single Bedrock call (no tool calls) to produce a structured DiagnosticSummary.
Retries once on parse failure; falls back to a hardcoded NO_EDGE default on second failure.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import botocore.exceptions
from pydantic import ValidationError

from backend.agents.providers.bedrock import BedrockAdapter
from backend.agents.providers.logging import AgentLogger
from backend.agents.state import AgentState
from backend.agents.tools.schemas import DiagnosticSummary, FailureTaxonomy

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

NODE_NAME = "backtest_diagnostics"
MAX_TOOL_RETRIES = 2
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]

SYSTEM_PROMPT = """You are a quantitative trading strategy diagnostician. Your role is to analyze
the results of a rules-based Forex strategy backtest and produce a structured diagnosis that
explains why the strategy performed as it did, and what specific changes are most likely to improve it.

You will receive:
- METRICS: overall and per-regime performance metrics
- TRADES: summary statistics from the trade log (count, win_rate, avg_pnl, avg_holding)
- EQUITY: summary of the equity curve (final_equity, max_drawdown_pct, recovery_ratio)
- STRATEGY: the rules_engine JSON definition that was tested
- INSTRUMENT and TIMEFRAME: the market context
- REGIME_CONTEXT: the HMM regime distribution

Your output must be a JSON object with this exact schema:
{
  "failure_taxonomy": "<one of: zero_trades | too_few_trades | excessive_drawdown | poor_sharpe |
                       overfitting_signal | wrong_regime_filter | entry_too_restrictive |
                       exit_too_early | exit_too_late | no_edge | positive>",
  "root_cause": "<2-3 sentence explanation of the primary performance driver>",
  "recommended_mutations": ["<specific actionable change 1>", "<specific actionable change 2>", ...],
  "confidence": <float 0.0-1.0>,
  "discard": <true if strategy has no recoverable path, false if mutations are viable>
}

For failure_taxonomy values:
- zero_trades: entry conditions never triggered (0 trades)
- too_few_trades: fewer than 20 trades (statistically insignificant)
- excessive_drawdown: max_drawdown_pct worse than -25%
- poor_sharpe: Sharpe ratio below 0.3
- overfitting_signal: high win_rate but low total PnL (spread/cost erosion)
- wrong_regime_filter: regime_label filter excludes most of the time period
- entry_too_restrictive: many AND conditions that rarely all trigger simultaneously
- exit_too_early: average holding period below 2 bars
- exit_too_late: large peak-to-valley drawdown within individual trades
- no_edge: random-looking equity curve with near-zero Sharpe
- positive: strategy is profitable, mutations are incremental improvements

For recommended_mutations:
- Be specific: "Change stop_atr_multiplier from 1.5 to 2.5" not "increase stop"
- Reference actual field values from the STRATEGY definition
- Limit to 3-5 mutations, ordered by expected impact

Set discard=true only if: strategy has been mutated more than 4 times (GENERATION >= 5)
AND still shows zero_trades, no_edge, or excessive_drawdown.
"""

_HARDCODED_FALLBACK = DiagnosticSummary(
    failure_taxonomy=FailureTaxonomy.NO_EDGE,
    root_cause="Automated diagnosis unavailable. Manual review required.",
    recommended_mutations=["Review entry conditions manually"],
    confidence=0.0,
    discard=False,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_message(state: AgentState) -> str:
    """Construct the diagnostics user message from AgentState."""
    instrument = state.get("instrument", "EUR_USD")
    timeframe = state.get("timeframe", "H4")
    generation = state.get("generation", 0)
    metrics = state.get("backtest_metrics") or {}
    trades: list[dict[str, Any]] = list(state.get("backtest_trades") or [])
    equity: list[dict[str, Any]] = list(state.get("equity_curve") or [])
    strategy_def = state.get("strategy_definition") or {}
    regime_context = state.get("regime_context")

    # Zero-trade detection prefix
    zero_trade_prefix = ""
    total_trades_metric = metrics.get("total_trades", None)
    if total_trades_metric == 0 or (not trades and total_trades_metric is None):
        zero_trade_prefix = (
            "ZERO_TRADE_CONTEXT: This strategy generated 0 trades. Focus entirely on why the entry conditions\n"
            "are too restrictive. The failure_taxonomy must be \"zero_trades\". Do not recommend discard=true\n"
            f"unless GENERATION >= 5.\n\n"
        )

    # Metrics block
    metrics_lines = "\n".join(
        f"  {k}: {v}" for k, v in sorted(metrics.items())
    ) or "  (none)"

    # Trades summary block
    if trades:
        pnls = [t.get("pnl", 0.0) for t in trades]
        holdings = [t.get("holding_period", 0) for t in trades]
        winning = sum(1 for p in pnls if p > 0)
        losing = len(pnls) - winning
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        avg_holding = sum(holdings) / len(holdings) if holdings else 0.0
        largest_loss = min(pnls) if pnls else 0.0
        largest_win = max(pnls) if pnls else 0.0
        trades_block = (
            f"  total_count: {len(trades)}\n"
            f"  winning_trades: {winning}\n"
            f"  losing_trades: {losing}\n"
            f"  avg_pnl: {avg_pnl:.4f}\n"
            f"  avg_holding_bars: {avg_holding:.1f}\n"
            f"  largest_loss: {largest_loss:.4f}\n"
            f"  largest_win: {largest_win:.4f}"
        )
    else:
        trades_block = "no trades recorded"

    # Equity summary block
    if equity:
        initial_eq = equity[0].get("equity", 0.0) if equity else 0.0
        final_eq = equity[-1].get("equity", 0.0) if equity else 0.0
        drawdowns = [e.get("drawdown", 0.0) for e in equity]
        max_dd = min(drawdowns) if drawdowns else 0.0
        recovery_ratio = (final_eq / initial_eq) if initial_eq else 1.0
        equity_block = (
            f"  initial_equity: {initial_eq:.2f}\n"
            f"  final_equity: {final_eq:.2f}\n"
            f"  max_drawdown_pct: {max_dd:.4f}\n"
            f"  recovery_ratio: {recovery_ratio:.4f}"
        )
    else:
        equity_block = "not available"

    regime_json = (
        json.dumps(regime_context, indent=2)
        if regime_context
        else "not available"
    )

    parts = [
        f"{zero_trade_prefix}INSTRUMENT: {instrument}",
        f"TIMEFRAME: {timeframe}",
        f"GENERATION: {generation}",
        "",
        "METRICS:",
        metrics_lines,
        "",
        "TRADES SUMMARY:",
        trades_block,
        "",
        "EQUITY SUMMARY:",
        equity_block,
        "",
        "STRATEGY:",
        json.dumps(strategy_def, indent=2),
        "",
        "REGIME_CONTEXT:",
        regime_json,
    ]
    return "\n".join(parts)


def _parse_diagnostic_summary(text: str) -> DiagnosticSummary:
    """Extract and validate DiagnosticSummary from LLM response text."""
    import re
    # Try JSON fence first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        raw = json.loads(fence_match.group(1))
    else:
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in response")
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw = json.loads(text[start : i + 1])
                    break
        else:
            raise ValueError("Malformed JSON — unmatched braces")
    return DiagnosticSummary.model_validate(raw)


async def _run_diagnostics(state: AgentState) -> dict[str, Any]:
    """Async implementation of backtest_diagnostics_node."""
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    trace_id = state.get("trace_id", "")
    prior_errors: list[str] = list(state.get("errors") or [])

    t_node_start = time.monotonic()
    _logger.node_enter(NODE_NAME, trace_id, list(state.keys()))

    adapter = BedrockAdapter()
    user_message_text = _build_user_message(state)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_message_text}]}
    ]

    # ── Single Bedrock call with throttle retry ─────────────────────────────
    result = None
    for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
        try:
            t_llm = time.monotonic()
            result = await adapter.converse(
                messages=messages,
                system_prompt=SYSTEM_PROMPT,
                tools=None,
                max_tokens=2048,
                temperature=0.0,
            )
            llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
            _logger.llm_call(
                adapter._model_id, trace_id, NODE_NAME,
                result.input_tokens, result.output_tokens, llm_latency_ms,
            )
            break
        except botocore.exceptions.ClientError as exc:
            err_code = exc.response.get("Error", {}).get("Code", "")
            if err_code == "ThrottlingException" and attempt < len(BEDROCK_THROTTLE_BACKOFF_SECONDS):
                await asyncio.sleep(BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt])
                continue
            duration_ms = int((time.monotonic() - t_node_start) * 1000)
            _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
            return {
                "next_node": "supervisor",
                "diagnosis_summary": "diagnostics_failed",
                "diagnostic_summary": None,
                "discard": False,
                "errors": prior_errors + [f"backtest_diagnostics: Bedrock error: {exc}"],
            }
    else:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "diagnosis_summary": "diagnostics_failed",
            "diagnostic_summary": None,
            "discard": False,
            "errors": prior_errors + ["backtest_diagnostics: ThrottlingException — max retries exhausted"],
        }

    # ── Parse DiagnosticSummary — retry once on failure ─────────────────────
    summary: DiagnosticSummary | None = None
    parse_error_msg: str | None = None

    try:
        summary = _parse_diagnostic_summary(result.content)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        parse_error_msg = str(exc)

    if summary is None:
        # Retry once with parse error feedback
        retry_messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": user_message_text}]},
            {"role": "assistant", "content": [{"text": result.content}]},
            {
                "role": "user",
                "content": [{
                    "text": (
                        f"Your previous response could not be parsed. "
                        f"Error: {parse_error_msg}. "
                        "Please output only the JSON object."
                    )
                }],
            },
        ]
        try:
            t_llm = time.monotonic()
            retry_result = await adapter.converse(
                messages=retry_messages,
                system_prompt=SYSTEM_PROMPT,
                tools=None,
                max_tokens=2048,
                temperature=0.0,
            )
            llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
            _logger.llm_call(
                adapter._model_id, trace_id, NODE_NAME,
                retry_result.input_tokens, retry_result.output_tokens, llm_latency_ms,
            )
            summary = _parse_diagnostic_summary(retry_result.content)
        except Exception:
            summary = _HARDCODED_FALLBACK

    duration_ms = int((time.monotonic() - t_node_start) * 1000)
    _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")

    return {
        "next_node": "supervisor",
        "diagnosis_summary": summary.root_cause,
        "diagnostic_summary": summary.model_dump(mode="json"),
        "recommended_mutations": summary.recommended_mutations,
        "discard": summary.discard,
        "mutation_plan": summary.root_cause,
        "errors": prior_errors,
    }


def backtest_diagnostics_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered backtest diagnostician.

    Makes a single Bedrock call to produce a structured DiagnosticSummary.
    Retries once on parse failure; falls back to hardcoded NO_EDGE default.

    Parameters
    ----------
    state:
        The current AgentState flowing through the graph.

    Returns
    -------
    dict
        Partial state update. LangGraph merges this back into the full state.
    """
    return asyncio.run(_run_diagnostics(state))
