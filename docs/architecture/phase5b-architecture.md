# Phase 5B Architecture — Agentic Research Layer, Stage 2 Implementation

**File:** `docs/architecture/phase5b-architecture.md`
**Date:** 2026-03-15
**Status:** Implementation Design — Ready for Claude Code Execution
**Supersedes:** `docs/architecture/phase5-architecture.md` (Stage 1 Foundation)
**Prerequisite reading:** `docs/reviews/phase5-stage1-review.md` (all Warnings and Stage 2 recommendations apply)

---

## Summary

Phase 5B converts the three stub agent nodes (strategy_researcher, backtest_diagnostics, generation_comparator) into full Bedrock-powered LLM nodes, builds the `backend/lab/` persistence layer, and adds the `POST /api/research/run` HTTP trigger. All Stage 1 infrastructure (state, graph, tools, providers) is preserved and extended — not replaced.

This document is the authoritative specification for implementation. Every class name, field name, function signature, constant name, and data flow described here must be followed exactly. Implementation agents must not invent alternatives.

---

## 1. New Files Created in Phase 5B

```
backend/agents/
    strategy_researcher.py      REPLACE stub with full implementation
    backtest_diagnostics.py     REPLACE stub with full implementation
    generation_comparator.py    REPLACE stub with full implementation

backend/lab/
    __init__.py                 NEW — module docstring + __all__
    experiment_registry.py      NEW — ExperimentRecord, ExperimentRegistry
    evaluation.py               NEW — ExperimentScore, score_experiment, compare_experiments
    mutation.py                 NEW — perturb_parameters, substitute_rule, inject_regime_filter

apps/api/routes/
    research.py                 NEW — POST /api/research/run, GET /api/research/runs/{id}

backend/schemas/
    requests.py                 EXTEND — add ResearchRunRequest, ResearchRunResponse

backend/agents/
    state.py                    EXTEND — add 6 new fields to AgentState
```

Files that must NOT be modified in Phase 5B:
- `backend/agents/graph.py` — topology is already complete
- `backend/agents/providers/bedrock.py` — adapter is already complete
- `backend/agents/providers/logging.py` — logger is already complete
- `backend/agents/tools/` — tool executors are already complete (additive only)
- All `backend/strategies/`, `backend/backtest/`, `backend/models/` modules

---

## 2. AgentState Extensions

Add these six fields to the `AgentState` TypedDict in `backend/agents/state.py`. Update both `DEFAULT_STATE` and `make_default_state` to include all six with their default values.

### 2.1 New Fields

```python
# Regime context — populated by strategy_researcher_node on first invocation
regime_context: dict[str, Any] | None

# Strategy artifacts
strategy_candidates: list[dict[str, Any]] | None
selected_candidate_id: str | None

# Structured diagnosis (dict form of DiagnosticSummary)
diagnostic_summary: dict[str, Any] | None

# Structured comparison result
comparison_result: dict[str, Any] | None
```

`regime_context` shape:
```python
{
    "model_id": "abc-123",
    "instrument": "EUR_USD",
    "timeframe": "H4",
    "num_states": 7,
    "label_map": {"0": "TREND_BULL_LOW_VOL", ...},
    "state_stats": [
        {"state_id": 0, "label": "TREND_BULL_LOW_VOL", "mean_return": 0.0003,
         "mean_adx": 28.5, "mean_volatility": 0.0012, "frequency_pct": 14.2},
    ]
}
```

`diagnostic_summary` replaces the stub `diagnosis_summary: str | None` for structured output. Both fields coexist — `diagnosis_summary` (str) continues to be written for supervisor routing compatibility; `diagnostic_summary` (dict) is the authoritative structured output.

### 2.2 Default Values

```python
regime_context=None,
strategy_candidates=None,
selected_candidate_id=None,
diagnostic_summary=None,
comparison_result=None,
```

---

## 3. strategy_researcher_node Implementation Design

**File:** `backend/agents/strategy_researcher.py`

### 3.1 Module-Level Constants

```python
NODE_NAME = "strategy_researcher"
MAX_TOOL_RETRIES = 3
POLL_INTERVAL_SECONDS = 5.0
MAX_POLL_ATTEMPTS = 120
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]
```

### 3.2 System Prompt Constant

```python
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
3. Available feature fields for rule conditions: log_ret_1, log_ret_5, log_ret_20, rvol_20,
   atr_14, atr_pct_14, rsi_14, ema_slope_20, ema_slope_50, adx_14, breakout_20, session,
   regime_label (the HMM semantic regime string, e.g. "TREND_BULL_LOW_VOL")
4. Call validate_strategy. If it returns errors, revise and retry up to 3 times.
5. Call create_strategy with the valid definition.
6. Call submit_backtest with the strategy_id and the date range and instrument from context.
7. Call poll_job repeatedly until status is SUCCEEDED or FAILED.
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
"""
```

### 3.3 StrategyCandidate Pydantic v2 Model

Add to `backend/agents/tools/schemas.py`:

```python
class StrategyCandidate(BaseModel):
    candidate_id: str
    hypothesis: str
    strategy_id: str | None = None
    strategy_definition: dict[str, Any]
    backtest_run_id: str | None = None
    metrics: dict[str, float | None] = {}
    trade_count: int = 0
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    win_rate: float | None = None
    generation: int = 0
    created_at: str
    validation_errors: list[str] = []
    error: str | None = None
```

### 3.4 Input Construction from AgentState

Build the user message from these fields in this order:

```
TASK: generate_seed | mutate
INSTRUMENT: EUR_USD
TIMEFRAME: H4
PERIOD: 2023-01-01 to 2024-01-01
HMM_MODEL: abc-123  (or "none" if None)
FEATURE_RUN: def-456  (or "none" if None)
GENERATION: 0
REGIME_CONTEXT: <JSON block or "not available">
PRIOR_STRATEGY: <JSON block>  (only if task == "mutate")
PRIOR_METRICS: <JSON block>  (only if task == "mutate")
DIAGNOSIS: <text>  (only if task == "mutate" and non-None)
RECOMMENDED_MUTATIONS: <bullet list>  (only if task == "mutate" and non-None)
MUTATION_PLAN: <text>  (only if task == "mutate" and non-None)
```

### 3.5 Regime Context Loading

Before building the user message, if `state.get("regime_context") is None` and `state.get("model_id") is not None`:

1. Call `GET /api/models/hmm/{model_id}` via `MedallionClient.get()`.
2. Build the `regime_context` dict and write to the return dict (LangGraph merges it into state).
3. If `ToolCallError`, log and continue with `regime_context = {"error": "unavailable"}`. Do not abort the node.

### 3.6 Tool Call Loop

```
Turn 1: Send system_prompt + user_message to adapter.converse() with RESEARCHER_TOOLS
   → stop_reason == "end_turn": parse JSON from content, build StrategyCandidate, done
   → stop_reason == "tool_use": dispatch tool, append tool_result, go to Turn N+1

Turn N: Send accumulated messages to converse()
   → Continue until stop_reason == "end_turn" OR total tool calls > MAX_TOOL_RETRIES * 10
```

Tool dispatcher:

```python
_TOOL_DISPATCH = {
    "create_strategy":     (create_strategy,     CreateStrategyInput),
    "validate_strategy":   (validate_strategy,   ValidateStrategyInput),
    "submit_backtest":     (submit_backtest,      SubmitBacktestInput),
    "poll_job":            (poll_job,             PollJobInput),
    "get_backtest_run":    (get_backtest_run,     GetBacktestRunInput),
    "get_backtest_trades": (get_backtest_trades,  GetBacktestTradesInput),
    "get_equity_curve":    (get_equity_curve,     GetEquityCurveInput),
}
```

Each dispatch step:
1. Extract `(tool_name, tool_input_dict)` via `BedrockAdapter.extract_tool_use(result)`.
2. Validate: `inp = input_model_class.model_validate(tool_input_dict)` — on `ValidationError`, return a tool_result error to the LLM (do not raise).
3. Execute `await executor_fn(inp, client)` — on `ToolCallError`, append error as tool_result and continue.
4. Serialize output with `output.model_dump(mode="json")` and append as tool_result.
5. Call `_logger.tool_call(...)` after every dispatch.

tool_result message format:
```python
{
    "role": "user",
    "content": [{
        "toolResult": {
            "toolUseId": result.tool_use["toolUseId"],
            "content": [{"json": serialized_output_or_error_dict}],
            "status": "success" | "error",
        }
    }]
}
```

### 3.7 Error Handling Policy

- **ToolCallError (429/503):** Append error as tool_result, let LLM retry naturally.
- **Bedrock ThrottlingException:** Catch `botocore.exceptions.ClientError` where `Code == "ThrottlingException"`. Sleep `BEDROCK_THROTTLE_BACKOFF_SECONDS[attempt_index]`. Max 3 retries.
- **Unrecoverable:** Return `{"next_node": "supervisor", "errors": [...], "task": "done"}`. Setting `task="done"` prevents infinite retry.
- **Validation retry:** After 3 consecutive validation failures, set `task="done"` in return dict.

### 3.8 State Writes (on success)

```python
{
    "next_node": "supervisor",
    "hypothesis": candidate.hypothesis,
    "strategy_id": candidate.strategy_id,
    "strategy_definition": candidate.strategy_definition,
    "job_id": job_id,
    "backtest_run_id": candidate.backtest_run_id,
    "backtest_metrics": candidate.metrics,
    "backtest_trades": raw_trades_list,
    "equity_curve": raw_equity_list,
    "strategy_candidates": (prior_candidates or []) + [candidate.model_dump(mode="json")],
    "regime_context": regime_context,  # only if loaded in this invocation
    "errors": prior_errors,
}
```

### 3.9 Logging Calls

Instantiate `AgentLogger(session_id=state.get("session_id", ""))` at the start of each node function call (not as a module-level singleton — ensures session_id is in every event).

1. `_logger.node_enter(NODE_NAME, trace_id, list(state.keys()))` — at entry
2. `_logger.llm_call(model, trace_id, NODE_NAME, result.input_tokens, result.output_tokens, latency_ms, ...)` — after every `converse()` call
3. `_logger.tool_call(tool_name, trace_id, NODE_NAME, input_dict, output_summary, latency_ms, success=bool)` — after every dispatch
4. `_logger.state_update(trace_id, "strategy_id", candidate.strategy_id)` — when strategy created
5. `_logger.state_update(trace_id, "backtest_run_id", candidate.backtest_run_id)` — when backtest completes
6. `_logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")` — at exit

### 3.10 RESEARCHER_TOOLS Constant

Module-level constant in `strategy_researcher.py`, built from model JSON schemas:

```python
RESEARCHER_TOOLS = [
    {"toolSpec": {"name": "create_strategy", "description": "...", "inputSchema": {"json": CreateStrategyInput.model_json_schema()}}},
    {"toolSpec": {"name": "validate_strategy", "description": "...", "inputSchema": {"json": ValidateStrategyInput.model_json_schema()}}},
    {"toolSpec": {"name": "submit_backtest", "description": "...", "inputSchema": {"json": SubmitBacktestInput.model_json_schema()}}},
    {"toolSpec": {"name": "poll_job", "description": "...", "inputSchema": {"json": PollJobInput.model_json_schema()}}},
    {"toolSpec": {"name": "get_backtest_run", "description": "...", "inputSchema": {"json": GetBacktestRunInput.model_json_schema()}}},
    {"toolSpec": {"name": "get_backtest_trades", "description": "...", "inputSchema": {"json": GetBacktestTradesInput.model_json_schema()}}},
    {"toolSpec": {"name": "get_equity_curve", "description": "...", "inputSchema": {"json": GetEquityCurveInput.model_json_schema()}}},
]
```

---

## 4. backtest_diagnostics_node Implementation Design

**File:** `backend/agents/backtest_diagnostics.py`

### 4.1 Module-Level Constants

```python
NODE_NAME = "backtest_diagnostics"
MAX_TOOL_RETRIES = 2
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]
```

### 4.2 System Prompt Constant

```python
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
```

### 4.3 DiagnosticSummary and FailureTaxonomy Models

Add to `backend/agents/tools/schemas.py`:

```python
class FailureTaxonomy(str, Enum):
    ZERO_TRADES = "zero_trades"
    TOO_FEW_TRADES = "too_few_trades"
    EXCESSIVE_DRAWDOWN = "excessive_drawdown"
    POOR_SHARPE = "poor_sharpe"
    OVERFITTING_SIGNAL = "overfitting_signal"
    WRONG_REGIME_FILTER = "wrong_regime_filter"
    ENTRY_TOO_RESTRICTIVE = "entry_too_restrictive"
    EXIT_TOO_EARLY = "exit_too_early"
    EXIT_TOO_LATE = "exit_too_late"
    NO_EDGE = "no_edge"
    POSITIVE = "positive"


class DiagnosticSummary(BaseModel):
    failure_taxonomy: FailureTaxonomy
    root_cause: str
    recommended_mutations: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    discard: bool
```

### 4.4 Input Construction from AgentState

```
INSTRUMENT: <instrument>
TIMEFRAME: <timeframe>
GENERATION: <generation>

METRICS:
<backtest_metrics key: value, sorted by key>

TRADES SUMMARY:
  total_count: <len(backtest_trades)>
  winning_trades: <count where pnl > 0>
  losing_trades: <count where pnl <= 0>
  avg_pnl: <mean pnl>
  avg_holding_bars: <mean holding_period>
  largest_loss: <min pnl>
  largest_win: <max pnl>
  (or "TRADES SUMMARY: no trades recorded" if empty)

EQUITY SUMMARY:
  initial_equity / final_equity / max_drawdown_pct / recovery_ratio
  (or "EQUITY SUMMARY: not available" if empty)

STRATEGY:
<strategy_definition as indented JSON>

REGIME_CONTEXT:
<regime_context as JSON or "not available">
```

### 4.5 Zero-Trade Detection (Pre-LLM)

If `backtest_metrics.get("total_trades") == 0`, prepend to the user message:

```
ZERO_TRADE_CONTEXT: This strategy generated 0 trades. Focus entirely on why the entry conditions
are too restrictive. The failure_taxonomy must be "zero_trades". Do not recommend discard=true
unless GENERATION >= 5.
```

### 4.6 State Writes (on success)

```python
{
    "next_node": "supervisor",
    "diagnosis_summary": summary.root_cause,              # str for supervisor routing compat
    "diagnostic_summary": summary.model_dump(mode="json"), # structured output
    "recommended_mutations": summary.recommended_mutations,
    "discard": summary.discard,
    "mutation_plan": summary.root_cause,
    "errors": prior_errors,
}
```

On unrecoverable error:
```python
{
    "next_node": "supervisor",
    "diagnosis_summary": "diagnostics_failed",
    "diagnostic_summary": None,
    "discard": False,
    "errors": prior_errors + [f"backtest_diagnostics: {error_description}"],
}
```

### 4.7 Parse Failure Fallback

The diagnostics node makes a single Bedrock call (no tool calls). If the LLM response cannot be parsed as valid `DiagnosticSummary`, retry once with the parse error appended as: `"Your previous response could not be parsed. Error: {parse_error}. Please output only the JSON object."`. After the second failure, use:

```python
DiagnosticSummary(
    failure_taxonomy=FailureTaxonomy.NO_EDGE,
    root_cause="Automated diagnosis unavailable. Manual review required.",
    recommended_mutations=["Review entry conditions manually"],
    confidence=0.0,
    discard=False,
)
```

---

## 5. generation_comparator_node Implementation Design

**File:** `backend/agents/generation_comparator.py`

### 5.1 Module-Level Constants

```python
NODE_NAME = "generation_comparator"
BEDROCK_THROTTLE_BACKOFF_SECONDS = [2.0, 5.0, 15.0]
```

### 5.2 ComparisonResult Pydantic v2 Model

Add to `backend/agents/tools/schemas.py`:

```python
class ComparisonResult(BaseModel):
    winner_id: str | None
    winner_strategy_id: str | None
    rationale: str
    score_delta: float | None
    recommendation: str       # "continue" | "archive" | "discard"
    scores: dict[str, float]  # candidate_id -> composite score
```

### 5.3 System Prompt Constant

```python
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
```

### 5.4 Pre-LLM Guard

If `len(strategy_candidates) < 1`:
```python
return {
    "next_node": "supervisor",
    "comparison_recommendation": "discard",
    "comparison_result": None,
    "errors": prior_errors + ["generation_comparator: no candidates to compare"],
    "task": "done",
}
```

If `len(strategy_candidates) == 1`, still call the LLM — single candidate is valid input.

### 5.5 State Writes (on success)

```python
{
    "next_node": "supervisor",
    "comparison_recommendation": result.recommendation,
    "comparison_result": result.model_dump(mode="json"),
    "comparison_summary": result.rationale,
    "selected_candidate_id": result.winner_id,
    "strategy_id": result.winner_strategy_id,
    "errors": prior_errors,
}
```

---

## 6. backend/lab/ Module Design

### 6.1 experiment_registry.py

**File:** `backend/lab/experiment_registry.py`

#### ExperimentStatus Enum

```python
class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ARCHIVED = "archived"
    VALIDATED = "validated"
```

#### ExperimentRecord Pydantic v2 Model

```python
class ExperimentRecord(BaseModel):
    id: str
    session_id: str
    parent_id: str | None = None
    generation: int = 0
    status: ExperimentStatus = ExperimentStatus.PENDING
    instrument: str
    timeframe: str
    test_start: str
    test_end: str
    model_id: str | None = None
    feature_run_id: str | None = None
    task: str = "generate_seed"
    requested_by: str = "system"
    hypothesis: str | None = None
    strategy_id: str | None = None
    backtest_run_id: str | None = None
    score: float | None = None
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    win_rate: float | None = None
    total_trades: int | None = None
    failure_taxonomy: str | None = None
    comparison_recommendation: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    final_state_snapshot: dict[str, Any] | None = None
```

#### ExperimentRegistry Class

```python
class ExperimentRegistry:
    def __init__(self, metadata_repo: LocalMetadataRepository) -> None: ...

    def create(
        self,
        session_id: str,
        instrument: str,
        timeframe: str,
        test_start: str,
        test_end: str,
        task: str = "generate_seed",
        requested_by: str = "system",
        model_id: str | None = None,
        feature_run_id: str | None = None,
        parent_id: str | None = None,
        generation: int = 0,
    ) -> ExperimentRecord: ...

    def get(self, experiment_id: str) -> ExperimentRecord:
        # Raises KeyError if not found
        ...

    def update_status(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        *,
        hypothesis: str | None = None,
        strategy_id: str | None = None,
        backtest_run_id: str | None = None,
        score: float | None = None,
        sharpe: float | None = None,
        max_drawdown_pct: float | None = None,
        win_rate: float | None = None,
        total_trades: int | None = None,
        failure_taxonomy: str | None = None,
        comparison_recommendation: str | None = None,
        error_message: str | None = None,
        final_state_snapshot: dict[str, Any] | None = None,
    ) -> ExperimentRecord: ...
    # Only non-None kwargs overwrite existing fields; always updates updated_at

    def list_recent(
        self,
        limit: int = 20,
        instrument: str | None = None,
        status: ExperimentStatus | None = None,
    ) -> list[ExperimentRecord]: ...
    # Returns sorted by created_at descending

    def get_lineage(self, experiment_id: str) -> list[ExperimentRecord]: ...
    # Walk parent_id chain. Returns [root, child1, child2, ...] in generation order.
```

#### Storage Pattern

Store as JSON files in `LocalMetadataRepository` under subdirectory `experiments/`. Each file named `{experiment_id}.json`. Reuses existing repository file read/write methods — examine `backend/data/repositories.py` for the exact pattern.

`create()` assigns UUID via `str(uuid.uuid4())`, sets `created_at` and `updated_at` to `datetime.now(tz=timezone.utc).isoformat()`.

**No DuckDB table.** Experiment records are JSON metadata files, consistent with how strategies, models, and signals are stored.

#### Dependency Function

Add to `backend/deps.py`:
```python
@lru_cache(maxsize=1)
def get_experiment_registry() -> ExperimentRegistry:
    from backend.lab.experiment_registry import ExperimentRegistry
    return ExperimentRegistry(get_metadata_repo())
```

### 6.2 evaluation.py

**File:** `backend/lab/evaluation.py`

#### Module-Level Gate Constants

```python
MIN_TRADE_COUNT = 20
MIN_SHARPE = 0.1
MAX_DRAWDOWN_PCT = -30.0
MIN_WIN_RATE = 0.35
```

#### ExperimentScore Pydantic v2 Model

```python
class ExperimentScore(BaseModel):
    experiment_id: str
    composite_score: float
    sharpe_score: float
    drawdown_score: float
    activity_score: float
    win_rate_score: float
    sharpe: float | None
    max_drawdown_pct: float | None
    total_trades: int | None
    win_rate: float | None
    passed_minimum_gates: bool
    gate_failures: list[str]
```

#### score_experiment() Algorithm

```python
def score_experiment(record: ExperimentRecord) -> ExperimentScore:
```

1. **sharpe_score**: `min(max(sharpe / 2.0, 0.0), 1.0)` — None → 0.0
2. **drawdown_score**: `max(0.0, 1.0 - abs(max_drawdown_pct) / 50.0)` — None → 0.0
3. **activity_score**: `min(total_trades / 50.0, 1.0)` — None → 0.0
4. **win_rate_score**: `min(max((win_rate - 0.35) / 0.30, 0.0), 1.0)` — None → 0.0
5. **composite**: `(0.40 * sharpe_score) + (0.30 * drawdown_score) + (0.15 * activity_score) + (0.15 * win_rate_score)`

Gate failures are computed independently from scores.

#### compare_experiments()

```python
def compare_experiments(records: list[ExperimentRecord]) -> list[ExperimentScore]:
    # Score each record, return sorted descending by composite_score
```

### 6.3 mutation.py

**File:** `backend/lab/mutation.py`

All functions return new dicts — never mutate in place.

#### perturb_parameters()

```python
def perturb_parameters(
    definition: dict[str, Any],
    magnitude: float = 0.2,   # fractional ±perturbation, clamped to [0.01, 1.0]
) -> dict[str, Any]:
```

- Perturbs `stop_atr_multiplier` (range [0.5, 6.0]) and `take_profit_atr_multiplier` (range [0.5, 8.0])
- `position_size_units` always kept at 1000
- Direction: `random.choice([1, -1])`
- Invariant: `stop_atr_multiplier < take_profit_atr_multiplier` — swap if violated

#### substitute_rule()

```python
def substitute_rule(
    definition: dict[str, Any],
    rule_index: int,          # zero-based index into depth-first leaf traversal
    new_rule: dict[str, Any],
    target: str = "entry_long",   # "entry_long" | "entry_short" | "exit"
) -> dict[str, Any]:
```

- "Leaf node" = dict with "field" key (not composite all/any/not); named refs ("ref" key) count as leaves
- Raises `IndexError` if `rule_index >= len(leaf_nodes)`
- Raises `KeyError` if `target` not in definition

#### inject_regime_filter()

```python
def inject_regime_filter(
    definition: dict[str, Any],
    regime_label: str,        # must be in ALL_LABELS — raises ValueError otherwise
    target: str = "entry_long",
) -> dict[str, Any]:
```

- Wraps target rule tree in `{"all": [{"field": "regime_label", "op": "eq", "value": regime_label}, <original>]}`
- If original is already an "all" composite, prepend the regime leaf to the existing list
- If regime_label node already present, replace rather than duplicate
- Imports `ALL_LABELS` from `backend.models.labeling` for validation

---

## 7. POST /api/research/run — Supervisor Graph Trigger Flow

**File:** `apps/api/routes/research.py`

### 7.1 New Schemas (add to backend/schemas/requests.py)

```python
class ResearchRunRequest(BaseModel):
    instrument: str
    timeframe: str
    test_start: str               # ISO date "YYYY-MM-DD"
    test_end: str
    task: str = "generate_seed"   # "generate_seed" | "mutate"
    model_id: str | None = None
    feature_run_id: str | None = None
    parent_experiment_id: str | None = None  # required if task == "mutate"
    requested_by: str = "api"
    max_iterations: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def _require_parent_for_mutate(self) -> "ResearchRunRequest":
        if self.task == "mutate" and self.parent_experiment_id is None:
            raise ValueError("parent_experiment_id required when task is 'mutate'")
        return self


class ResearchRunResponse(BaseModel):
    experiment_id: str
    session_id: str
    status: str
    created_at: str
```

### 7.2 Routes

```python
router = APIRouter(prefix="/api/research", tags=["research"])

@router.post("/run", response_model=ResearchRunResponse, status_code=202)
async def trigger_research_run(
    body: ResearchRunRequest,
    background_tasks: BackgroundTasks,
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> ResearchRunResponse: ...

@router.get("/runs/{experiment_id}", response_model=ExperimentRecord)
async def get_research_run(
    experiment_id: str,
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> ExperimentRecord: ...

@router.get("/runs", response_model=list[ExperimentRecord])
async def list_research_runs(
    limit: int = Query(default=20, ge=1, le=100),
    instrument: str | None = Query(default=None),
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> list[ExperimentRecord]: ...
```

### 7.3 ExperimentRecord Creation Flow

1. If `task == "mutate"`, resolve generation: `registry.get(parent_experiment_id).generation + 1` (404 if not found)
2. Call `registry.create(...)` with all fields from request
3. Add background task: `background_tasks.add_task(_run_research_graph, ...)`
4. Return `ResearchRunResponse` immediately (202)

### 7.4 Background Task: _run_research_graph

```python
async def _run_research_graph(
    experiment_id: str,
    session_id: str,
    body: ResearchRunRequest,
    registry: ExperimentRegistry,
) -> None:
```

Use `BackgroundTasks` (not `asyncio.create_task`). Rationale: FastAPI BackgroundTasks runs after response is sent, is compatible with the existing sync/async pattern, and is not lost on event loop teardown.

Build `initial_state` from `make_default_state(...)` overriding `session_id`, `experiment_id`, `model_id`, `feature_run_id`, `generation`.

Invoke graph:
```python
graph = build_graph()
registry.update_status(experiment_id, ExperimentStatus.RUNNING)
final_state = await asyncio.get_event_loop().run_in_executor(
    None,
    lambda: graph.invoke(initial_state),
)
```

`graph.invoke()` is synchronous — wrap in `run_in_executor` to avoid blocking the event loop.

### 7.5 _write_graph_result

```python
def _write_graph_result(
    experiment_id: str,
    final_state: AgentState,
    registry: ExperimentRegistry,
) -> None:
```

Terminal status determination:
- `discard is True` → `FAILED`
- `comparison_recommendation == "continue"` → `SUCCEEDED`
- `backtest_run_id` present → `ARCHIVED`
- Otherwise → `FAILED`

Write all metrics, taxonomy, strategy_id, backtest_run_id, and `final_state_snapshot=dict(final_state)`.

### 7.6 Router Registration

In `apps/api/main.py`:
```python
from apps.api.routes.research import router as research_router
app.include_router(research_router)
```

---

## 8. New Tool: get_hmm_model

### 8.1 Schemas (add to tools/schemas.py)

```python
class GetHmmModelInput(BaseModel):
    model_id: str

class HmmModelStateStats(BaseModel):
    state_id: int
    label: str | None = None
    mean_return: float | None = None
    mean_adx: float | None = None
    mean_volatility: float | None = None
    frequency_pct: float | None = None

class GetHmmModelOutput(BaseModel):
    id: str
    instrument_id: str
    timeframe: str
    num_states: int
    label_map: dict[str, str]
    state_stats: list[HmmModelStateStats]
    feature_run_id: str | None = None
    created_at: str
```

### 8.2 Executor (add to backend/agents/tools/backtest.py)

```python
async def get_hmm_model(inp: GetHmmModelInput, client: MedallionClient) -> GetHmmModelOutput:
    """Maps to: GET /api/models/hmm/{model_id}"""
    raw = await client.get(f"/api/models/hmm/{inp.model_id}", tool_name="get_hmm_model")
    return GetHmmModelOutput.model_validate(raw)
```

---

## 9. Supervisor Routing Addition for Phase 5B

Add this `elif` branch to `supervisor_node` in `supervisor.py` **before the fallback**, to route to `generation_comparator` after a mutation cycle:

```python
# Multiple generations exist — compare candidates
elif (
    state.get("generation", 0) >= 1
    and state.get("backtest_run_id") is not None
    and state.get("diagnosis_summary") is not None
    and state.get("discard") is not True
    and state.get("comparison_result") is None
):
    next_node = "generation_comparator"
```

Also add `"generation_comparator"` to `_VALID_NEXT_NODES` set if that set is created per the Stage 1 review recommendation.

---

## 10. Implementation Order

Dependencies flow top-to-bottom:

```
Step 1:  backend/agents/tools/schemas.py — add 8 new models
Step 2:  backend/agents/tools/backtest.py — add get_hmm_model
Step 3:  backend/agents/state.py — add 5 new fields
Step 4:  backend/lab/__init__.py — new file
Step 5:  backend/lab/experiment_registry.py — ExperimentStatus, ExperimentRecord, ExperimentRegistry
Step 6:  backend/lab/evaluation.py — ExperimentScore, score_experiment, compare_experiments
Step 7:  backend/lab/mutation.py — perturb_parameters, substitute_rule, inject_regime_filter
Step 8:  backend/deps.py — add get_experiment_registry()
Step 9:  backend/agents/strategy_researcher.py — replace stub
Step 10: backend/agents/backtest_diagnostics.py — replace stub
Step 11: backend/agents/generation_comparator.py — replace stub
Step 12: backend/agents/supervisor.py — add generation_comparator routing condition
Step 13: backend/schemas/requests.py — add ResearchRunRequest, ResearchRunResponse
Step 14: apps/api/routes/research.py — POST /api/research/run + GET routes
Step 15: apps/api/main.py — register research router
Step 16: backend/tests/test_phase5b.py — test suite
```

---

## 11. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | LLM generates invalid rules_engine JSON | MED | Validate before create_strategy; 3 retry limit then task=done |
| 2 | LLM polls job once and assumes done | MED | System prompt instructs repeated polling |
| 3 | Bedrock ThrottlingException mid-loop | MED | Exponential backoff 3 retries; partial work preserved in state |
| 4 | get_hmm_model 404 | LOW | Continue with regime_context={"error": "unavailable"} |
| 5 | BackgroundTask lost on server restart | MED | Experiment stuck RUNNING; manual FAILED override acceptable for local dev |
| 6 | DiagnosticSummary parse fails | LOW | Retry once; hardcoded NO_EDGE default on second failure |
| 7 | generation_comparator routing condition missing | MED | Must apply Section 9 change; without it comparator is unreachable |
| 8 | final_state_snapshot bloat | LOW | Acceptable for local dev; compress in Phase 7 |

---

## 12. Critical Naming Fix

The `BedrockAdapter` method for LLM calls is `converse()`, **not** `invoke()`. All node implementations must use:

```python
result = await adapter.converse(
    messages=messages,
    system_prompt=SYSTEM_PROMPT,
    tools=RESEARCHER_TOOLS,   # or None for diagnostics/comparator
    max_tokens=4096,
    temperature=0.0,
)
```

There is no `invoke()` method. Using it will raise `AttributeError` at runtime.
