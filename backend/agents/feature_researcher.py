"""FeatureResearcher agent node — proposes, computes, evaluates, and registers features.

Uses the Bedrock Converse API in a multi-turn tool-call loop. The node iterates
up to MAX_FEATURES_PER_SESSION times, each time proposing a FeatureSpec,
computing it against bar data, evaluating regime discrimination via ANOVA, and
registering survivors to the FeatureLibrary.

Pattern mirrors strategy_researcher.py exactly.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import botocore.exceptions
from pydantic import ValidationError

from backend.agents.providers.bedrock import BedrockAdapter, ConverseResult
from backend.agents.providers.logging import AgentLogger
from backend.agents.state import AgentState
from backend.agents.tools.client import MedallionClient, ToolCallError
from backend.agents.tools.feature import (
    _FEATURE_EVAL_CACHE,
    compute_feature,
    evaluate_feature,
    propose_feature,
    register_feature,
)
from backend.agents.tools.schemas import (
    ComputeFeatureInput,
    EvaluateFeatureInput,
    ProposeFeatureInput,
    RegisterFeatureInput,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

NODE_NAME = "feature_researcher"
MAX_FEATURES_PER_SESSION = 5
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]

SYSTEM_PROMPT = f"""You are a quantitative feature researcher for a professional Forex trading platform.
Your role is to discover novel technical features that discriminate between HMM market regimes.

ALLOWED FEATURE FAMILIES (you are constrained to these only):
  momentum        — multi-horizon log returns, RSI variants, MACD signal line, momentum z-score
  breakout        — Donchian channel position, range expansion ratio, ATR relative to N-bar average
  volatility      — realised vol ratio (short/long), vol regime z-score, vol compression/expansion
  session         — hour-of-day buckets, session overlap flags, distance from rolling extremes
  microstructure  — spread-to-ATR ratio, vol-adjusted spread estimate
  regime_persistence — N-bar streak of same direction, consecutive highs/lows count

LEAKAGE RULES — self-assess leakage_risk in the FeatureSpec:
  "none"   — uses only past data, deterministic, no forward look
  "low"    — uses close-of-bar values available at bar close time
  "medium" — relies on high/low of the current bar (may look ahead intrabar)
  "high"   — uses future prices, future returns, or the regime label itself as input
CRITICAL: Any feature with leakage_risk="high" will be blocked from registration unconditionally.
Do NOT propose features that use df['close'].shift(-1) or any negative shift.

CODE CONTRACT:
Your feature code receives a pandas DataFrame named `df` with columns:
  open, high, low, close, volume
and a DatetimeIndex (timestamp_utc, UTC). You must assign a pd.Series to `result`.
Use ONLY numpy and pandas — no other imports are allowed.

Example:
  result = df["close"].rolling(10).mean() / df["close"].rolling(50).mean() - 1

SESSION COLUMN (5 buckets):
  0 = Asia (00-07 UTC)
  1 = London (08-12 UTC)
  2 = Overlap (13-16 UTC)
  3 = NY (17-20 UTC)
  4 = Off-hours (21-23 UTC)
Note: session is not in the bar DataFrame. Build it from df.index.hour if needed.

WORKFLOW (repeat up to {MAX_FEATURES_PER_SESSION} times per session):
1. Call propose_feature with a complete FeatureSpec dict:
   {{
     "name": "unique_snake_case_name",
     "family": "<one of the allowed families>",
     "formula_description": "...",
     "lookback_bars": <int>,
     "dependency_columns": ["close", ...],
     "transformation": "...",
     "expected_intuition": "...",
     "leakage_risk": "none|low|medium|high",
     "code": "result = ..."
   }}
   If errors returned, revise and call propose_feature again (max 2 retries per feature).
2. Call compute_feature with feature_name, code, instrument, timeframe, start, end.
   If success=False, revise the code and retry once.
3. Call evaluate_feature with feature_name, instrument, timeframe, start, end, model_id.
4. Call register_feature with feature_name.
5. Move to the next feature.

After completing all features (or reaching the session limit), return a JSON summary:
{{
  "features_proposed": <int>,
  "features_registered": <int>,
  "results": [<FeatureEvalResult dict>, ...]
}}
"""

# ---------------------------------------------------------------------------
# RESEARCHER_TOOLS — built from model JSON schemas at module load time
# ---------------------------------------------------------------------------

RESEARCHER_TOOLS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "propose_feature",
            "description": (
                "Validate a FeatureSpec dict. Checks schema compliance and family allowlist. "
                "Returns valid=true and the spec dict on success, or errors on failure."
            ),
            "inputSchema": {"json": ProposeFeatureInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "compute_feature",
            "description": (
                "Execute feature code against bar data using a subprocess sandbox. "
                "Returns success=true with series_length and sample_values, or success=false with error."
            ),
            "inputSchema": {"json": ComputeFeatureInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "evaluate_feature",
            "description": (
                "Run ANOVA F-statistic on the cached feature Series grouped by HMM regime labels. "
                "Returns f_statistic, regime_breakdown, and passes_threshold."
            ),
            "inputSchema": {"json": EvaluateFeatureInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "register_feature",
            "description": (
                "Register the feature to the FeatureLibrary if it passes threshold "
                "(f_statistic > 2.0 AND leakage_risk != 'high'). "
                "Returns registered=true/false with reason."
            ),
            "inputSchema": {"json": RegisterFeatureInput.model_json_schema()},
        }
    },
]

# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, tuple[Any, Any]] = {
    "propose_feature":  (propose_feature,  ProposeFeatureInput),
    "compute_feature":  (compute_feature,  ComputeFeatureInput),
    "evaluate_feature": (evaluate_feature, EvaluateFeatureInput),
    "register_feature": (register_feature, RegisterFeatureInput),
}


# ---------------------------------------------------------------------------
# Internal helpers  (copied from strategy_researcher.py pattern)
# ---------------------------------------------------------------------------

def _build_user_message(state: AgentState) -> str:
    """Construct the initial user message from state fields."""
    instrument = state.get("instrument", "EUR_USD")
    timeframe = state.get("timeframe", "H4")
    test_start = state.get("test_start", "")
    test_end = state.get("test_end", "")
    model_id = state.get("model_id") or "none"

    regime_context = state.get("regime_context")
    ctx_json = (
        json.dumps(regime_context, indent=2)
        if regime_context and "error" not in regime_context
        else "not available"
    )

    # List existing feature names to avoid duplicates
    try:
        from backend.deps import get_feature_library as _get_lib
        existing = _get_lib().list_all()
        existing_names = ", ".join(r["feature_name"] for r in existing) or "none"
    except Exception:
        existing_names = "none"

    return "\n".join([
        f"TASK: discover_features",
        f"INSTRUMENT: {instrument}",
        f"TIMEFRAME: {timeframe}",
        f"PERIOD: {test_start} to {test_end}",
        f"HMM_MODEL: {model_id}",
        f"MAX_FEATURES: {MAX_FEATURES_PER_SESSION}",
        f"REGIME_CONTEXT: {ctx_json}",
        f"EXISTING_FEATURES: {existing_names}",
    ])


def _extract_json_from_text(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a text string (handles markdown fences)."""
    import re
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start: i + 1])
    raise ValueError("Malformed JSON in LLM response")


async def _dispatch_tool(
    tool_name: str,
    tool_input_dict: dict[str, Any],
    tool_use_id: str,
    client: MedallionClient,
    _logger: AgentLogger,
    trace_id: str,
) -> dict[str, Any]:
    """Validate, execute one tool, and return a Bedrock tool_result message dict."""
    t_start = time.monotonic()

    if tool_name not in _TOOL_DISPATCH:
        return {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": {"error": f"Unknown tool: {tool_name}"}}],
                    "status": "error",
                }
            }],
        }

    executor_fn, input_model_class = _TOOL_DISPATCH[tool_name]

    try:
        inp = input_model_class.model_validate(tool_input_dict)
    except ValidationError as exc:
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _logger.tool_call(tool_name, trace_id, NODE_NAME, tool_input_dict,
                          f"ValidationError: {exc}", latency_ms, success=False)
        return {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": {"error": f"Input validation failed: {exc}"}}],
                    "status": "error",
                }
            }],
        }

    try:
        output = await executor_fn(inp, client)
        serialized = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        latency_ms = int((time.monotonic() - t_start) * 1000)
        summary = str(serialized)[:120]
        _logger.tool_call(tool_name, trace_id, NODE_NAME, tool_input_dict,
                          summary, latency_ms, success=True)
        return {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": serialized}],
                    "status": "success",
                }
            }],
        }
    except ToolCallError as exc:
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _logger.tool_call(tool_name, trace_id, NODE_NAME, tool_input_dict,
                          f"ToolCallError: {exc}", latency_ms, success=False)
        return {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": {"error": str(exc)}}],
                    "status": "error",
                }
            }],
        }


# ---------------------------------------------------------------------------
# Main async implementation
# ---------------------------------------------------------------------------

async def _run_feature_researcher(state: AgentState) -> dict[str, Any]:
    """Async implementation of feature_researcher_node."""
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    trace_id = state.get("trace_id", "")
    prior_errors: list[str] = list(state.get("errors") or [])

    t_node_start = time.monotonic()
    _logger.node_enter(NODE_NAME, trace_id, list(state.keys()))

    adapter = BedrockAdapter()
    client = MedallionClient()

    # Track which feature names are touched this session (to collect results)
    session_feature_names: list[str] = []

    # ── Build initial messages ──────────────────────────────────────────────
    user_message_text = _build_user_message(state)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_message_text}]}
    ]

    # ── First Bedrock call with throttle retry ──────────────────────────────
    max_total_tool_calls = MAX_FEATURES_PER_SESSION * 6  # 4 tools + 2 retries per feature
    result: ConverseResult | None = None

    for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
        try:
            t_llm = time.monotonic()
            result = await adapter.converse(
                messages=messages,
                system_prompt=SYSTEM_PROMPT,
                tools=RESEARCHER_TOOLS,
                max_tokens=4096,
                temperature=0.0,
            )
            llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
            _logger.llm_call(
                adapter._model_id, trace_id, NODE_NAME,
                result.input_tokens, result.output_tokens, llm_latency_ms,
                tool_use=result.tool_use is not None,
                tool_name=result.tool_use.get("name") if result.tool_use else None,
            )
            break
        except botocore.exceptions.ClientError as exc:
            err_code = exc.response.get("Error", {}).get("Code", "")
            if err_code == "ThrottlingException" and attempt < len(BEDROCK_THROTTLE_BACKOFF_SECONDS):
                backoff = BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt]
                _logger.node_error(NODE_NAME, trace_id,
                                   f"ThrottlingException attempt {attempt}, backoff {backoff}s")
                await asyncio.sleep(backoff)
                continue
            duration_ms = int((time.monotonic() - t_node_start) * 1000)
            _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
            return {
                "next_node": "supervisor",
                "errors": prior_errors + [f"feature_researcher: Bedrock error: {exc}"],
                "task": "done",
                "research_mode": None,
                "feature_eval_results": [],
            }
    else:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "errors": prior_errors + [
                "feature_researcher: Bedrock ThrottlingException — max retries exhausted"
            ],
            "task": "done",
            "research_mode": None,
            "feature_eval_results": [],
        }

    # ── Tool-call loop ───────────────────────────────────────────────────────
    total_tool_calls = 0

    while result is not None and result.stop_reason == "tool_use":
        if total_tool_calls >= max_total_tool_calls:
            break

        # Append assistant message
        assistant_content: list[dict[str, Any]] = []
        if result.content:
            assistant_content.append({"text": result.content})
        if result.tool_use:
            assistant_content.append({"toolUse": result.tool_use})
        messages.append({"role": "assistant", "content": assistant_content})

        extracted = BedrockAdapter.extract_tool_use(result)
        if extracted is None:
            break

        tool_name, tool_input_dict = extracted
        tool_use_id = result.tool_use.get("toolUseId", str(uuid.uuid4()))  # type: ignore[union-attr]
        total_tool_calls += 1

        # Track feature names seen in this session
        if tool_name == "propose_feature":
            feature_name = tool_input_dict.get("spec", {}).get("name", "")
            if feature_name and feature_name not in session_feature_names:
                session_feature_names.append(feature_name)

        tool_result_msg = await _dispatch_tool(
            tool_name, tool_input_dict, tool_use_id, client, _logger, trace_id
        )
        messages.append(tool_result_msg)

        # Next Bedrock call with throttle retry
        for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
            try:
                t_llm = time.monotonic()
                result = await adapter.converse(
                    messages=messages,
                    system_prompt=SYSTEM_PROMPT,
                    tools=RESEARCHER_TOOLS,
                    max_tokens=4096,
                    temperature=0.0,
                )
                llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
                _logger.llm_call(
                    adapter._model_id, trace_id, NODE_NAME,
                    result.input_tokens, result.output_tokens, llm_latency_ms,
                    tool_use=result.tool_use is not None,
                    tool_name=result.tool_use.get("name") if result.tool_use else None,
                )
                break
            except botocore.exceptions.ClientError as exc:
                err_code = exc.response.get("Error", {}).get("Code", "")
                if err_code == "ThrottlingException" and attempt < len(BEDROCK_THROTTLE_BACKOFF_SECONDS):
                    backoff = BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt]
                    await asyncio.sleep(backoff)
                    continue
                duration_ms = int((time.monotonic() - t_node_start) * 1000)
                _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
                return {
                    "next_node": "supervisor",
                    "errors": prior_errors + [
                        f"feature_researcher: Bedrock error during tool loop: {exc}"
                    ],
                    "task": "done",
                    "research_mode": None,
                    "feature_eval_results": _collect_session_results(session_feature_names),
                }
        else:
            duration_ms = int((time.monotonic() - t_node_start) * 1000)
            _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
            return {
                "next_node": "supervisor",
                "errors": prior_errors + [
                    "feature_researcher: ThrottlingException in tool loop — max retries exhausted"
                ],
                "task": "done",
                "research_mode": None,
                "feature_eval_results": _collect_session_results(session_feature_names),
            }

    # ── Parse final end_turn response ────────────────────────────────────────
    session_eval_results = _collect_session_results(session_feature_names)

    if result is not None and result.content:
        try:
            _extract_json_from_text(result.content)  # validate JSON parseable
        except (ValueError, json.JSONDecodeError):
            prior_errors.append("feature_researcher: could not parse final JSON summary")

    duration_ms = int((time.monotonic() - t_node_start) * 1000)
    _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")

    return {
        "next_node": "supervisor",
        "feature_eval_results": session_eval_results,
        "research_mode": None,  # clear trigger so supervisor doesn't re-route
        "task": "done",
        "errors": prior_errors,
    }


def _collect_session_results(feature_names: list[str]) -> list[dict[str, Any]]:
    """Collect FeatureEvalResult dicts from cache for features touched this session."""
    results = []
    for name in feature_names:
        eval_result = _FEATURE_EVAL_CACHE.get(name)
        if eval_result is not None:
            results.append(eval_result.model_dump(mode="json"))
    return results


# ---------------------------------------------------------------------------
# Public LangGraph node
# ---------------------------------------------------------------------------

def feature_researcher_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered feature discovery and registration.

    Runs an async multi-turn tool-call loop synchronously (via asyncio.run)
    so it is compatible with LangGraph's synchronous node interface.

    Parameters
    ----------
    state:
        The current AgentState flowing through the graph.

    Returns
    -------
    dict
        Partial state update merged back by LangGraph.
        Always sets research_mode=None and task="done".
    """
    return asyncio.run(_run_feature_researcher(state))
