# Phase 5B Entry Recon Report

**Generated:** 2026-03-15
**Agent:** Repo Reconciliation Agent — Phase 5B Stage 1 Entry Scan
**Scope:** `backend/agents/`, `backend/agents/tools/`, `backend/agents/providers/`, `backend/tests/test_agents_foundation.py`
**Purpose:** Verify all Stage 1 entry checklist items before Stage 2 LLM integration begins.

---

## CRITICAL BLOCKER

### Memory/Handoff Names `BedrockAdapter.invoke()` — That Method Does Not Exist

The actual public interface of `BedrockAdapter` in `backend/agents/providers/bedrock.py` exposes exactly two public LLM-calling methods:

- `async def converse(...)` — non-streaming, returns `ConverseResult`
- `async def converse_stream(...)` — streaming, yields `str` deltas

There is no `invoke()` method anywhere in `BedrockAdapter`. There is no `__call__` override.

**Impact on Stage 2:** Any agent or node that calls `adapter.invoke()` will raise `AttributeError` at runtime. All Stage 2 references must use `await adapter.converse(messages, system_prompt, tools, max_tokens, temperature)`.

---

## 1. Stub Interface Signatures

### strategy_researcher.py

```python
def strategy_researcher_node(state: AgentState) -> dict[str, Any]
```
- Stage 1 behavior: appends stub notice to `errors`, sets `next_node` to `"supervisor"`
- Return shape: `{"next_node": "supervisor", "errors": list[str]}`

### backtest_diagnostics.py

```python
def backtest_diagnostics_node(state: AgentState) -> dict[str, Any]
```
- Stage 1 behavior: sets `diagnosis_summary` to `"stub"`, sets `discard` to `False`, routes back to supervisor
- Return shape: `{"next_node": "supervisor", "diagnosis_summary": "stub", "discard": False, "errors": list[str]}`

### generation_comparator.py

```python
def generation_comparator_node(state: AgentState) -> dict[str, Any]
```
- Stage 1 behavior: sets `comparison_recommendation` to `"stub"`, routes back to supervisor
- Return shape: `{"next_node": "supervisor", "comparison_recommendation": "stub", "errors": list[str]}`

---

## 2. BedrockAdapter Interface — Full Signature

File: `backend/agents/providers/bedrock.py`

### Class: `BedrockAdapter`

```python
def __init__(
    self,
    model_id: str | None = None,
    region: str | None = None,
) -> None
```
- `model_id` defaults to `settings.bedrock_model_id` (`"anthropic.claude-3-5-sonnet-20241022-v2:0"`)
- `region` defaults to `settings.bedrock_region` (`"us-east-1"`)
- boto3 client is lazily instantiated on first use (safe for tests that mock boto3)

### Primary LLM call method:
```python
async def converse(
    self,
    messages: list[dict[str, Any]],
    system_prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> ConverseResult
```
- Runs boto3 `client.converse()` in a thread pool executor to avoid blocking the event loop
- Emits structured JSON log event to stderr after each call

### Streaming method:
```python
async def converse_stream(
    self,
    messages: list[dict[str, Any]],
    system_prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]
```
- No `max_tokens` or `temperature` parameters

### Static utility:
```python
@staticmethod
def extract_tool_use(result: ConverseResult) -> tuple[str, dict[str, Any]] | None
```

### Dataclass: `ConverseResult`
```python
@dataclass
class ConverseResult:
    content: str
    tool_use: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    stop_reason: str
```

---

## 3. schemas.py Models — All 22 Models and Their Fields

File: `backend/agents/tools/schemas.py`

### Tool 1: list_strategies
- **`ListStrategiesInput`** — no fields
- **`StrategyRecord`**: `id`, `name`, `description`, `strategy_type: StrategyType`, `definition_json: dict`, `version: int`, `created_at: datetime`, `active_flag: bool`, `tags: list[str]`

### Tool 2: create_strategy
- **`CreateStrategyInput`**: `name: str` (req), `description: str = ""`, `strategy_type: StrategyType` (req), `definition_json: dict = {}`, `tags: list[str] = []`
- Output uses `StrategyRecord`

### Tool 3: validate_strategy
- **`ValidateStrategyInput`**: `strategy_id: str`, `definition_json: dict`, `strategy_type: StrategyType`
- **`ValidateStrategyOutput`**: `valid: bool`, `errors: list[str]`

### Tool 4: submit_backtest
- **`SubmitBacktestInput`**: `strategy_id: str | None = None`, `inline_strategy: dict | None = None`, `instrument: str`, `timeframe: Timeframe`, `test_start: datetime`, `test_end: datetime`, `spread_pips: float = 2.0`, `commission_per_unit: float = 0.0`, `slippage_pips: float = 0.5`, `pip_size: float = 0.0001`, `feature_run_id: str | None = None`, `model_id: str | None = None` — model_validator raises if both strategy_id and inline_strategy are None
- **`SubmitBacktestOutput`**: `job_id: str`, `status: JobStatus`

### Tool 5: poll_job
- **`PollJobInput`**: `job_id: str`
- **`PollJobOutput`**: `id`, `job_type`, `status: JobStatus`, `progress_pct`, `stage_label`, `requested_by`, `created_at`, `started_at?`, `completed_at?`, `error_code?`, `error_message?`, `params_json`, `result_ref?`, `logs_ref?`

### Tool 6: get_backtest_run
- **`GetBacktestRunInput`**: `run_id: str`
- **`BacktestRunDetail`**: `id`, `job_id?`, `strategy_id?`, `inline_definition?`, `instrument_id`, `timeframe`, `test_start`, `test_end`, `parameters_json`, `cost_model_json`, `status`, `created_at`, `result_ref?`, `oracle_regime_labels: bool`
- **`PerformanceMetric`**: `id`, `backtest_run_id`, `metric_name`, `metric_value: float | None`, `segment_type`, `segment_key`
- **`GetBacktestRunOutput`**: `run: BacktestRunDetail`, `metrics: list[PerformanceMetric]`

### Tool 7: get_backtest_trades
- **`GetBacktestTradesInput`**: `run_id: str`
- **`TradeRecord`**: `id`, `backtest_run_id`, `instrument_id`, `entry_time`, `exit_time?`, `side: TradeSide`, `quantity`, `entry_price`, `exit_price?`, `stop_price?`, `target_price?`, `pnl`, `pnl_pct`, `holding_period`, `entry_reason`, `exit_reason`, `regime_at_entry`, `regime_at_exit`
- **`GetBacktestTradesOutput`**: `run_id`, `trades: list[TradeRecord]`, `count: int`

### Tool 8: get_equity_curve
- **`GetEquityCurveInput`**: `run_id: str`
- **`EquityPoint`**: `timestamp: str`, `equity: float`, `drawdown: float`
- **`GetEquityCurveOutput`**: `run_id`, `equity_curve: list[EquityPoint]`

### Tool 9: list_backtest_runs
- **`ListBacktestRunsInput`**: `limit: int = 20`
- **`BacktestRunSummary`**: `id`, `job_id?`, `strategy_id?`, `instrument_id`, `timeframe`, `test_start`, `test_end`, `status`, `created_at`
- **`ListBacktestRunsOutput`**: `runs: list[BacktestRunSummary]`, `count: int`

Total: 22 Pydantic models across 9 tool pairs.

---

## 4. Missing Directories/Files (Expected Absent)

- `backend/lab/`: **ABSENT** — entire directory absent; no `experiment_registry.py`, `evaluation.py`, or `mutation.py`
- `apps/api/routes/experiments.py`: **ABSENT** — confirmed
- `apps/api/routes/research.py`: **ABSENT** — confirmed
- `backend/agents/tools/experiment.py`: **ABSENT** — no experiment tool schemas in schemas.py either

---

## 5. Test Count

| File | Test functions |
|---|---|
| `test_phase0_foundation.py` | 12 |
| `test_normalize.py` | 20 |
| `test_aggregate.py` | 22 |
| `test_hmm.py` | 15 |
| `test_features.py` | 16 |
| `test_rules_engine.py` | 33 |
| `test_strategy.py` | 37 |
| `test_backtest.py` | 55 |
| `test_backtest_integration.py` | 10 |
| `test_agents_foundation.py` | 30 |
| **TOTAL** | **250** |

---

## 6. Deferred Warnings and TODOs in Phase 5B Files

No `TODO`, `FIXME`, or `NOTE` markers in any `backend/agents/` source files. All deferred warnings are in `docs/reviews/phase5-stage1-review.md`:

1. **Supervisor iteration counter** — increments on every invocation regardless of whether useful work occurred; may exhaust max_iterations on repeated worker failures
2. **Duplicate state initialization** — `DEFAULT_STATE()` and `make_default_state()` maintain parallel field lists
3. **BedrockAdapter logs to stderr instead of AgentLogger** — mixed log formats will cause aggregation issues in Phase 7
4. **Tool executors do not re-validate LLM input** — raw dict from Bedrock tool-use block must be validated before passing to executor
5. **`route_next()` does not validate `next_node` against allowed set** — invalid string could cause LangGraph to hang

---

## 7. Conflicts and Blockers

### BLOCKER 1 — Method name mismatch: `invoke()` vs `converse()`
Any reference to `BedrockAdapter.invoke()` is wrong. The method does not exist. Use `await adapter.converse(messages, system_prompt, tools, max_tokens, temperature)`.

### BLOCKER 2 — `backend/lab/` does not exist
The entire experiment registry, evaluation, and mutation layer is absent. Stage 2 must build it from scratch.

### BLOCKER 3 — No experiment tool schemas in `tools/schemas.py`
`CreateExperimentInput`, `UpdateExperimentInput`, `ExperimentRecord` schemas do not exist anywhere.

### BLOCKER 4 — `apps/api/routes/experiments.py` and `research.py` absent
No HTTP surface for experiment CRUD or supervisor triggering.

### Non-blocking risks
- Supervisor never routes to `generation_comparator` under any current condition — Stage 2 must add routing condition
- `converse_stream()` lacks `max_tokens`/`temperature` parameters
- `_parse_converse_response()` silently produces empty `ConverseResult` on malformed Bedrock response

---

## 8. Verified Truths

- All 5 entry checklist files exist and follow correct LangGraph contracts (accept `AgentState`, return `dict[str, Any]`)
- Test count of 250 confirmed by direct file inspection
- `backend/lab/`, `experiments.py`, and `research.py` correctly absent
- `build_graph()` compiles `StateGraph` with 4 nodes: `supervisor`, `strategy_researcher`, `backtest_diagnostics`, `generation_comparator`
- `settings.bedrock_model_id` = `"anthropic.claude-3-5-sonnet-20241022-v2:0"`, `settings.bedrock_region` = `"us-east-1"`, `settings.api_base_url` = `"http://localhost:8000"`
- `BedrockAdapter` uses lazy boto3 client initialization (safe for mocking)
- 22 Pydantic models confirmed in `tools/schemas.py`

---

*All claims grounded in direct file reads. No claims inferred from intent, comments, or roadmap language.*
