# Phase 5C Architecture — Feature Discovery Agent

**Date:** 2026-03-15
**Status:** Implementation Design — Ready for Execution
**Supersedes:** N/A (additive to Phase 5B)
**Prerequisite reading:** `docs/recon/phase5c-entry-report.md`

---

## Summary

Phase 5C adds a `feature_researcher_node` to the LangGraph supervisor graph. The node is driven by Bedrock and proposes novel technical features, executes them in a subprocess sandbox, evaluates their statistical relationship to HMM regime labels via ANOVA F-statistic, and registers survivors to a persistent `FeatureLibrary`. It operates independently of the strategy research loop and is triggered by a new `task = "discover_features"` / `research_mode = "discover_features"` directive.

This document is the authoritative specification. Every class name, field name, function signature, and constant name described here must be followed exactly.

---

## 1. New Files

```
backend/features/
    sandbox.py          NEW — execute_feature_code() subprocess isolator
    evaluate.py         NEW — FeatureEvaluator.evaluate()
    feature_library.py  NEW — FeatureLibrary persistence layer

backend/agents/
    feature_researcher.py    NEW — feature_researcher_node, SYSTEM_PROMPT, RESEARCHER_TOOLS

backend/agents/tools/
    feature.py               NEW — propose_feature, compute_feature,
                                   evaluate_feature, register_feature tool executors
```

Files modified (additive only):
```
backend/agents/tools/schemas.py   EXTEND — add FeatureSpec, FeatureEvalResult, tool I/O models
backend/agents/state.py           EXTEND — add feature_eval_results, research_mode
backend/agents/graph.py           EXTEND — add feature_researcher node + edge
backend/agents/supervisor.py      EXTEND — add routing condition for research_mode == "discover_features"
backend/features/compute.py       EXTEND — append FeatureComputer class
backend/deps.py                   EXTEND — add get_feature_library() lru_cache singleton
```

Files that must NOT be modified:
- `backend/agents/providers/bedrock.py`
- `backend/agents/providers/logging.py`
- `backend/agents/tools/client.py`
- `backend/agents/tools/backtest.py`
- `backend/agents/tools/strategy.py`
- `backend/agents/tools/regime.py`
- All `backend/strategies/`, `backend/backtest/`, `backend/models/` modules
- Existing functions in `backend/features/compute.py` (`compute_features`, `run_feature_pipeline`, `load_feature_matrix`)

---

## 2. Data Models

### 2.1 FeatureSpec (Pydantic v2 — add to `backend/agents/tools/schemas.py`)

```python
ALLOWED_FAMILIES = {"momentum", "breakout", "volatility", "session", "microstructure", "regime_persistence"}

class FeatureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    family: str  # must be one of ALLOWED_FAMILIES
    formula_description: str
    lookback_bars: int
    dependency_columns: list[str]
    transformation: str
    expected_intuition: str
    leakage_risk: str  # "none" | "low" | "medium" | "high"
    code: str          # Python code block; must assign pd.Series to 'result'
```

### 2.2 FeatureEvalResult (Pydantic v2 — add to `backend/agents/tools/schemas.py`)

```python
class FeatureEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_name: str
    f_statistic: float
    regime_breakdown: dict[str, float]  # regime_label -> mean feature value
    leakage_risk: str
    registered: bool
```

### 2.3 Tool I/O schemas (add to `backend/agents/tools/schemas.py`)

```python
class ProposeFeatureInput(BaseModel):
    spec: dict[str, Any]  # raw FeatureSpec dict from LLM

class ProposeFeatureOutput(BaseModel):
    valid: bool
    errors: list[str]
    spec: dict[str, Any] | None  # validated FeatureSpec dict if valid=True

class ComputeFeatureInput(BaseModel):
    feature_name: str
    code: str
    instrument: str
    timeframe: str
    start: str   # ISO date
    end: str     # ISO date

class ComputeFeatureOutput(BaseModel):
    feature_name: str
    success: bool
    series_length: int
    sample_values: list[float]
    error: str | None

class EvaluateFeatureInput(BaseModel):
    feature_name: str
    instrument: str
    timeframe: str
    start: str
    end: str
    model_id: str

class EvaluateFeatureOutput(BaseModel):
    feature_name: str
    f_statistic: float
    regime_breakdown: dict[str, float]
    leakage_risk: str
    passes_threshold: bool  # f_statistic > REGISTRATION_THRESHOLD and leakage_risk != "high"

class RegisterFeatureInput(BaseModel):
    feature_name: str

class RegisterFeatureOutput(BaseModel):
    feature_name: str
    registered: bool
    reason: str  # "registered" | "below_threshold" | "leakage_blocked" | "already_exists"
```

---

## 3. AgentState Extensions

Add to `AgentState` TypedDict in `backend/agents/state.py`:

```python
# ── Feature discovery artifacts ──────────────────────────────────────────────
feature_eval_results: list[dict[str, Any]] | None
research_mode: str | None  # None | "discover_features"
```

Update `DEFAULT_STATE` and `make_default_state()`:
- `feature_eval_results`: `None`
- `research_mode`: `None`

---

## 4. `backend/features/sandbox.py`

### Custom exceptions

```python
class SandboxError(Exception):
    """Base class for all feature sandbox errors."""

class SandboxTimeoutError(SandboxError):
    """Raised when child process exceeds timeout_seconds."""

class SandboxValidationError(SandboxError):
    """Raised when code uses a non-whitelisted import or produces wrong output."""
```

### Import whitelist pre-check

```python
import re
_IMPORT_RE = re.compile(r'^\s*(?:import|from)\s+(\w+)', re.MULTILINE)
_ALLOWED_IMPORTS = {"numpy", "pandas", "np", "pd"}

def _check_imports(code: str) -> None:
    for m in _IMPORT_RE.finditer(code):
        module = m.group(1)
        if module not in _ALLOWED_IMPORTS:
            raise SandboxValidationError(f"Forbidden import: {module}")
```

### Child worker (runs inside child process)

```python
def _child_worker(code: str, df_bytes: bytes, conn):
    import pickle
    import pandas as pd
    import numpy as np
    try:
        df = pickle.loads(df_bytes)
        local_ns = {"pd": pd, "np": np, "df": df}
        exec(code, local_ns)
        result = local_ns.get("result")
        if not isinstance(result, pd.Series):
            conn.send({"error": "code must assign a pd.Series to 'result'"})
        else:
            conn.send({"series": pickle.dumps(result)})
    except Exception as exc:
        conn.send({"error": str(exc)})
    finally:
        conn.close()
```

### Main function signature

```python
def execute_feature_code(
    code: str,
    df: pd.DataFrame,
    timeout_seconds: float = 5.0,
) -> pd.Series:
```

**Spawn semantics:** Use `multiprocessing.Process(target=_child_worker, ...)`. Join with `timeout_seconds`. If still alive after join, call `process.terminate()` and raise `SandboxTimeoutError`.

### Code contract for LLM

The LLM writes code that receives `df` (DataFrame with columns `open`, `high`, `low`, `close`, `volume` and `DatetimeIndex`) and must assign a `pd.Series` named `result`. Example:
```python
result = (df["close"] - df["close"].rolling(20).mean()) / df["close"].rolling(20).std()
```

---

## 5. `backend/features/evaluate.py`

```python
from scipy.stats import f_oneway

class FeatureEvaluator:
    def evaluate(
        self,
        series: pd.Series,
        regime_labels: list[dict],  # [{"timestamp_utc": str, "label": str}, ...]
    ) -> FeatureEvalResult:
```

**Implementation:**
1. Join `series` (DatetimeIndex) to `regime_labels` on `timestamp_utc` via `pd.merge`.
2. Group by `label` column.
3. Build `regime_breakdown`: mean value per label group.
4. Collect groups with `len(group.dropna()) >= 2`.
5. If fewer than 2 valid groups: return `FeatureEvalResult(f_statistic=0.0, ...)` with warning.
6. Call `f_oneway(*[group.dropna().values for group in valid_groups])`.
7. Convert `nan` F-statistic to `0.0`.
8. `registered` is always `False` — FeatureLibrary decides registration.

---

## 6. `backend/features/feature_library.py`

### Module-level constant

```python
REGISTRATION_THRESHOLD = 2.0  # minimum F-statistic for registration (strict >)
```

### FeatureLibrary class

```python
class FeatureLibrary:
    _STORE = "features"

    def __init__(self, metadata_repo: Any) -> None:
        self._repo = metadata_repo

    def register(self, spec: FeatureSpec, eval_result: FeatureEvalResult) -> FeatureEvalResult:
        """Register if leakage_risk != "high" AND f_statistic > REGISTRATION_THRESHOLD.
        Returns updated FeatureEvalResult with registered=True/False."""

    def get(self, feature_name: str) -> dict | None:

    def list_all(self) -> list[dict]:
        """All registered features sorted by f_statistic descending."""

    def query(
        self,
        family: str | None = None,
        min_f_statistic: float | None = None,
        leakage_risk: str | None = None,
    ) -> list[dict]:
```

**Registration rules (hard):**
- `leakage_risk == "high"` → blocked unconditionally (F-statistic irrelevant)
- `f_statistic <= REGISTRATION_THRESHOLD` → blocked (strict `>`, not `>=`)
- Already exists by name → blocked with `reason="already_exists"`

**Stored record shape:**
```python
{
    "feature_name": str,
    "family": str,
    "formula_description": str,
    "lookback_bars": int,
    "dependency_columns": list[str],
    "transformation": str,
    "expected_intuition": str,
    "leakage_risk": str,
    "code": str,
    "f_statistic": float,
    "regime_breakdown": dict[str, float],
    "registered": bool,
    "registered_at": str,   # ISO datetime
    "session_id": str,
}
```

**Storage:** JSON via `LocalMetadataRepository`. Use `feature_name` as the record key (not `id`). Records stored as `{feature_name: record_dict}`.

---

## 7. `backend/features/compute.py` Extension (additive only)

Append `FeatureComputer` class to the existing file. Do NOT modify any existing functions.

```python
class FeatureComputer:
    """Execute a single FeatureSpec against live bar data via subprocess sandbox."""

    def __init__(self, market_repo: MarketDataRepository) -> None:
        self._market_repo = market_repo

    def compute(
        self,
        spec: "FeatureSpec",
        instrument_id: str,
        timeframe: "Timeframe",
        start: datetime,
        end: datetime,
    ) -> pd.Series:
        """Load bars from DuckDB and run spec.code in subprocess sandbox.

        Raises
        ------
        SandboxError, SandboxTimeoutError, SandboxValidationError
        ValueError: if no bars available for requested range
        """
        raw = self._market_repo.get_bars_agg(instrument_id, timeframe, start, end)
        if not raw:
            raise ValueError(f"No bars for {instrument_id} [{start}, {end})")
        df = pd.DataFrame(raw)
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.set_index("timestamp_utc").sort_index()
        from backend.features.sandbox import execute_feature_code
        return execute_feature_code(spec.code, df)
```

---

## 8. `backend/agents/tools/feature.py`

Four async tool executor functions following the exact signature pattern of `backend/agents/tools/strategy.py`:

```python
async def propose_feature(inp: ProposeFeatureInput, client: MedallionClient) -> ProposeFeatureOutput
async def compute_feature(inp: ComputeFeatureInput, client: MedallionClient) -> ComputeFeatureOutput
async def evaluate_feature(inp: EvaluateFeatureInput, client: MedallionClient) -> EvaluateFeatureOutput
async def register_feature(inp: RegisterFeatureInput, client: MedallionClient) -> RegisterFeatureOutput
```

**Module-level caches (process-scoped):**
```python
_FEATURE_SERIES_CACHE: dict[str, pd.Series] = {}
_FEATURE_SPEC_CACHE: dict[str, FeatureSpec] = {}
_FEATURE_EVAL_CACHE: dict[str, FeatureEvalResult] = {}
```

- `propose_feature` — validates `inp.spec` against `FeatureSpec` Pydantic model and checks `family in ALLOWED_FAMILIES`. Pure validation, no I/O. Populates `_FEATURE_SPEC_CACHE` on success.
- `compute_feature` — instantiates `FeatureComputer(get_market_repo())`, runs compute, stores result in `_FEATURE_SERIES_CACHE[feature_name]`.
- `evaluate_feature` — retrieves Series from cache, loads regime labels via market repo, runs `FeatureEvaluator().evaluate()`. Stores result in `_FEATURE_EVAL_CACHE[feature_name]`.
- `register_feature` — reads spec and eval result from caches, calls `get_feature_library().register()`.

---

## 9. `backend/agents/feature_researcher.py`

### Module-level constants

```python
NODE_NAME = "feature_researcher"
MAX_FEATURES_PER_SESSION = 5
```

### SYSTEM_PROMPT (module-level string constant)

Key sections:
- Role: quantitative feature researcher for Forex platform
- Allowed families: momentum, breakout, volatility, session, microstructure, regime_persistence
- Leakage rules with self-assessment guide
- Code contract: `df` with columns `open, high, low, close, volume`, DatetimeIndex, must assign `pd.Series` to `result`, numpy and pandas only
- Workflow: propose → compute → evaluate → register, up to MAX_FEATURES_PER_SESSION iterations
- Session features: 5 buckets — 0=Asia(00-07), 1=London(08-12), 2=Overlap(13-16), 3=NY(17-20), 4=Off-hours(21-23)
- Final response: JSON summary `{features_proposed, features_registered, results: [FeatureEvalResult, ...]}`

### RESEARCHER_TOOLS

```python
RESEARCHER_TOOLS: list[dict[str, Any]] = [
    {"toolSpec": {"name": "propose_feature", "description": "...", "inputSchema": {"json": ProposeFeatureInput.model_json_schema()}}},
    {"toolSpec": {"name": "compute_feature", "description": "...", "inputSchema": {"json": ComputeFeatureInput.model_json_schema()}}},
    {"toolSpec": {"name": "evaluate_feature", "description": "...", "inputSchema": {"json": EvaluateFeatureInput.model_json_schema()}}},
    {"toolSpec": {"name": "register_feature", "description": "...", "inputSchema": {"json": RegisterFeatureInput.model_json_schema()}}},
]
```

### Tool dispatcher

```python
_TOOL_DISPATCH: dict[str, tuple[Any, Any]] = {
    "propose_feature":  (propose_feature,  ProposeFeatureInput),
    "compute_feature":  (compute_feature,  ComputeFeatureInput),
    "evaluate_feature": (evaluate_feature, EvaluateFeatureInput),
    "register_feature": (register_feature, RegisterFeatureInput),
}
```

### _build_user_message(state) -> str

Constructs prompt from state:
```
TASK: discover_features
INSTRUMENT: {instrument}
TIMEFRAME: {timeframe}
PERIOD: {test_start} to {test_end}
HMM_MODEL: {model_id}
MAX_FEATURES: {MAX_FEATURES_PER_SESSION}
REGIME_CONTEXT: {regime_context as JSON or "not available"}
EXISTING_FEATURES: {comma-separated names from feature_library.list_all() or "none"}
```

### _run_feature_researcher(state) -> dict

Follows multi-turn tool-call loop pattern from `strategy_researcher.py`:
1. Instantiate `AgentLogger(session_id=state.get("session_id", ""))`, get `trace_id`
2. Call `_logger.node_enter(NODE_NAME, trace_id, list(state.keys()))`
3. Build `messages` from `_build_user_message(state)`
4. Multi-turn loop with ThrottlingException backoff (same pattern as strategy_researcher)
5. `adapter.converse()` only — never `adapter.invoke()`
6. Dispatch tools via `_TOOL_DISPATCH` with same `_dispatch_tool` helper
7. Loop until `stop_reason == "end_turn"` or tool call limit
8. Parse final JSON summary from `end_turn` response
9. Collect `FeatureEvalResult` objects from `_FEATURE_EVAL_CACHE` for this session

**Return dict:**
```python
{
    "next_node": "supervisor",
    "feature_eval_results": [r.model_dump(mode="json") for r in session_eval_results],
    "research_mode": None,  # clear trigger
    "task": "done",
    "errors": prior_errors,
}
```

### feature_researcher_node(state) -> dict

```python
def feature_researcher_node(state: AgentState) -> dict[str, Any]:
    return asyncio.run(_run_feature_researcher(state))
```

---

## 10. Supervisor Routing Extension

Add **one** new condition to `supervisor_node` in `backend/agents/supervisor.py`.

Insert at **priority position 2** — after the hard-stop block, before `task == "generate_seed"`:

```python
elif state.get("research_mode") == "discover_features":
    next_node = "feature_researcher"
```

### Full updated routing table (after extension)

| Priority | Condition | next_node |
|---|---|---|
| 1 | `task == "done"` OR `iteration >= 10` | END |
| 2 | `research_mode == "discover_features"` | feature_researcher |
| 3 | `task == "generate_seed"` | strategy_researcher |
| 4 | `backtest_run_id` set AND `diagnosis_summary` is None | backtest_diagnostics |
| 5 | `diagnosis_summary` set AND `discard == True` | END |
| 6 | `generation >= 1` AND `backtest_run_id` set AND `diagnosis_summary` set AND `discard != True` AND `comparison_result` is None | generation_comparator |
| 7 | `diagnosis_summary` set AND `discard == False` | strategy_researcher |
| 8 | `task == "mutate"` AND `backtest_run_id` set | strategy_researcher |
| 9 | fallback | strategy_researcher |

No existing routing logic is removed or reordered. The new condition is a pure insertion.

---

## 11. Graph Extension (`backend/agents/graph.py`)

Add to `build_graph()` (additive only):

```python
from backend.agents.feature_researcher import feature_researcher_node

graph.add_node("feature_researcher", feature_researcher_node)

# Update the conditional edges path map to include "feature_researcher"
graph.add_conditional_edges(
    "supervisor",
    route_next,
    {
        "strategy_researcher": "strategy_researcher",
        "backtest_diagnostics": "backtest_diagnostics",
        "generation_comparator": "generation_comparator",
        "feature_researcher": "feature_researcher",   # NEW
        "END": END,
    },
)

graph.add_edge("feature_researcher", "supervisor")
```

---

## 12. `backend/deps.py` Extension

Add after `get_experiment_registry()`:

```python
@lru_cache(maxsize=1)
def get_feature_library():
    from backend.features.feature_library import FeatureLibrary
    return FeatureLibrary(get_metadata_repo())
```

---

## 13. Registration Threshold

```
REGISTRATION_THRESHOLD = 2.0
```

Defined as module-level constant in `backend/features/feature_library.py`.

Feature registers if and only if **both**:
1. `eval_result.f_statistic > REGISTRATION_THRESHOLD` (strict `>`, not `>=`)
2. `eval_result.leakage_risk != "high"`

---

## 14. Error Handling

| Error source | Behavior |
|---|---|
| `ThrottlingException` on first LLM call | Retry with backoff; after exhausting: set `task="done"`, append to `errors` |
| `SandboxTimeoutError` | `ComputeFeatureOutput(success=False, error="timeout")` — LLM may retry |
| `SandboxValidationError` (forbidden import) | `ComputeFeatureOutput(success=False, error="forbidden import: {module}")` |
| `SandboxError` (other) | `ComputeFeatureOutput(success=False, error=str(exc))` |
| `ValueError` (no bars) | `ComputeFeatureOutput(success=False, error="no bars available")` |
| Family validation failure | `ProposeFeatureOutput(valid=False, errors=["family must be one of: ..."])` |
| JSON parse failure on end_turn | Append to `state.errors`, return collected eval results |

---

## 15. Logging Contract

Every `adapter.converse()` call logs via `_logger.llm_call()`. Additionally emit a supplemental `feature_llm_call` event with:
```python
{
    "event": "feature_llm_call",
    "node": NODE_NAME,
    "trace_id": trace_id,
    "session_id": state.get("session_id", ""),
    "model": adapter._model_id,
    "input_tokens": result.input_tokens,
    "output_tokens": result.output_tokens,
    "latency_ms": llm_latency_ms,
    "feature_name": current_feature_name,   # None until propose_feature succeeds
    "family": current_feature_family,        # None until propose_feature succeeds
    "f_statistic": current_f_statistic,      # None until evaluate_feature completes
}
```

Tool call logging uses `_logger.tool_call(...)` output summaries:
- `propose_feature`: `f"valid={output.valid}, errors={output.errors[:2]}"`
- `compute_feature`: `f"success={output.success}, series_length={output.series_length}"`
- `evaluate_feature`: `f"f_statistic={output.f_statistic:.3f}, passes={output.passes_threshold}"`
- `register_feature`: `f"registered={output.registered}, reason={output.reason}"`

---

## 16. Test Strategy

| Test file | Coverage target |
|---|---|
| `backend/tests/test_feature_sandbox.py` | valid code, forbidden import, timeout, wrong result type |
| `backend/tests/test_feature_evaluate.py` | ANOVA F-test, edge cases: 1 class → F=0.0, all NaN |
| `backend/tests/test_feature_library.py` | register/get/list/query, threshold blocking, leakage blocking, duplicate |
| `backend/tests/test_feature_researcher.py` | full mocked lifecycle, family rejection, leakage block, MAX_FEATURES_PER_SESSION loop, throttle retry |
| `backend/tests/test_supervisor_phase5c.py` | research_mode routing, research_mode clears after node |

Target: ≥90% coverage on all Phase 5C files. Total suite: ≥410 tests (318 existing + ≥92 new).

---

## 17. Complete File Inventory

```
NEW:
    backend/agents/feature_researcher.py
    backend/agents/tools/feature.py
    backend/features/sandbox.py
    backend/features/evaluate.py
    backend/features/feature_library.py
    apps/api/routes/features.py

MODIFIED (additive only):
    backend/agents/tools/schemas.py   — FeatureSpec, FeatureEvalResult, 4 tool I/O pairs
    backend/agents/state.py           — feature_eval_results + research_mode fields
    backend/agents/graph.py           — 1 new node + updated path map + 1 new edge
    backend/agents/supervisor.py      — 1 new routing condition at priority 2
    backend/features/compute.py       — FeatureComputer class appended
    backend/deps.py                   — get_feature_library() singleton
    apps/api/main.py                  — register features router
```
