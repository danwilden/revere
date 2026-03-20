"""GenerationComparator agent node — compares strategy candidates across generations.

Makes a single Bedrock call (no tool calls) to score and select the best candidate
from state.strategy_candidates, then writes a structured ComparisonResult back to state.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import botocore.exceptions
from pydantic import ValidationError

from backend.agents.providers.bedrock import BedrockAdapter
from backend.agents.providers.logging import AgentLogger
from backend.agents.state import AgentState
from backend.agents.tools.schemas import ComparisonResult

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

NODE_NAME = "generation_comparator"
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]

# The {N} placeholder in the prompt is substituted at call time via str.replace,
# not via .format(), to avoid clashes with JSON brace syntax in the prompt body.
SYSTEM_PROMPT = """You are a quantitative strategy evaluator. Compare {N} strategy candidates
and select the best one based on their backtest performance.

Scoring guidance:
- Primary criterion: Sharpe ratio (weight 40%)
- Secondary: max_drawdown_pct — penalize drawdowns worse than -20% (weight 30%)
- Tertiary: trade_count — strategies with < 20 trades get 0.5x multiplier (weight 15%)
- Quaternary: win_rate (weight 15%)
- Compute a composite score 0.0-1.0 for each candidate.

- If no candidate has sharpe > 0.1 AND trade_count >= 10: recommendation = "discard"
- If winner has sharpe > 0.5 AND trade_count >= 30 AND max_drawdown_pct > -20%: recommendation = "continue"
- Otherwise: recommendation = "archive"

Output JSON:
{
  "winner_id": "<candidate_id or null>",
  "winner_strategy_id": "<strategy_id or null>",
  "rationale": "<2-3 sentence explanation>",
  "score_delta": <float or null>,
  "recommendation": "<continue | archive | discard>",
  "scores": {"<candidate_id>": <float>, ...}
}
"""

_SCORE_DELTA_TIE_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_message(candidates: list[dict[str, Any]]) -> str:
    """Construct the comparator user message from the candidates list."""
    n = len(candidates)
    lines = [f"CANDIDATE_COUNT: {n}", ""]
    for i, c in enumerate(candidates):
        lines.append(f"CANDIDATE {i + 1}:")
        lines.append(f"  candidate_id: {c.get('candidate_id', 'unknown')}")
        lines.append(f"  strategy_id: {c.get('strategy_id')}")
        lines.append(f"  generation: {c.get('generation', 0)}")
        lines.append(f"  hypothesis: {c.get('hypothesis', '')}")
        lines.append(f"  sharpe: {c.get('sharpe')}")
        lines.append(f"  max_drawdown_pct: {c.get('max_drawdown_pct')}")
        lines.append(f"  win_rate: {c.get('win_rate')}")
        lines.append(f"  trade_count: {c.get('trade_count', 0)}")
        metrics = c.get("metrics") or {}
        if metrics:
            lines.append("  metrics:")
            for k, v in sorted(metrics.items()):
                lines.append(f"    {k}: {v}")
        lines.append("")
    return "\n".join(lines)


def _parse_comparison_result(text: str) -> ComparisonResult:
    """Extract and validate ComparisonResult from LLM response text."""
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        raw = json.loads(fence_match.group(1))
    else:
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in response")
        depth = 0
        end_idx = -1
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        if end_idx == -1:
            raise ValueError("Malformed JSON — unmatched braces")
        raw = json.loads(text[start : end_idx + 1])
    return ComparisonResult.model_validate(raw)


async def _run_comparator(state: AgentState) -> dict[str, Any]:
    """Async implementation of generation_comparator_node."""
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    trace_id = state.get("trace_id", "")
    prior_errors: list[str] = list(state.get("errors") or [])

    t_node_start = time.monotonic()
    _logger.node_enter(NODE_NAME, trace_id, list(state.keys()))

    candidates: list[dict[str, Any]] = list(state.get("strategy_candidates") or [])

    # ── Pre-LLM guard: nothing to compare ──────────────────────────────────
    if len(candidates) < 1:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "comparison_recommendation": "discard",
            "comparison_result": None,
            "errors": prior_errors + ["generation_comparator: no candidates to compare"],
            "task": "done",
        }

    # ── Build prompt — replace {N} with actual candidate count ─────────────
    n = len(candidates)
    system_prompt = SYSTEM_PROMPT.replace("{N}", str(n))
    user_message_text = _build_user_message(candidates)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_message_text}]}
    ]

    adapter = BedrockAdapter()

    # ── Single Bedrock call with throttle retry ─────────────────────────────
    result = None
    for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
        try:
            t_llm = time.monotonic()
            result = await adapter.converse(
                messages=messages,
                system_prompt=system_prompt,
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
                "comparison_recommendation": "discard",
                "comparison_result": None,
                "errors": prior_errors + [f"generation_comparator: Bedrock error: {exc}"],
                "task": "done",
            }
    else:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "comparison_recommendation": "discard",
            "comparison_result": None,
            "errors": prior_errors + ["generation_comparator: ThrottlingException — max retries exhausted"],
            "task": "done",
        }

    if result is None:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "comparison_recommendation": "discard",
            "comparison_result": None,
            "errors": prior_errors + ["generation_comparator: no result from LLM"],
            "task": "done",
        }

    # ── Parse ComparisonResult — retry once on failure ──────────────────────
    comparison: ComparisonResult | None = None
    parse_error_msg: str | None = None

    try:
        comparison = _parse_comparison_result(result.content)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        parse_error_msg = str(exc)

    if comparison is None:
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
                system_prompt=system_prompt,
                tools=None,
                max_tokens=2048,
                temperature=0.0,
            )
            llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
            _logger.llm_call(
                adapter._model_id, trace_id, NODE_NAME,
                retry_result.input_tokens, retry_result.output_tokens, llm_latency_ms,
            )
            comparison = _parse_comparison_result(retry_result.content)
        except Exception as exc:
            duration_ms = int((time.monotonic() - t_node_start) * 1000)
            _logger.node_error(NODE_NAME, trace_id, f"ComparisonResult parse failed after retry: {exc}")
            _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
            return {
                "next_node": "supervisor",
                "comparison_recommendation": "discard",
                "comparison_result": None,
                "errors": prior_errors + [f"generation_comparator: failed to parse ComparisonResult: {exc}"],
                "task": "done",
            }

    # ── Tie detection ───────────────────────────────────────────────────────
    score_delta = comparison.score_delta
    if score_delta is not None and abs(score_delta) < _SCORE_DELTA_TIE_THRESHOLD:
        _logger.state_update(
            trace_id,
            "comparison_tie",
            f"score_delta={score_delta:.4f} < {_SCORE_DELTA_TIE_THRESHOLD} threshold",
        )

    duration_ms = int((time.monotonic() - t_node_start) * 1000)
    _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")

    return {
        "next_node": "supervisor",
        "comparison_recommendation": comparison.recommendation,
        "comparison_result": comparison.model_dump(mode="json"),
        "comparison_summary": comparison.rationale,
        "selected_candidate_id": comparison.winner_id,
        "strategy_id": comparison.winner_strategy_id,
        "errors": prior_errors,
    }


def generation_comparator_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered generation comparator.

    Makes a single Bedrock call to score all accumulated strategy candidates
    and select the winner. Retries once on parse failure.

    Parameters
    ----------
    state:
        The current AgentState flowing through the graph.

    Returns
    -------
    dict
        Partial state update. LangGraph merges this back into the full state.
    """
    return asyncio.run(_run_comparator(state))
