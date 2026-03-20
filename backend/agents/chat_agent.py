"""LangGraph chat agent for the Medallion Forex platform.

Implements a conversational agent that supports three modes:
- ideation: help users develop rules-based trading strategies
- failure_analysis: diagnose poor backtest results and propose mutations
- research: open-ended Forex discussion

Graph topology
--------------
Entry → intake_node (deterministic)
  → ideation_node  (Bedrock, multi-turn tool loop)
  → analysis_node  (Bedrock, single call with optional tools)
  → research_node  (Bedrock, single call)
  → confirm_node   (deterministic + execute_proposed_action)
  → decline_node   (deterministic)
  → END
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, TypedDict

import botocore.exceptions
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from backend.agents.providers.bedrock import BedrockAdapter, ConverseResult
from backend.agents.providers.logging import AgentLogger
from backend.agents.tools.chat_execute import execute_proposed_action
from backend.agents.tools.chat_read_tools import CHAT_READ_TOOLS, dispatch_chat_read_tool
from backend.agents.tools.chat_schemas import ExecuteProposedActionInput
from backend.agents.tools.client import MedallionClient, ToolCallError

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

NODE_NAME_INTAKE = "intake"
NODE_NAME_IDEATION = "ideation_node"
NODE_NAME_ANALYSIS = "analysis_node"
NODE_NAME_RESEARCH = "research_node"
NODE_NAME_CONFIRM = "confirm_node"
NODE_NAME_DECLINE = "decline_node"

BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]
MAX_TOOL_CALLS_PER_TURN = 30

STRATEGY_JSON_START = "===STRATEGY_JSON_START==="
STRATEGY_JSON_END = "===STRATEGY_JSON_END==="

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

IDEATION_SYSTEM_PROMPT = """You are a quantitative Forex strategy design assistant for the Medallion trading platform.

Your role is to help the user develop a complete, valid rules-based trading strategy through conversation.
You ask clarifying questions and guide the user toward a fully-specified strategy definition.

Available feature fields for rule conditions:
{{AVAILABLE_FEATURES}}

Rules engine JSON format:
- Composite: {"all": [...]}, {"any": [...]}, {"not": <node>}
- Leaf: {"field": "<name>", "op": "<gt|gte|lt|lte|eq|neq|in>", "value": <scalar or list>}
- Field-to-field: {"field": "<name>", "op": "<op>", "field2": "<name>"}
- Named ref: {"ref": "<name>"}

Strategy definition structure:
{
  "entry_long": <rule node tree>,
  "entry_short": <rule node tree or null>,
  "exit": <rule node tree or optional>,
  "stop_atr_multiplier": <float, 1.5-3.0>,
  "take_profit_atr_multiplier": <float, 2.0-5.0>,
  "position_size_units": 1000,
  "named_conditions": {}
}

When the strategy is fully specified (entry conditions + stop/target defined), output it using this exact marker format:

===STRATEGY_JSON_START===
{<valid strategy definition JSON>}
===STRATEGY_JSON_END===

Only output the strategy JSON when the rules are complete and the user has provided enough context
to construct a valid strategy. Before that, ask questions to fill in gaps.

You have access to read tools: get_experiment, get_backtest_result, get_strategy_definition,
list_recent_experiments, search_experiments, check_data_availability. Use them to load context
when the user refers to an existing experiment or strategy.

Before proposing a backtest — that is, before emitting the ===STRATEGY_JSON_START=== block —
always call check_data_availability with the instrument, timeframe, and date range you intend
to use. If the result shows needs_ingestion=true, inform the user that data is not yet available
and that the system will automatically fetch it via Dukascopy when they confirm. Only emit the
strategy JSON once you have confirmed that coverage is sufficient, or the user has explicitly
acknowledged they want to proceed despite the missing data.
"""

ANALYSIS_SYSTEM_PROMPT = """You are a Forex backtest failure analyst for the Medallion trading platform.

Your role is to diagnose why a strategy produced poor results and propose specific, actionable improvements.

Apply this failure taxonomy:
- zero_trades: entry conditions never triggered
- low_trade_count: fewer than 20 trades (statistically insignificant)
- poor_signal_quality: high win-rate but low net PnL (spread erosion)
- adverse_cost_impact: profitable gross PnL but costs destroy net PnL
- regime_mismatch: regime filter excludes most of the testing period
- overfit: entry conditions are overly specific — rarely trigger in new data

For each diagnosis:
1. State the primary failure taxonomy
2. Explain the root cause in 2-3 sentences
3. Propose 2-4 specific mutations with exact field names and values

When proposing field names, use only these registered features:
{{AVAILABLE_FEATURES}}

When the user asks you to build a revised strategy, emit it using this marker format:

===STRATEGY_JSON_START===
{<valid revised strategy definition JSON>}
===STRATEGY_JSON_END===

You have access to read tools: get_experiment, get_backtest_result, get_strategy_definition,
list_recent_experiments, search_experiments. Use them to load the relevant backtest context.
"""

RESEARCH_SYSTEM_PROMPT = """You are a quantitative Forex trading research assistant for the Medallion platform.

You help users with:
- Understanding technical indicators and their suitability for different market regimes
- Explaining strategy design principles for currency pairs
- Discussing HMM regime detection and how regime labels affect strategy entry conditions
- General Forex market microstructure: spread costs, pip sizing, volatility regimes
- Interpreting backtest metrics: Sharpe ratio, max drawdown, win rate, trade count

Be precise, cite specific indicator values and thresholds where relevant.
When discussing strategy ideas, always ground them in the available feature fields.

Available feature fields:
{{AVAILABLE_FEATURES}}

You have access to read tools to look up specific experiments, strategies, or backtest results
if the user references them.
"""

# Default feature list when the feature library is empty or unavailable (fallback).
DEFAULT_AVAILABLE_FEATURES = """- log_ret_1, log_ret_5, log_ret_20  (log returns over 1/5/20 bars)
- rvol_20  (realized volatility over 20 bars)
- atr_14   (ATR over 14 bars, in price units)
- atr_pct_14 (ATR as % of price)
- rsi_14   (RSI over 14 bars, 0-100)
- ema_slope_20, ema_slope_50  (EMA slope: positive = uptrend)
- adx_14   (ADX over 14 bars, 0-100; above 25 = trending)
- breakout_20  (1 if price breaks 20-bar high/low, else 0)
- session   (market session: "london", "new_york", "asian", "overlap")
- hmm_regime  (HMM regime label, e.g. "TREND_BULL_LOW_VOL")"""


def _get_available_features_section() -> str:
    """Build the 'available feature fields' section from the feature library.

    Returns the default hardcoded list when the library is empty or unavailable.
    """
    try:
        from backend.deps import get_feature_library

        records = get_feature_library().list_all()
        if not records:
            return DEFAULT_AVAILABLE_FEATURES
        lines = []
        for r in records:
            name = r.get("feature_name") or r.get("name") or ""
            if not name:
                continue
            desc = (
                r.get("formula_description")
                or r.get("description")
                or r.get("family")
                or "registered"
            )
            lines.append(f"- {name}  ({desc})")
        return "\n".join(lines) if lines else DEFAULT_AVAILABLE_FEATURES
    except Exception:
        return DEFAULT_AVAILABLE_FEATURES


# ---------------------------------------------------------------------------
# ChatAgentState TypedDict
# ---------------------------------------------------------------------------

class ChatAgentState(TypedDict, total=False):
    # Session
    session_id: str
    trace_id: str

    # Conversation history (Bedrock messages format)
    # [{"role": "user"|"assistant", "content": [{"type":"text","text":"..."}]}]
    bedrock_messages: list[dict]

    # Flow
    last_detected_mode: str        # "ideation" | "failure_analysis" | "research"
    conversation_stage: str        # "active" | "awaiting_confirmation"
    next_node: str

    # Context refs from user
    message_context: dict          # {experiment_id, strategy_id, backtest_id, conversation_mode}
    context_cache: dict            # resolved records keyed by id

    # Strategy under development
    draft_strategy_definition: dict | None
    scope: dict                    # {instrument, timeframe, test_start, test_end, model_id, feature_run_id}

    # Confirmation flow
    pending_action: dict | None    # ProposedAction dict (ExecuteProposedActionInput fields)
    last_executed_action_result: dict | None

    # Output
    pending_reply_text: str | None
    pending_actions: list[dict]    # action events emitted this turn
    total_tokens: int

    # Errors
    errors: list[str]


# ---------------------------------------------------------------------------
# Mode detection helpers
# ---------------------------------------------------------------------------

_FAILURE_KEYWORDS = [
    "why", "failed", "drawdown", "loss", "fix", "diagnose",
    "what went wrong", "broke", "underperform", "analyze", "poor",
    "zero trades", "no trades", "sharpe",
]
_IDEATION_KEYWORDS = [
    "build", "create", "strategy", "entry", "signal", "regime",
    "rsi", "ema", "adx", "macd", "breakout", "momentum", "trend",
    "mean revert", "scalp", "swing", "long", "short", "indicator",
    "filter", "condition", "rule",
]


def detect_mode(message_text: str, context: dict, current_mode: str | None) -> str:
    """Keyword-based mode detection. Once set, mode persists unless explicitly changed.

    Parameters
    ----------
    message_text:
        The user's message text.
    context:
        ChatMessageContext dict — may contain experiment_id, backtest_id, etc.
    current_mode:
        The previously detected mode, preserved unless overridden.

    Returns
    -------
    str
        One of "ideation", "failure_analysis", or "research".
    """
    text = message_text.lower()

    has_failure_context = bool(
        context.get("experiment_id") or context.get("backtest_id")
    )
    has_failure_keywords = any(k in text for k in _FAILURE_KEYWORDS)
    has_ideation_keywords = any(k in text for k in _IDEATION_KEYWORDS)

    # Failure analysis: explicit failure context + failure language
    if has_failure_context and has_failure_keywords:
        return "failure_analysis"

    # Ideation: existing strategy reference without failure language
    if context.get("strategy_id") and not has_failure_keywords:
        return "ideation"

    # Ideation: build/create keywords
    if has_ideation_keywords:
        return "ideation"

    # Preserve mode once set — mode is sticky
    if current_mode:
        return current_mode

    # Default
    return "research"


def _is_confirmation(text: str) -> bool:
    """Return True if the text represents a user confirmation."""
    t = text.strip().lower()
    confirmations = {
        "yes", "y", "run it", "go", "confirm", "do it", "run",
        "ok", "okay", "sure", "yep", "yeah", "let's do it", "do it",
    }
    return t in confirmations


# ---------------------------------------------------------------------------
# Strategy JSON extraction
# ---------------------------------------------------------------------------

def _extract_strategy_json(text: str) -> dict | None:
    """Extract the strategy definition JSON from between the marker delimiters.

    Returns the parsed dict or None if the markers are absent or the content
    is not valid JSON.
    """
    start_idx = text.find(STRATEGY_JSON_START)
    end_idx = text.find(STRATEGY_JSON_END)

    if start_idx == -1 or end_idx == -1:
        return None

    raw_block = text[start_idx + len(STRATEGY_JSON_START):end_idx].strip()
    try:
        return json.loads(raw_block)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Bedrock tool dispatch helper (shared across specialist nodes)
# ---------------------------------------------------------------------------

async def _dispatch_read_tool(
    tool_name: str,
    tool_input_dict: dict[str, Any],
    tool_use_id: str,
    client: MedallionClient,
    node_name: str,
    _logger: AgentLogger,
    trace_id: str,
) -> dict[str, Any]:
    """Execute a chat read tool and return a Bedrock tool_result message dict."""
    t_start = time.monotonic()
    try:
        result_dict = await dispatch_chat_read_tool(tool_name, tool_input_dict, client)
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _logger.tool_call(
            tool_name, trace_id, node_name, tool_input_dict,
            str(result_dict)[:120], latency_ms, success=True,
        )
        return {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": result_dict}],
                    "status": "success",
                }
            }],
        }
    except ToolCallError as exc:
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _logger.tool_call(
            tool_name, trace_id, node_name, tool_input_dict,
            f"ToolCallError: {exc}", latency_ms, success=False,
        )
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
    except Exception as exc:
        latency_ms = int((time.monotonic() - t_start) * 1000)
        _logger.tool_call(
            tool_name, trace_id, node_name, tool_input_dict,
            f"Unexpected error: {exc}", latency_ms, success=False,
        )
        return {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"json": {"error": f"Unexpected error: {exc}"}}],
                    "status": "error",
                }
            }],
        }


# ---------------------------------------------------------------------------
# Tool-call loop (shared by ideation and analysis nodes)
# ---------------------------------------------------------------------------

async def _run_tool_call_loop(
    messages: list[dict],
    system_prompt: str,
    adapter: BedrockAdapter,
    client: MedallionClient,
    node_name: str,
    _logger: AgentLogger,
    trace_id: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> tuple[ConverseResult | None, int]:
    """Run the Bedrock tool-call loop.

    Returns
    -------
    tuple[ConverseResult | None, int]
        The final end_turn ConverseResult and total token count for this turn.
        Returns (None, tokens) if an unrecoverable error occurs.
    """
    total_tokens = 0
    total_tool_calls = 0

    # Initial call with throttle retry
    result: ConverseResult | None = None
    for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
        try:
            t_llm = time.monotonic()
            result = await adapter.converse(
                messages=messages,
                system_prompt=system_prompt,
                tools=CHAT_READ_TOOLS,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
            _logger.llm_call(
                adapter._model_id, trace_id, node_name,
                result.input_tokens, result.output_tokens, llm_latency_ms,
                tool_use=result.tool_use is not None,
                tool_name=result.tool_use.get("name") if result.tool_use else None,
            )
            total_tokens += result.input_tokens + result.output_tokens
            break
        except botocore.exceptions.ClientError as exc:
            err_code = exc.response.get("Error", {}).get("Code", "")
            if err_code == "ThrottlingException" and attempt < len(BEDROCK_THROTTLE_BACKOFF_SECONDS):
                backoff = BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt]
                _logger.node_error(node_name, trace_id,
                                   f"ThrottlingException attempt {attempt}, backoff {backoff}s")
                await asyncio.sleep(backoff)
                continue
            _logger.node_error(node_name, trace_id, f"Bedrock ClientError: {exc}")
            return None, total_tokens
    else:
        _logger.node_error(node_name, trace_id,
                           "ThrottlingException — max retries exhausted on initial call")
        return None, total_tokens

    # Tool-call loop
    while result is not None and result.stop_reason == "tool_use":
        if total_tool_calls >= MAX_TOOL_CALLS_PER_TURN:
            _logger.node_error(node_name, trace_id,
                               f"Max tool calls ({MAX_TOOL_CALLS_PER_TURN}) reached, breaking loop")
            break

        # Append assistant message with tool_use block
        assistant_content: list[dict] = []
        if result.content:
            assistant_content.append({"text": result.content})
        if result.tool_use:
            assistant_content.append({"toolUse": result.tool_use})
        messages.append({"role": "assistant", "content": assistant_content})

        # Extract and dispatch the tool
        extracted = BedrockAdapter.extract_tool_use(result)
        if extracted is None:
            break

        tool_name, tool_input_dict = extracted
        tool_use_id = result.tool_use.get("toolUseId", str(uuid.uuid4()))
        total_tool_calls += 1

        tool_result_msg = await _dispatch_read_tool(
            tool_name, tool_input_dict, tool_use_id,
            client, node_name, _logger, trace_id,
        )
        messages.append(tool_result_msg)

        # Next Bedrock call with throttle retry
        next_result: ConverseResult | None = None
        for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
            try:
                t_llm = time.monotonic()
                next_result = await adapter.converse(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=CHAT_READ_TOOLS,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                llm_latency_ms = int((time.monotonic() - t_llm) * 1000)
                _logger.llm_call(
                    adapter._model_id, trace_id, node_name,
                    next_result.input_tokens, next_result.output_tokens, llm_latency_ms,
                    tool_use=next_result.tool_use is not None,
                    tool_name=next_result.tool_use.get("name") if next_result.tool_use else None,
                )
                total_tokens += next_result.input_tokens + next_result.output_tokens
                break
            except botocore.exceptions.ClientError as exc:
                err_code = exc.response.get("Error", {}).get("Code", "")
                if err_code == "ThrottlingException" and attempt < len(BEDROCK_THROTTLE_BACKOFF_SECONDS):
                    backoff = BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt]
                    _logger.node_error(node_name, trace_id,
                                       f"ThrottlingException in tool loop attempt {attempt}, backoff {backoff}s")
                    await asyncio.sleep(backoff)
                    continue
                _logger.node_error(node_name, trace_id,
                                   f"Bedrock ClientError in tool loop: {exc}")
                return None, total_tokens
        else:
            _logger.node_error(node_name, trace_id,
                               "ThrottlingException in tool loop — max retries exhausted")
            return None, total_tokens

        result = next_result

    return result, total_tokens


# ---------------------------------------------------------------------------
# Node: intake (deterministic — no LLM)
# ---------------------------------------------------------------------------

def intake_node(state: ChatAgentState) -> dict[str, Any]:
    """Deterministic routing node.

    Reads the last user message, detects conversation mode, and sets next_node
    for the conditional edge.
    """
    t0 = time.monotonic()
    trace_id = state.get("trace_id", "")
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    _logger.node_enter(NODE_NAME_INTAKE, trace_id, list(state.keys()))

    messages: list[dict] = state.get("bedrock_messages", [])
    context: dict = state.get("message_context", {})
    current_mode: str | None = state.get("last_detected_mode")
    conversation_stage: str = state.get("conversation_stage", "active")

    # Extract the last user message text
    last_user_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    last_user_text = block["text"]
                    break
            break

    # Detect mode
    detected_mode = detect_mode(last_user_text, context, current_mode)

    # Route logic
    if conversation_stage == "awaiting_confirmation":
        if _is_confirmation(last_user_text):
            next_node = NODE_NAME_CONFIRM
        else:
            next_node = NODE_NAME_DECLINE
    elif detected_mode == "ideation":
        next_node = NODE_NAME_IDEATION
    elif detected_mode == "failure_analysis":
        next_node = NODE_NAME_ANALYSIS
    else:
        next_node = NODE_NAME_RESEARCH

    duration_ms = int((time.monotonic() - t0) * 1000)
    _logger.node_exit(NODE_NAME_INTAKE, trace_id, duration_ms, next_node)

    return {
        "last_detected_mode": detected_mode,
        "next_node": next_node,
    }


# ---------------------------------------------------------------------------
# Node: ideation (async Bedrock multi-turn tool loop)
# ---------------------------------------------------------------------------

async def _run_ideation(state: ChatAgentState) -> dict[str, Any]:
    """Async implementation of ideation_node."""
    trace_id = state.get("trace_id", "")
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    prior_errors: list[str] = list(state.get("errors") or [])

    t0 = time.monotonic()
    _logger.node_enter(NODE_NAME_IDEATION, trace_id, list(state.keys()))

    adapter = BedrockAdapter()
    client = MedallionClient()

    # Build the messages list (copy so we don't mutate state)
    messages: list[dict] = list(state.get("bedrock_messages") or [])

    if not messages:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_error(NODE_NAME_IDEATION, trace_id, "No messages in state")
        _logger.node_exit(NODE_NAME_IDEATION, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "I didn't receive a message. Could you please tell me what strategy you'd like to build?",
            "pending_actions": [],
            "total_tokens": 0,
            "errors": prior_errors,
        }

    system_prompt = IDEATION_SYSTEM_PROMPT.replace(
        "{{AVAILABLE_FEATURES}}", _get_available_features_section()
    )
    result, total_tokens = await _run_tool_call_loop(
        messages=messages,
        system_prompt=system_prompt,
        adapter=adapter,
        client=client,
        node_name=NODE_NAME_IDEATION,
        _logger=_logger,
        trace_id=trace_id,
        max_tokens=4096,
        temperature=0.7,
    )

    if result is None:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_exit(NODE_NAME_IDEATION, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "I encountered an error communicating with the AI model. Please try again.",
            "pending_actions": [],
            "total_tokens": total_tokens,
            "errors": prior_errors + ["ideation_node: Bedrock error — null result"],
        }

    reply_text = result.content or ""
    pending_actions: list[dict] = []
    new_stage = state.get("conversation_stage", "active")
    pending_action: dict | None = state.get("pending_action")

    # Check if the LLM emitted a strategy JSON block
    strategy_def = _extract_strategy_json(reply_text)
    if strategy_def is not None:
        # Build a pending_action from the strategy + scope
        scope: dict = state.get("scope") or {}
        pending_action = {
            "action_type": "submit_backtest",
            "strategy_definition": strategy_def,
            "instrument": scope.get("instrument", "EUR_USD"),
            "timeframe": scope.get("timeframe", "H4"),
            "test_start": scope.get("test_start", "2024-01-01"),
            "test_end": scope.get("test_end", "2024-06-01"),
            "feature_run_id": scope.get("feature_run_id"),
            "model_id": scope.get("model_id"),
        }
        new_stage = "awaiting_confirmation"
        pending_actions = [{"action_type": "run_strategy", "payload": strategy_def}]
        _logger.state_update(trace_id, "pending_action", "strategy ready for confirmation")

    duration_ms = int((time.monotonic() - t0) * 1000)
    _logger.node_exit(NODE_NAME_IDEATION, trace_id, duration_ms, END)

    return {
        "pending_reply_text": reply_text,
        "pending_actions": pending_actions,
        "total_tokens": total_tokens,
        "conversation_stage": new_stage,
        "pending_action": pending_action,
        "draft_strategy_definition": strategy_def,
        "errors": prior_errors,
    }


async def ideation_node(state: ChatAgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered strategy ideation.

    Runs the async multi-turn tool-call loop; use graph.ainvoke() so this
    runs in the same event loop as the caller.
    """
    return await _run_ideation(state)


# ---------------------------------------------------------------------------
# Node: analysis (single Bedrock call with optional tool loop)
# ---------------------------------------------------------------------------

async def _run_analysis(state: ChatAgentState) -> dict[str, Any]:
    """Async implementation of analysis_node."""
    trace_id = state.get("trace_id", "")
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    prior_errors: list[str] = list(state.get("errors") or [])

    t0 = time.monotonic()
    _logger.node_enter(NODE_NAME_ANALYSIS, trace_id, list(state.keys()))

    adapter = BedrockAdapter()
    client = MedallionClient()

    messages: list[dict] = list(state.get("bedrock_messages") or [])

    if not messages:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_exit(NODE_NAME_ANALYSIS, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "Please describe the backtest results you'd like me to analyze.",
            "pending_actions": [],
            "total_tokens": 0,
            "errors": prior_errors,
        }

    system_prompt = ANALYSIS_SYSTEM_PROMPT.replace(
        "{{AVAILABLE_FEATURES}}", _get_available_features_section()
    )
    result, total_tokens = await _run_tool_call_loop(
        messages=messages,
        system_prompt=system_prompt,
        adapter=adapter,
        client=client,
        node_name=NODE_NAME_ANALYSIS,
        _logger=_logger,
        trace_id=trace_id,
        max_tokens=4096,
        temperature=0.3,
    )

    if result is None:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_exit(NODE_NAME_ANALYSIS, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "I encountered an error analyzing the backtest. Please try again.",
            "pending_actions": [],
            "total_tokens": total_tokens,
            "errors": prior_errors + ["analysis_node: Bedrock error — null result"],
        }

    reply_text = result.content or ""
    pending_actions: list[dict] = []
    new_stage = state.get("conversation_stage", "active")
    pending_action: dict | None = state.get("pending_action")

    # Check if analysis proposes a revised strategy
    strategy_def = _extract_strategy_json(reply_text)
    if strategy_def is not None:
        scope: dict = state.get("scope") or {}
        context: dict = state.get("message_context") or {}
        pending_action = {
            "action_type": "submit_backtest",
            "strategy_definition": strategy_def,
            "instrument": scope.get("instrument", "EUR_USD"),
            "timeframe": scope.get("timeframe", "H4"),
            "test_start": scope.get("test_start", "2024-01-01"),
            "test_end": scope.get("test_end", "2024-06-01"),
            "feature_run_id": scope.get("feature_run_id"),
            "model_id": scope.get("model_id"),
        }
        new_stage = "awaiting_confirmation"
        pending_actions = [{"action_type": "run_strategy", "payload": strategy_def}]
        _logger.state_update(trace_id, "pending_action", "revised strategy ready for confirmation")

    duration_ms = int((time.monotonic() - t0) * 1000)
    _logger.node_exit(NODE_NAME_ANALYSIS, trace_id, duration_ms, END)

    return {
        "pending_reply_text": reply_text,
        "pending_actions": pending_actions,
        "total_tokens": total_tokens,
        "conversation_stage": new_stage,
        "pending_action": pending_action,
        "errors": prior_errors,
    }


async def analysis_node(state: ChatAgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered backtest failure analysis."""
    return await _run_analysis(state)


# ---------------------------------------------------------------------------
# Node: research (simple single Bedrock call)
# ---------------------------------------------------------------------------

async def _run_research(state: ChatAgentState) -> dict[str, Any]:
    """Async implementation of research_node."""
    trace_id = state.get("trace_id", "")
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    prior_errors: list[str] = list(state.get("errors") or [])

    t0 = time.monotonic()
    _logger.node_enter(NODE_NAME_RESEARCH, trace_id, list(state.keys()))

    adapter = BedrockAdapter()
    client = MedallionClient()

    messages: list[dict] = list(state.get("bedrock_messages") or [])

    if not messages:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_exit(NODE_NAME_RESEARCH, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "How can I help you with Forex strategy research?",
            "pending_actions": [],
            "total_tokens": 0,
            "errors": prior_errors,
        }

    # Research node uses tool-call loop too so it can answer questions about
    # experiments/backtests, but never proposes actions.
    system_prompt = RESEARCH_SYSTEM_PROMPT.replace(
        "{{AVAILABLE_FEATURES}}", _get_available_features_section()
    )
    result, total_tokens = await _run_tool_call_loop(
        messages=messages,
        system_prompt=system_prompt,
        adapter=adapter,
        client=client,
        node_name=NODE_NAME_RESEARCH,
        _logger=_logger,
        trace_id=trace_id,
        max_tokens=2048,
        temperature=0.5,
    )

    if result is None:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_exit(NODE_NAME_RESEARCH, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "I encountered an error generating a response. Please try again.",
            "pending_actions": [],
            "total_tokens": total_tokens,
            "errors": prior_errors + ["research_node: Bedrock error — null result"],
        }

    reply_text = result.content or ""

    duration_ms = int((time.monotonic() - t0) * 1000)
    _logger.node_exit(NODE_NAME_RESEARCH, trace_id, duration_ms, END)

    return {
        "pending_reply_text": reply_text,
        "pending_actions": [],
        "total_tokens": total_tokens,
        "conversation_stage": "active",   # research never enters awaiting_confirmation
        "errors": prior_errors,
    }


async def research_node(state: ChatAgentState) -> dict[str, Any]:
    """LangGraph node: open-ended Forex research assistant."""
    return await _run_research(state)


# ---------------------------------------------------------------------------
# Node: confirm (deterministic + execute_proposed_action)
# ---------------------------------------------------------------------------

async def _run_confirm(state: ChatAgentState) -> dict[str, Any]:
    """Async implementation of confirm_node."""
    trace_id = state.get("trace_id", "")
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    prior_errors: list[str] = list(state.get("errors") or [])

    t0 = time.monotonic()
    _logger.node_enter(NODE_NAME_CONFIRM, trace_id, list(state.keys()))

    action = state.get("pending_action")
    if not action:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_error(NODE_NAME_CONFIRM, trace_id, "No pending action in state")
        _logger.node_exit(NODE_NAME_CONFIRM, trace_id, duration_ms, END)
        return {
            "pending_reply_text": "No pending action found. Let's continue working on the strategy.",
            "conversation_stage": "active",
            "pending_actions": [],
            "total_tokens": 0,
            "errors": prior_errors,
        }

    client = MedallionClient()
    try:
        inp = ExecuteProposedActionInput(**{**action, "session_id": state.get("session_id")})
        result = await execute_proposed_action(inp, client)

        _logger.state_update(trace_id, "executed_action", f"job_id={result.job_id}")
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_exit(NODE_NAME_CONFIRM, trace_id, duration_ms, END)

        reply_text = (
            result.message.strip()
            if (result.message and result.message.strip())
            else (
                f"Backtest job queued successfully. "
                f"Job ID: `{result.job_id}`. "
                "Check the Backtests view to monitor progress."
            )
        )
        return {
            "pending_reply_text": reply_text,
            "last_executed_action_result": result.model_dump(mode="json"),
            "pending_action": None,
            "conversation_stage": "active",
            "pending_actions": [{"action_type": "job_queued", "payload": result.model_dump(mode="json")}],
            "total_tokens": 0,
            "errors": prior_errors,
        }

    except (ToolCallError, ValidationError) as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _logger.node_error(NODE_NAME_CONFIRM, trace_id, f"execute_proposed_action failed: {exc}")
        _logger.node_exit(NODE_NAME_CONFIRM, trace_id, duration_ms, END)
        return {
            "pending_reply_text": (
                f"I was unable to queue the backtest: {exc}. "
                "You can try adjusting the strategy parameters and confirming again."
            ),
            "pending_action": None,
            "conversation_stage": "active",
            "pending_actions": [],
            "total_tokens": 0,
            "errors": prior_errors + [f"confirm_node: execute_proposed_action failed: {exc}"],
        }


async def confirm_node(state: ChatAgentState) -> dict[str, Any]:
    """LangGraph node: execute the confirmed pending action."""
    return await _run_confirm(state)


# ---------------------------------------------------------------------------
# Node: decline (deterministic)
# ---------------------------------------------------------------------------

def decline_node(state: ChatAgentState) -> dict[str, Any]:
    """LangGraph node: discard the pending action."""
    trace_id = state.get("trace_id", "")
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    _logger.node_enter(NODE_NAME_DECLINE, trace_id, list(state.keys()))

    result = {
        "pending_reply_text": (
            "Understood, I've discarded that proposal. "
            "We can continue refining the strategy or explore a different approach."
        ),
        "pending_action": None,
        "conversation_stage": "active",
        "pending_actions": [],
        "total_tokens": 0,
    }

    _logger.node_exit(NODE_NAME_DECLINE, trace_id, 0, END)
    return result


# ---------------------------------------------------------------------------
# Conditional edge routing
# ---------------------------------------------------------------------------

def route_from_intake(state: ChatAgentState) -> str:
    """Read state["next_node"] set by intake_node and return the target node name."""
    return state.get("next_node", NODE_NAME_RESEARCH)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_chat_graph():
    """Build and compile the chat agent StateGraph.

    Returns
    -------
    CompiledGraph
        Ready for ``.invoke()`` calls.
    """
    workflow: StateGraph = StateGraph(ChatAgentState)

    workflow.add_node(NODE_NAME_INTAKE, intake_node)
    workflow.add_node(NODE_NAME_IDEATION, ideation_node)
    workflow.add_node(NODE_NAME_ANALYSIS, analysis_node)
    workflow.add_node(NODE_NAME_RESEARCH, research_node)
    workflow.add_node(NODE_NAME_CONFIRM, confirm_node)
    workflow.add_node(NODE_NAME_DECLINE, decline_node)

    workflow.set_entry_point(NODE_NAME_INTAKE)

    workflow.add_conditional_edges(
        NODE_NAME_INTAKE,
        route_from_intake,
        {
            NODE_NAME_IDEATION: NODE_NAME_IDEATION,
            NODE_NAME_ANALYSIS: NODE_NAME_ANALYSIS,
            NODE_NAME_RESEARCH: NODE_NAME_RESEARCH,
            NODE_NAME_CONFIRM: NODE_NAME_CONFIRM,
            NODE_NAME_DECLINE: NODE_NAME_DECLINE,
        },
    )

    workflow.add_edge(NODE_NAME_IDEATION, END)
    workflow.add_edge(NODE_NAME_ANALYSIS, END)
    workflow.add_edge(NODE_NAME_RESEARCH, END)
    workflow.add_edge(NODE_NAME_CONFIRM, END)
    workflow.add_edge(NODE_NAME_DECLINE, END)

    return workflow.compile()


# Compile once at module load — avoids rebuild overhead on every request.
_CHAT_GRAPH = build_chat_graph()


# ---------------------------------------------------------------------------
# Conversation history → Bedrock messages format converter
# ---------------------------------------------------------------------------

def _history_to_bedrock_messages(
    message_history: list,  # list[ChatMessage]
) -> list[dict]:
    """Convert ChatMessage list to Bedrock Converse messages format.

    Parameters
    ----------
    message_history:
        Ordered list of ChatMessage objects (oldest first).

    Returns
    -------
    list[dict]
        Bedrock-compatible message list with role + content blocks.
    """
    result: list[dict] = []
    for msg in message_history:
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", "")
        if role not in ("user", "assistant"):
            continue
        result.append({
            "role": role,
            "content": [{"text": content}],
        })
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def invoke_chat_agent(
    session_id: str,
    message_history: list,      # list of ChatMessage objects from ChatRepository
    new_message_content: str,
    context,                    # ChatMessageContext | None
    persisted_agent_state: dict | None = None,  # loaded from ChatRepository (future)
) -> tuple[str, list[dict], int, dict]:
    """Invoke the chat agent for a single conversational turn.

    Rehydrates ChatAgentState from persisted_agent_state (or initializes fresh),
    converts message_history to Bedrock messages format, appends the new user
    message, invokes the compiled graph, and returns the reply.

    Parameters
    ----------
    session_id:
        Chat session UUID.
    message_history:
        All messages for the session so far (includes the user message just
        persisted by the route handler), oldest first.
    new_message_content:
        The text content of the user's latest message.
    context:
        ChatMessageContext with optional experiment_id, strategy_id, backtest_id,
        conversation_mode.
    persisted_agent_state:
        Optional dict from a prior turn (currently unused — reserved for Phase 7
        session-level state persistence).

    Returns
    -------
    tuple[str, list[dict], int, dict]
        - reply_text: the assistant's response
        - actions: list of action event dicts emitted this turn
        - total_tokens: total Bedrock tokens consumed
        - updated_agent_state: serializable dict to persist back to the session
    """
    trace_id = str(uuid.uuid4())

    # Build context dict from ChatMessageContext model
    context_dict: dict = {}
    if context is not None:
        if hasattr(context, "model_dump"):
            context_dict = {k: v for k, v in context.model_dump().items() if v is not None}
        elif isinstance(context, dict):
            context_dict = {k: v for k, v in context.items() if v is not None}

    # Carry forward conversation flow state from persisted_agent_state
    prior_state: dict = persisted_agent_state or {}
    conversation_stage: str = prior_state.get("conversation_stage", "active")
    last_detected_mode: str | None = prior_state.get("last_detected_mode")
    pending_action: dict | None = prior_state.get("pending_action")
    scope: dict = prior_state.get("scope") or {}
    context_cache: dict = prior_state.get("context_cache") or {}
    errors: list[str] = prior_state.get("errors") or []

    # Apply conversation_mode hint from context if present
    if context_dict.get("conversation_mode") and not last_detected_mode:
        last_detected_mode = context_dict["conversation_mode"]

    # Convert history to Bedrock messages (message_history already includes the new user message)
    bedrock_messages = _history_to_bedrock_messages(message_history)

    # Build initial state
    initial_state: ChatAgentState = ChatAgentState(
        session_id=session_id,
        trace_id=trace_id,
        bedrock_messages=bedrock_messages,
        last_detected_mode=last_detected_mode,
        conversation_stage=conversation_stage,
        next_node=NODE_NAME_RESEARCH,  # placeholder; intake will overwrite
        message_context=context_dict,
        context_cache=context_cache,
        draft_strategy_definition=prior_state.get("draft_strategy_definition"),
        scope=scope,
        pending_action=pending_action,
        last_executed_action_result=prior_state.get("last_executed_action_result"),
        pending_reply_text=None,
        pending_actions=[],
        total_tokens=0,
        errors=errors,
    )

    # Invoke the compiled graph with ainvoke so async nodes run in the same event loop
    final_state: ChatAgentState = await _CHAT_GRAPH.ainvoke(initial_state)

    # Extract outputs
    reply_text: str = final_state.get("pending_reply_text") or ""
    actions: list[dict] = final_state.get("pending_actions") or []
    total_tokens: int = final_state.get("total_tokens") or 0

    # Build serializable state to persist back to the session
    updated_agent_state: dict = {
        "session_id": session_id,
        "trace_id": trace_id,
        "conversation_stage": final_state.get("conversation_stage", "active"),
        "last_detected_mode": final_state.get("last_detected_mode"),
        "pending_action": final_state.get("pending_action"),
        "draft_strategy_definition": final_state.get("draft_strategy_definition"),
        "scope": final_state.get("scope") or {},
        "context_cache": final_state.get("context_cache") or {},
        "last_executed_action_result": final_state.get("last_executed_action_result"),
        "errors": final_state.get("errors") or [],
    }

    return reply_text, actions, total_tokens, updated_agent_state
