"""StrategyResearcher agent node — generates and mutates rules-based trading strategies.

Uses the Bedrock Converse API in a multi-turn tool-call loop.  The node produces a
StrategyCandidate (hypothesis + strategy + backtest results) and writes it back to
AgentState via LangGraph merge semantics.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import botocore.exceptions
from pydantic import BaseModel, ValidationError

from backend.agents.providers.bedrock import BedrockAdapter, ConverseResult
from backend.agents.providers.logging import AgentLogger
from backend.agents.state import AgentState
from backend.agents.tools.truncation import (
    truncate_equity_curve,
    truncate_metrics,
    truncate_trades,
)
from backend.agents.tools.backtest import (
    get_backtest_run,
    get_backtest_trades,
    get_equity_curve,
    get_hmm_model,
    poll_job,
    submit_backtest,
)
from backend.agents.tools.client import MedallionClient, ToolCallError
from backend.agents.tools.schemas import (
    CreateStrategyInput,
    GetBacktestRunInput,
    GetBacktestTradesInput,
    GetEquityCurveInput,
    GetHmmModelInput,
    PollJobInput,
    StrategyCandidate,
    SubmitBacktestInput,
    ValidateStrategyInput,
)
from backend.agents.tools.strategy import create_strategy, validate_strategy

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

NODE_NAME = "strategy_researcher"
MAX_TOOL_RETRIES = 3
POLL_INTERVAL_SECONDS = 5.0
MAX_POLL_ATTEMPTS = 120
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]

SYSTEM_PROMPT = """You are a quantitative Forex strategy researcher for a professional trading platform.
Your role is to generate and mutate rules-based trading strategies for a specific currency pair and market timeframe.

You have access to the following tools:
- create_strategy: Persist a new strategy definition as structured rules-engine JSON
- validate_strategy: Pre-flight validate a strategy definition before backtesting
- submit_backtest: Launch a backtest job for a strategy
- poll_job: Check the current status of a running job (call repeatedly until SUCCEEDED or FAILED)
- get_backtest_run: Retrieve full performance metrics for a completed backtest run
- get_backtest_trades: Retrieve the full trade log for a completed backtest run
- get_equity_curve: Retrieve bar-by-bar equity and drawdown series

You will receive a research context describing: instrument, timeframe, date range, the current task
(generate_seed OR mutate), any prior backtest metrics, any diagnostician recommendations, and
the HMM regime distribution for the instrument.

When generating a seed strategy:
1. Write a brief natural language hypothesis (2-3 sentences) grounded in the regime context.
2. Translate it into a valid rules_engine JSON definition using these fields:
   - entry_long: rule node tree (composite all/any/not or leaf field comparisons)
   - entry_short: rule node tree (optional, may be null)
   - exit: rule node tree (optional, overrides stop/target)
   - stop_atr_multiplier: float (recommended range 1.5-3.0)
   - take_profit_atr_multiplier: float (recommended range 2.0-5.0)
   - position_size_units: integer (always use 1000)
   - named_conditions: dict of reusable named rule nodes (optional)
3. Available feature fields for rule conditions:
   - When FEATURE_AVAILABILITY is FULL: log_ret_1, log_ret_5, log_ret_20, rvol_20,
     atr_14, atr_pct_14, rsi_14, ema_slope_20, ema_slope_50, adx_14, breakout_20, session,
     regime_label (the HMM semantic regime string, e.g. "TREND_BULL_LOW_VOL")
   - When FEATURE_AVAILABILITY is NONE: ONLY use native fields: open, high, low, close,
     volume, bars_in_trade, minutes_in_trade, days_in_trade, regime_label, state_id.
     Do NOT use session, day_of_week, hour_of_day, rsi_14, atr_14, or any other
     feature-computed or calendar field. Call list_native_fields for the complete safe list.

NATIVE FIELDS (always available, no feature run needed):
open, high, low, close, volume, bars_in_trade, minutes_in_trade, days_in_trade,
regime_label, state_id, instrument_id, timestamp_utc

RULE: If a backtest fails with "Field '...' not found in context", the field is not
available in this bar context. Switch to native fields only. Do NOT retry with the
same feature-dependent field. Call list_native_fields to see all safe options.

RULE: Before using any field not in the NATIVE FIELDS list above, call inspect_capability.
If it returns requires_feature_run=true and FEATURE_AVAILABILITY is NONE, do not use
that field — it will crash the backtest with a runtime field-not-found error.

3a. REQUIRED RULES (non-negotiable):
   (a) position_size_units MUST be a positive integer >= 1000, never 0 or null
   (b) Do NOT set cooldown_hours above 4 — omit to use default of 0
   (c) Do NOT use FULL feature fields when FEATURE_AVAILABILITY is NONE
   (d) Entry thresholds must fire on >=5% of bars:
       RSI long entry: 20-45, RSI short entry: 55-80
       ADX trend filter: < 30 (not > 40)
       ema_slope near-zero: between -0.001 and 0.001
4. Call validate_strategy. If it returns errors, revise and retry up to 3 times.
5. Call create_strategy with the valid definition.
6. Call submit_backtest with the strategy_id and the date range and instrument from context.
7. Call poll_job repeatedly until status is SUCCEEDED or FAILED.
   If poll_job returns status FAILED, the response includes an error_message field explaining why (e.g. no bars for instrument/date range, or wrong instrument format). Use it to correct the request (e.g. use the exact instrument from context, such as EUR_USD with underscore) before retrying submit_backtest.
8. If SUCCEEDED, call get_backtest_run, get_backtest_trades, and get_equity_curve.
9. Return a StrategyCandidate JSON object with all results.

When mutating an existing strategy:
1. Read the mutation_plan and recommended_mutations from context.
2. Apply the recommended mutations to the existing strategy_definition.
3. Follow steps 3-9 from seed generation above.

Output a JSON object with this exact schema at the end of your turn:
{
  "candidate_id": "<UUID>",
  "hypothesis": "<natural language hypothesis>",
  "strategy_id": "<strategy UUID>",
  "strategy_definition": {<rules_engine JSON>},
  "backtest_run_id": "<run UUID>",
  "metrics": {<metric_name>: <value>, ...},
  "trade_count": <int>,
  "sharpe": <float or null>,
  "max_drawdown_pct": <float or null>,
  "win_rate": <float or null>,
  "generation": <int>
}

Rules for the rules_engine JSON:
- Composite nodes: {"all": [...]}, {"any": [...]}, {"not": <node>}
- Leaf nodes: {"field": "<name>", "op": "<gt|gte|lt|lte|eq|neq|in>", "value": <scalar or list>}
- Field-to-field: {"field": "<name>", "op": "<op>", "field2": "<name>"}
- Named ref: {"ref": "<condition_name>"}
- Do NOT use field names not in the Available feature fields list above.
- regime_label comparisons must use op "eq" or "in" with exact label strings.

When the user requests calendar/time-derived logic, holding-period logic, trade lifecycle markers,
or native exit primitives, call inspect_capability to classify the field before drafting rules.
Do not say a capability is unsupported without first calling inspect_capability.
"""

async def _load_memory_context(instrument: str, timeframe: str) -> str:
    """Load past research memories and format as context block.

    Returns empty string on any error — never raises.
    """
    try:
        from backend.deps import get_memory_store
        store = get_memory_store()
        memories = store.get_context_for_run(instrument, timeframe, limit=5)
        if not memories:
            return ""
        lines = ["PAST_RESEARCH_MEMORIES (use these to avoid repeating failed approaches):"]
        for i, mem in enumerate(memories, 1):
            lines.append(f"\n[Memory {i}] {mem.instrument} {mem.timeframe} — outcome: {mem.outcome}")
            if mem.sharpe is not None:
                lines.append(f"  Sharpe: {mem.sharpe:.3f}, Trades: {mem.total_trades}")
            lines.append(f"  Theory: {mem.theory}")
            lines.append(f"  Results: {mem.results_reasoning}")
            lines.append(f"  Learnings:")
            for learning in mem.learnings:
                lines.append(f"    - {learning}")
            lines.append(f"  Tags: {', '.join(mem.tags)}")
        return "\n".join(lines)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# inspect_capability — inline input model and executor
# ---------------------------------------------------------------------------

class InspectCapabilityInput(BaseModel):
    field_name: str


async def _exec_inspect_capability_researcher(inp: InspectCapabilityInput, client) -> dict:
    from backend.strategies.capabilities import inspect_capability
    record = inspect_capability(inp.field_name)
    return {
        "name": record.name,
        "taxonomy": record.taxonomy.value,
        "description": record.description,
        "available": record.available,
        "resolution_hint": record.resolution_hint,
        "requires_feature_run": record.requires_feature_run,
    }


class _ListNativeFieldsInput(BaseModel):
    pass  # no inputs required


async def _exec_list_native_fields(inp: _ListNativeFieldsInput, client) -> dict:
    from backend.strategies.capabilities import list_native_fields
    return {"native_fields": list_native_fields()}


# ---------------------------------------------------------------------------
# RESEARCHER_TOOLS — built from model JSON schemas at module load time
# ---------------------------------------------------------------------------

RESEARCHER_TOOLS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "create_strategy",
            "description": "Persist a new strategy definition as structured rules-engine JSON and return the created strategy record including its UUID.",
            "inputSchema": {"json": CreateStrategyInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "validate_strategy",
            "description": "Pre-flight validate a strategy definition before backtesting. Returns valid=true and empty errors on success.",
            "inputSchema": {"json": ValidateStrategyInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "submit_backtest",
            "description": "Launch a backtest job for a strategy. Returns job_id and initial status.",
            "inputSchema": {"json": SubmitBacktestInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "poll_job",
            "description": "Check the current status of a running job. Call repeatedly until status is SUCCEEDED or FAILED.",
            "inputSchema": {"json": PollJobInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "get_backtest_run",
            "description": "Retrieve full performance metrics for a completed backtest run.",
            "inputSchema": {"json": GetBacktestRunInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "get_backtest_trades",
            "description": "Retrieve the full trade log for a completed backtest run.",
            "inputSchema": {"json": GetBacktestTradesInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "get_equity_curve",
            "description": "Retrieve bar-by-bar equity and drawdown series for a backtest run.",
            "inputSchema": {"json": GetEquityCurveInput.model_json_schema()},
        }
    },
    {
        "toolSpec": {
            "name": "inspect_capability",
            "description": (
                "Classify a named field or capability and determine whether it is available "
                "in the current strategy context. Returns the taxonomy (MARKET_FEATURE, "
                "STATE_MARKER, NATIVE_PRIMITIVE, SIGNAL_FIELD, or UNKNOWN), a description, "
                "availability status, requires_feature_run flag, and a resolution hint. "
                "If requires_feature_run=true and FEATURE_AVAILABILITY is NONE, do not use "
                "that field — it will crash the backtest. Call this before saying a feature "
                "or capability is unsupported."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "field_name": {
                            "type": "string",
                            "description": "The field or capability name to inspect (e.g. 'days_in_trade', 'day_of_week', 'exit_before_weekend')",
                        }
                    },
                    "required": ["field_name"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_native_fields",
            "description": (
                "Return all fields unconditionally available in the backtest bar context "
                "(OHLCV, volume, trade lifecycle markers like bars_in_trade/days_in_trade). "
                "Use this as a starting point when FEATURE_AVAILABILITY is NONE or to "
                "avoid feature-not-found runtime errors. Fields NOT in this list require "
                "a feature_run_id or a materialized signal to be present."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }
            },
        }
    },
]

# ---------------------------------------------------------------------------
# Tool dispatcher — maps tool name to (executor_fn, input_model_class)
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, tuple[Any, Any]] = {
    "create_strategy":     (create_strategy,                    CreateStrategyInput),
    "validate_strategy":   (validate_strategy,                  ValidateStrategyInput),
    "submit_backtest":     (submit_backtest,                    SubmitBacktestInput),
    "poll_job":            (poll_job,                           PollJobInput),
    "get_backtest_run":    (get_backtest_run,                   GetBacktestRunInput),
    "get_backtest_trades": (get_backtest_trades,                GetBacktestTradesInput),
    "get_equity_curve":    (get_equity_curve,                   GetEquityCurveInput),
    "inspect_capability":  (_exec_inspect_capability_researcher, InspectCapabilityInput),
    "list_native_fields":  (_exec_list_native_fields,             _ListNativeFieldsInput),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_message(state: AgentState, regime_context: dict[str, Any] | None) -> str:
    """Construct the user message from AgentState fields in the specified order."""
    task = state.get("task", "generate_seed")
    instrument = state.get("instrument", "EUR_USD")
    timeframe = state.get("timeframe", "H4")
    test_start = state.get("test_start", "")
    test_end = state.get("test_end", "")
    model_id = state.get("model_id") or "none"
    feature_run_id = state.get("feature_run_id") or "none"
    generation = state.get("generation", 0)

    ctx_json = (
        json.dumps(regime_context, indent=2)
        if regime_context and "error" not in regime_context
        else "not available"
    )

    if feature_run_id == "none":
        feature_availability = (
            "FEATURE_AVAILABILITY: NONE — no feature pipeline run exists for this "
            "instrument/timeframe. Rules may ONLY reference native bar fields: "
            "open, high, low, close, volume, bars_in_trade, minutes_in_trade, "
            "days_in_trade. Do NOT use session, day_of_week, hour_of_day, rsi_14, "
            "atr_14, atr_pct_14, log_ret_*, ema_slope_*, adx_14, rvol_20, "
            "breakout_20, or any cyclical/calendar fields — these require a feature "
            "run and will crash the backtest. Call list_native_fields for the safe list."
        )
    else:
        feature_availability = (
            f"FEATURE_AVAILABILITY: FULL — feature run {feature_run_id} is available. "
            "All 28 feature columns are accessible in strategy rules."
        )

    lines = [
        f"TASK: {task}",
        f"INSTRUMENT: {instrument}",
        f"TIMEFRAME: {timeframe}",
        f"PERIOD: {test_start} to {test_end}",
        f"HMM_MODEL: {model_id}",
        f"FEATURE_RUN: {feature_run_id}",
        feature_availability,
        f"GENERATION: {generation}",
        f"REGIME_CONTEXT: {ctx_json}",
    ]

    if task == "mutate":
        prior_def = state.get("strategy_definition")
        if prior_def:
            lines.append(f"PRIOR_STRATEGY: {json.dumps(prior_def, indent=2)}")
        prior_metrics = state.get("backtest_metrics")
        if prior_metrics:
            lines.append(f"PRIOR_METRICS: {json.dumps(prior_metrics, indent=2)}")
        diagnosis = state.get("diagnosis_summary")
        if diagnosis:
            lines.append(f"DIAGNOSIS: {diagnosis}")
        mutations = state.get("recommended_mutations")
        if mutations:
            bullet_list = "\n".join(f"- {m}" for m in mutations)
            lines.append(f"RECOMMENDED_MUTATIONS:\n{bullet_list}")
        mutation_plan = state.get("mutation_plan")
        if mutation_plan:
            lines.append(f"MUTATION_PLAN: {mutation_plan}")

    return "\n".join(lines)


def _extract_json_from_text(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a text string (handles markdown fences and nested braces)."""
    import re
    content = text
    # If there is a fenced block, extract the inner content first (so nested JSON is handled)
    fence_open = re.search(r"```(?:json)?\s*\n?", content)
    if fence_open:
        rest = content[fence_open.end() :]
        fence_close = rest.find("```")
        if fence_close != -1:
            content = rest[:fence_close]
    # Find the first { and then match braces to get the full object (handles nested { })
    start = content.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    depth = 0
    for i, ch in enumerate(content[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start : i + 1])
    raise ValueError("Malformed JSON in LLM response — unmatched braces")


from backend.strategies.field_registry import ALWAYS_AVAILABLE_FIELDS, FEATURE_REQUIRED_FIELDS

_FEATURE_ONLY_FIELDS: frozenset[str] = FEATURE_REQUIRED_FIELDS


def _validate_candidate_definition(
    definition: dict[str, Any],
    feature_run_id: str | None,
) -> list[str]:
    """Sanity-check a generated strategy definition. Non-raising — returns warning strings."""
    warnings: list[str] = []

    units = definition.get("position_size_units")
    if units is not None:
        try:
            if float(units) < 1000:
                warnings.append(f"position_size_units={units} below minimum 1000")
        except (TypeError, ValueError):
            warnings.append(f"position_size_units={units!r} is not numeric")

    cooldown = definition.get("cooldown_hours")
    if cooldown is not None:
        try:
            if float(cooldown) > 4.0:
                warnings.append(
                    f"cooldown_hours={cooldown} likely causes zero trades on short windows"
                )
        except (TypeError, ValueError):
            pass

    if feature_run_id is None:
        try:
            from backend.strategies.rules_engine import validate_signal_fields
            unresolved = set(validate_signal_fields(definition, set(ALWAYS_AVAILABLE_FIELDS)))
            # Only warn about known feature fields — never flag OHLC or bar metadata
            feature_fields_used = unresolved & _FEATURE_ONLY_FIELDS
            if feature_fields_used:
                warnings.append(
                    f"Feature fields used without feature_run_id: {sorted(feature_fields_used)}"
                )
        except Exception:
            pass  # never block on import errors

    return warnings


_TRUNCATORS = {
    "get_equity_curve": truncate_equity_curve,
    "get_backtest_trades": truncate_trades,
    "get_backtest_run": truncate_metrics,
}


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
        # Unknown tool — return error to LLM
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

    # Validate input
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

    # Execute
    try:
        output = await executor_fn(inp, client)
        serialized = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        if tool_name in _TRUNCATORS:
            serialized = _TRUNCATORS[tool_name](serialized)
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


async def _run_researcher(state: AgentState) -> dict[str, Any]:
    """Async implementation of strategy_researcher_node."""
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    trace_id = state.get("trace_id", "")
    prior_errors: list[str] = list(state.get("errors") or [])
    prior_candidates: list[dict[str, Any]] = list(state.get("strategy_candidates") or [])

    t_node_start = time.monotonic()
    _logger.node_enter(NODE_NAME, trace_id, list(state.keys()))

    adapter = BedrockAdapter()
    client = MedallionClient()

    # ── Step 1: Load regime context if needed ──────────────────────────────
    regime_context: dict[str, Any] | None = state.get("regime_context")
    regime_context_loaded: dict[str, Any] | None = None

    if regime_context is None and state.get("model_id") is not None:
        try:
            hmm_out = await get_hmm_model(
                GetHmmModelInput(model_id=state["model_id"]), client
            )
            regime_context_loaded = {
                "model_id": hmm_out.id,
                "instrument": hmm_out.instrument_id,
                "timeframe": hmm_out.timeframe,
                "num_states": hmm_out.num_states,
                "label_map": hmm_out.label_map,
                "state_stats": [s.model_dump(mode="json") for s in hmm_out.state_stats],
            }
            regime_context = regime_context_loaded
        except ToolCallError as exc:
            _logger.node_error(NODE_NAME, trace_id, f"get_hmm_model failed: {exc}")
            regime_context = {"error": "unavailable"}
            regime_context_loaded = regime_context

    # ── Step 2: Build initial messages ─────────────────────────────────────
    user_message_text = _build_user_message(state, regime_context)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_message_text}]}
    ]

    # ── Step 2b: Augment system prompt with marker_action context ───────────
    marker_action: str | None = state.get("marker_action")
    effective_system_prompt = SYSTEM_PROMPT
    if marker_action == "lock":
        candidates = state.get("strategy_candidates") or []
        locked_def: dict[str, Any] | None = None
        if candidates:
            locked_def = candidates[-1].get("strategy_definition")
        seed_json = (
            f"\n\nMARKER_ACTION=lock: A breakthrough candidate has been detected. "
            f"You MUST use the following strategy definition as the mandatory seed for "
            f"the next generation hypothesis — preserve its core structure and make only "
            f"targeted improvements:\n{json.dumps(locked_def or {}, indent=2)}"
        )
        effective_system_prompt = SYSTEM_PROMPT + seed_json
    elif marker_action == "explore":
        effective_system_prompt = (
            SYSTEM_PROMPT
            + "\n\nMARKER_ACTION=explore: High uncertainty or surprising results detected. "
            "Significantly broaden mutation parameter ranges — try structurally different "
            "entry/exit rules, different feature combinations, and wider stop/target "
            "multiplier ranges than you would normally use."
        )
    elif marker_action == "exploit":
        effective_system_prompt = (
            SYSTEM_PROMPT
            + "\n\nMARKER_ACTION=exploit: Consistent improvement trend detected. "
            "Make small, targeted refinements to the current best strategy — tighten "
            "parameters, adjust thresholds incrementally, and avoid large structural changes."
        )
    # marker_action == "continue" or None: no change to system prompt

    # ── Memory context injection ─────────────────────────────────────────────
    try:
        memory_context = await _load_memory_context(
            state.get("instrument", ""), state.get("timeframe", "")
        )
        if memory_context:
            effective_system_prompt += "\n\n" + memory_context
    except Exception:
        pass  # never fail the researcher

    # ── Step 3: Multi-turn tool-call loop ───────────────────────────────────
    total_tool_calls = 0
    max_total_tool_calls = MAX_TOOL_RETRIES * 10
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    result: ConverseResult | None = None

    for attempt in range(len(BEDROCK_THROTTLE_BACKOFF_SECONDS) + 1):
        try:
            t_llm = time.monotonic()
            result = await adapter.converse(
                messages=messages,
                system_prompt=effective_system_prompt,
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
            cumulative_input_tokens += result.input_tokens
            cumulative_output_tokens += result.output_tokens
            if cumulative_input_tokens > 50_000:
                _logger.node_error(NODE_NAME, trace_id,
                    f"TOKEN_BUDGET_WARNING: cumulative_input_tokens={cumulative_input_tokens}")
            break  # success — exit throttle retry loop
        except botocore.exceptions.ClientError as exc:
            err_code = exc.response.get("Error", {}).get("Code", "")
            if err_code == "ThrottlingException" and attempt < len(BEDROCK_THROTTLE_BACKOFF_SECONDS):
                backoff = BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt]
                _logger.node_error(NODE_NAME, trace_id,
                                   f"ThrottlingException attempt {attempt}, backoff {backoff}s")
                await asyncio.sleep(backoff)
                continue
            # Non-throttle error or exhausted retries
            duration_ms = int((time.monotonic() - t_node_start) * 1000)
            _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
            return {
                "next_node": "supervisor",
                "errors": prior_errors + [f"strategy_researcher: Bedrock error: {exc}"],
                "task": "done",
            }
    else:
        # All throttle retries exhausted
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "errors": prior_errors + ["strategy_researcher: Bedrock ThrottlingException — max retries exhausted"],
            "task": "done",
        }

    # ── Tool-call loop continuation ─────────────────────────────────────────
    while result is not None and result.stop_reason == "tool_use":
        if total_tool_calls >= max_total_tool_calls:
            break

        # Append the assistant message (with the tool_use block)
        assistant_content: list[dict[str, Any]] = []
        if result.content:
            assistant_content.append({"text": result.content})
        if result.tool_use:
            assistant_content.append({"toolUse": result.tool_use})
        messages.append({"role": "assistant", "content": assistant_content})

        # Extract tool name and input
        extracted = BedrockAdapter.extract_tool_use(result)
        if extracted is None:
            break

        tool_name, tool_input_dict = extracted
        tool_use_id = result.tool_use.get("toolUseId", str(uuid.uuid4()))  # type: ignore[union-attr]
        total_tool_calls += 1

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
                    system_prompt=effective_system_prompt,
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
                cumulative_input_tokens += result.input_tokens
                cumulative_output_tokens += result.output_tokens
                if cumulative_input_tokens > 50_000:
                    _logger.node_error(NODE_NAME, trace_id,
                        f"TOKEN_BUDGET_WARNING: cumulative_input_tokens={cumulative_input_tokens}")
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
                    "errors": prior_errors + [f"strategy_researcher: Bedrock error during tool loop: {exc}"],
                    "task": "done",
                }
        else:
            duration_ms = int((time.monotonic() - t_node_start) * 1000)
            _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
            return {
                "next_node": "supervisor",
                "errors": prior_errors + ["strategy_researcher: ThrottlingException in tool loop — max retries exhausted"],
                "task": "done",
            }

    # ── Step 4: Parse final end_turn response ──────────────────────────────
    if result is None:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "errors": prior_errors + ["strategy_researcher: no result from LLM"],
            "task": "done",
        }

    try:
        raw_json = _extract_json_from_text(result.content)
        # Inject created_at if not present (LLM may omit it)
        if "created_at" not in raw_json:
            raw_json["created_at"] = datetime.now(tz=timezone.utc).isoformat()
        # Inject candidate_id if missing
        if "candidate_id" not in raw_json or not raw_json["candidate_id"]:
            raw_json["candidate_id"] = str(uuid.uuid4())
        # Inject generation from state if missing
        if "generation" not in raw_json:
            raw_json["generation"] = state.get("generation", 0)
        # strategy_definition is required — default to empty dict if absent
        if "strategy_definition" not in raw_json:
            raw_json["strategy_definition"] = {}

        candidate = StrategyCandidate.model_validate(raw_json)
        for w in _validate_candidate_definition(
            candidate.strategy_definition, state.get("feature_run_id")
        ):
            _logger.node_error(NODE_NAME, trace_id, f"CANDIDATE_VALIDATION: {w}")
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        duration_ms = int((time.monotonic() - t_node_start) * 1000)
        _logger.node_error(NODE_NAME, trace_id, f"StrategyCandidate parse failed: {exc}")
        _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")
        return {
            "next_node": "supervisor",
            "errors": prior_errors + [f"strategy_researcher: failed to parse StrategyCandidate: {exc}"],
            "task": "done",
        }

    # ── Step 5: Log state updates and write result ──────────────────────────
    if candidate.strategy_id:
        _logger.state_update(trace_id, "strategy_id", candidate.strategy_id)
    if candidate.backtest_run_id:
        _logger.state_update(trace_id, "backtest_run_id", candidate.backtest_run_id)

    duration_ms = int((time.monotonic() - t_node_start) * 1000)
    _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")

    result_dict: dict[str, Any] = {
        "next_node": "supervisor",
        "hypothesis": candidate.hypothesis,
        "strategy_id": candidate.strategy_id,
        "strategy_definition": candidate.strategy_definition,
        "backtest_run_id": candidate.backtest_run_id,
        "backtest_metrics": candidate.metrics,
        "backtest_trades": [],      # will be populated if LLM fetched trades via tool
        "equity_curve": [],         # will be populated if LLM fetched equity via tool
        "strategy_candidates": prior_candidates + [candidate.model_dump(mode="json")],
        "errors": prior_errors,
    }
    # When no backtest succeeded, end the run so supervisor routes to END instead of re-dispatching here
    if candidate.backtest_run_id is None:
        result_dict["task"] = "done"

    if regime_context_loaded is not None:
        result_dict["regime_context"] = regime_context_loaded

    return result_dict


def strategy_researcher_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered strategy generation and mutation.

    Runs an async multi-turn tool-call loop synchronously (via asyncio.run) so
    it is compatible with LangGraph's synchronous node interface.

    Parameters
    ----------
    state:
        The current AgentState flowing through the graph.

    Returns
    -------
    dict
        Partial state update. LangGraph merges this back into the full state.
    """
    return asyncio.run(_run_researcher(state))
