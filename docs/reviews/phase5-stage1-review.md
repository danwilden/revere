# Phase 5 Stage 1 Code Review
## Medallion Forex Platform — Agentic Research Layer Foundation

**Review Date:** 2026-03-15
**Scope:** Agent orchestration, tools, providers, state, and API integration
**Reviewer:** Claude (Code Maintainability Reviewer)

---

## Executive Summary

**Overall Assessment:** The Phase 5 Stage 1 foundation is well-structured, follows the design spec closely, and is ready for Stage 2 LLM integration. The code is clean, modular, and maintainable. Most findings are **polish suggestions** rather than blockers.

**Health:** GOOD. Files are appropriately sized, responsibilities are clear, naming is consistent, and docstrings are present. No critical runtime risks identified.

---

## BLOCKERS
**None identified.** All code is ready to merge.

---

## WARNINGS
### 1. Supervisor iteration increment happens regardless of error state

**File:** `backend/agents/supervisor.py`, lines 78–81
**Issue:** The supervisor increments `iteration` on every invocation, even if an error occurs. If a worker node fails internally (not caught by LangGraph), the iteration counter still advances, potentially masking failure loops.

**Why it matters:** In Stage 2, if the StrategyResearcher node raises an exception internally (e.g., Bedrock timeout), the graph may retry the same branch repeatedly, incrementing iteration each time. The counter becomes a false positive for "loop progress."

**Recommendation:** Document that `iteration` is "invocation count" not "unique work count." Or, if Stage 2 error handling re-enters the supervisor on exception, consider moving iteration increment to supervisor exit points only. For now, the current behavior is acceptable since Stage 1 nodes don't error; add a comment.

**Suggested fix:**
```python
# Line ~79: Add clarifying comment
return {
    "next_node": next_node,
    "iteration": iteration + 1,  # Increments on every supervisor invocation, whether productive or not
}
```

---

### 2. Duplicate state field initialization in DEFAULT_STATE and make_default_state

**File:** `backend/agents/state.py`, lines 65–179
**Issue:** `DEFAULT_STATE()` and `make_default_state()` have nearly identical field-initialization code. If a new field is added to `AgentState`, both functions must be updated, creating a maintainability risk.

**Why it matters:** Copy-paste in state initialization is a common source of bugs as AgentState evolves. Stage 2 will add new experiment fields (`experiment_id`, etc.); both functions must stay synchronized.

**Recommendation:** Refactor to a single parameterized helper. Options:
1. Make `DEFAULT_STATE` accept optional params that override defaults (simplest).
2. Create a `_init_agent_state()` helper that both call.

**Suggested approach:**
```python
def DEFAULT_STATE(
    session_id: str,
    instrument: str = "EUR_USD",
    timeframe: str = "H4",
    test_start: str = "2024-01-01",
    test_end: str = "2024-06-01",
    **kwargs,
) -> AgentState:
    """Return a complete initial AgentState. Override defaults via kwargs."""
    return AgentState(
        session_id=session_id,
        trace_id=str(uuid.uuid4()),
        requested_by=kwargs.get("requested_by", "system"),
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        instrument=instrument,
        timeframe=timeframe,
        test_start=test_start,
        test_end=test_end,
        # ... rest of fields
    )

# Then make_default_state call DEFAULT_STATE
def make_default_state(...) -> AgentState:
    return DEFAULT_STATE(session_id=str(uuid.uuid4()), ...)
```

---

### 3. Bedrock stub logging uses sys.stderr directly instead of AgentLogger

**File:** `backend/agents/providers/bedrock.py`, lines 122–133
**Issue:** The BedrockAdapter logs LLM calls directly to stderr via `json.dumps()` and `print(..., file=sys.stderr)` instead of using the centralized `AgentLogger` from `providers/logging.py`.

**Why it matters:** When both Bedrock and AgentLogger emit JSON to stderr, the format is inconsistent (BedrockAdapter uses inline `json.dumps()`; AgentLogger uses loguru). Log aggregation and parsing in Phase 7 will be harder if events have mixed formats.

**Recommendation:** Refactor BedrockAdapter to accept an optional `AgentLogger` instance, or make it a dependency. For Stage 1, add a comment explaining the split. In Stage 2, consolidate logging.

**Suggested fix:**
```python
# At top of BedrockAdapter.converse()
# NOTE: LLM call events are logged to stderr here. In Stage 2, consolidate
# with AgentLogger for consistent structured logging format.
```

Or, better:
```python
class BedrockAdapter:
    def __init__(
        self,
        model_id: str | None = None,
        region: str | None = None,
        logger: AgentLogger | None = None,
    ) -> None:
        self._model_id = model_id or settings.bedrock_model_id
        self._region = region or settings.bedrock_region
        self._logger = logger or AgentLogger()
        self._client: Any = None
```

---

### 4. Tool executors do not validate tool input against schema before execution

**File:** `backend/agents/tools/backtest.py`, `strategy.py`
**Issue:** Tool executor functions (e.g., `submit_backtest()`, `create_strategy()`) receive Pydantic input models but do not explicitly call `model_validate()`. They assume the LLM-provided JSON was already deserialized by the graph.

**Why it matters:** In Stage 2, when the Bedrock LLM calls `submit_backtest` via tool use, the tool input JSON may be malformed. Without explicit validation in the executor, a Pydantic validation error will propagate as a generic exception instead of being caught and returned as a tool error.

**Recommendation:** Each tool executor should wrap validation and catch ValidationError, returning a structured error response. Add a retry loop in the calling node.

**Suggested fix:**
```python
# In backend/agents/tools/backtest.py, add a validator wrapper:
def _validate_tool_input(model_class, raw_input):
    """Validate tool input; raise ToolCallError with context if invalid."""
    try:
        return model_class.model_validate(raw_input)
    except Exception as e:
        raise ToolCallError(
            tool_name=model_class.__name__,
            status_code=400,
            detail=f"Invalid tool input: {str(e)}",
        )

async def submit_backtest(
    inp: SubmitBacktestInput,
    client: MedallionClient,
) -> SubmitBacktestOutput:
    """..."""
    # inp is already validated by LangGraph; no need to re-validate here.
    # This is OK for Stage 1. In Stage 2, consider explicit re-validation.
    body = inp.model_dump(mode="json")
    raw = await client.post("/api/backtests/jobs", body=body, tool_name="submit_backtest")
    return SubmitBacktestOutput.model_validate(raw)
```

For now, leave as-is with a comment. The graph framework will validate before calling the executor.

---

### 5. Conditional edge "END" string must be mapped to langgraph.graph.END in build_graph

**File:** `backend/agents/graph.py`, lines 43–52
**Issue:** The path map correctly maps `"END"` to `langgraph.graph.END`, which is good. However, there is no runtime check to ensure that nodes don't accidentally return `"END"` as a next_node value. If a node returns `{"next_node": "END"}` before the supervisor has set it, the edge function will return the string "END" instead of the sentinel.

**Why it matters:** LangGraph may treat the string "END" differently than the sentinel object, potentially causing the graph to hang or error.

**Recommendation:** Add a runtime assertion in `route_next()` to validate that `state["next_node"]` is in the allowed set. Or, use an Enum for next_node values.

**Suggested fix:**
```python
# In backend/agents/supervisor.py or route_next()
_VALID_NEXT_NODES = {"supervisor", "strategy_researcher", "backtest_diagnostics", "generation_comparator", "END"}

def route_next(state: AgentState) -> str:
    """Conditional edge function: read state["next_node"] and validate."""
    next_node = state.get("next_node", "END")
    if next_node not in _VALID_NEXT_NODES:
        raise ValueError(f"Invalid next_node: {next_node}. Must be one of {_VALID_NEXT_NODES}")
    return next_node
```

For Stage 1, this is low-priority since supervisor is the only source of next_node writes. Add a comment and enforce in Stage 2.

---

## SUGGESTIONS

### 1. AgentLogger session_id should be passed on instantiation (currently unused)

**File:** `backend/agents/providers/logging.py`, lines 40–41
**Issue:** `AgentLogger.__init__()` accepts `session_id` but never uses it. All methods require a separate `trace_id` parameter. The stored `session_id` is always empty in test calls.

**Why it matters:** Inconsistent state initialization suggests the API was designed but not fully implemented. Callers may assume `session_id` is used globally, when it is not.

**Recommendation:** Document the intended usage:
- If `session_id` is global, use it in every method.
- If each log event needs its own `session_id`, accept it in every method (current pattern).

**Suggested fix:**
```python
class AgentLogger:
    """Structured event logger.

    session_id is optional and can be set once or per-call.
    If set in __init__, it is included in all events.
    If passed to a method, it overrides the stored value.
    """
    def __init__(self, session_id: str = "") -> None:
        self._session_id = session_id

    def node_enter(self, node: str, trace_id: str, state_keys: list[str], session_id: str = "") -> None:
        sid = session_id or self._session_id
        _emit({...})
```

For now, clarify that session_id is optional per-call. Or use it as global default if provided.

---

### 2. Tool schemas duplicate some request.py models

**File:** `backend/agents/tools/schemas.py`
**Issue:** `SubmitBacktestInput` is defined in both `tools/schemas.py` (lines 66–84) and `requests.py` (lines 94–125). Same for trade records and metrics. This violates DRY.

**Why it matters:** If an API request schema changes, both files must be updated. Divergence will cause hard-to-debug type mismatches between the agent layer and the API layer.

**Recommendation:** Refactor to a single canonical set of schemas:
- **Option A (preferred):** Keep `tools/schemas.py` light; import from `requests.py` and re-export.
- **Option B:** Move tool-specific schemas to `tools/schemas.py` and have `requests.py` import them (if tools are stable).

**Current:** `tools/schemas.py` is Stage 1 foundational. `requests.py` was already defined in Phase 4. For Stage 1, keep both for isolation; document the split. In Stage 2, consolidate.

**Suggested comment in tools/schemas.py:**
```python
"""Tool input/output Pydantic models.

Some models are duplicated from backend/schemas/requests.py for isolation.
In Stage 2+, consider importing from requests.py to reduce duplication.

Current split:
- requests.py: API request/response contracts (Phases 1–4)
- tools/schemas.py: Agent tool contracts (Phase 5)

Consolidation is deferred to avoid coupling Phase 5 tools to Phase 4 API changes mid-flight.
"""
```

---

### 3. Test file lacks coverage for error paths in tool client

**File:** `backend/tests/test_agents_foundation.py`
**Issue:** Tests cover happy paths (successful API responses, tool extraction) but do not test:
- HTTP 500 errors (ToolCallError propagation).
- Malformed JSON responses (parse failures).
- Network timeouts (httpx timeout behavior).
- Tool input validation errors.

**Why it matters:** Stage 2 will add retries and error recovery logic. Without error-path tests, regressions will slip through.

**Recommendation:** Add tests for:
```python
@pytest.mark.asyncio
async def test_medallion_client_raises_on_500():
    """ToolCallError on server error."""
    ...

@pytest.mark.asyncio
async def test_medallion_client_raises_on_malformed_json():
    """ToolCallError when response is not valid JSON."""
    ...

def test_submit_backtest_input_validation_error():
    """Pydantic validation error when required fields are missing."""
    ...
```

For Stage 1, this is a "nice-to-have." Make a note for Stage 2 testing.

---

### 4. supervisor_node lacks comments on routing logic

**File:** `backend/agents/supervisor.py`, lines 47–73
**Issue:** Each if/elif branch is labeled with a comment, which is good. However, the rationale for the order of checks is not explained. A future reader may wonder why "discard" is checked before "mutate," or why "diagnosis_summary" requires "backtest_run_id" to be set.

**Why it matters:** The routing logic is the heartbeat of the agent loop. Non-obvious conditions (e.g., state combinations that should be impossible) are not flagged.

**Recommendation:** Add a top-level comment explaining the decision tree structure. Example:
```python
def supervisor_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: deterministic routing based on AgentState fields.

    Decision tree:
    1. Terminal conditions: task=done OR iteration >= MAX_ITERATIONS → END
    2. Seed generation: task=generate_seed → researcher (first strategy)
    3. Fresh backtest result: backtest_run_id set AND diagnosis_summary not yet set → diagnostics
    4. Diagnosis received: diagnose what went wrong
       a. discard=True → END (archive path for Stage 1; Stage 2 routes to librarian)
       b. discard=False → researcher (mutation)
    5. Fallback: mutation or ambiguous state → researcher

    Note: Supervisor assumes preconditions are stable. The graph framework and node
    implementations must guarantee that backtest_run_id, diagnosis_summary, discard, etc.
    are only written by the correct nodes and never reverted.
    """
```

---

### 5. AgentState TypedDict is very large (63 fields); consider splitting into nested types

**File:** `backend/agents/state.py`, lines 9–62
**Issue:** `AgentState` has 63 fields covering 11 conceptual domains (session context, experiment scope, LLM content, strategy artifacts, backtest artifacts, diagnosis, comparison, robustness, flow control, errors). While the comments organize them well, this is a large type.

**Why it matters:** Large TypedDicts are harder to reason about. When Stage 2 adds more fields (e.g., `experiment_lineage`, `robustness_battery_results`), the type will grow further. Serialization and merging become less obvious.

**Recommendation:** For Stage 1, keep as-is (it's well-organized with section comments). In Stage 2, consider nested TypedDicts:
```python
class SessionContext(TypedDict):
    session_id: str
    trace_id: str
    requested_by: str
    created_at: str

class ExperimentScope(TypedDict):
    instrument: str
    timeframe: str
    test_start: str
    test_end: str
    ...

class AgentState(TypedDict, total=False):
    session: SessionContext
    scope: ExperimentScope
    ...
```

This would make merging in LangGraph more explicit. Defer to Stage 2 if needed.

---

### 6. Missing docstring on backend/agents/__init__.py

**File:** `backend/agents/__init__.py`
**Issue:** File exists but has only a docstring on line 1, no imports or __all__ export.

**Why it matters:** The module docstring is good, but the file is empty. Future readers will wonder if __init__.py is intentionally minimal or if something was omitted.

**Recommendation:** Add a comment or __all__ to clarify intent:
```python
"""Phase 5 agentic research layer — LangGraph-based autonomous strategy research."""

__all__ = [
    "build_graph",
    "AgentState",
    "supervisor_node",
]
```

Or, if the module is truly empty, add a comment:
```python
"""Phase 5 agentic research layer — LangGraph-based autonomous strategy research.

This module contains agent orchestration, tools, and providers. Import specific
submodules or use backend.agents.graph.build_graph() to instantiate the research loop.
"""
```

---

### 7. BedrockAdapter._parse_converse_response lacks error handling for malformed responses

**File:** `backend/agents/providers/bedrock.py`, lines 221–247
**Issue:** The response parser assumes `response.get("output", {}).get("message", {})` and `response.get("usage", {})` exist and are well-formed dicts. If Bedrock returns an unexpected structure, the parser silently creates an empty ConverseResult.

**Why it matters:** Silent failures are hard to debug. If Bedrock API changes or returns an error in an unexpected format, the agent will continue with partial data.

**Recommendation:** Add explicit error checking:
```python
@staticmethod
def _parse_converse_response(response: dict[str, Any]) -> ConverseResult:
    """Convert a raw Bedrock Converse response dict to ConverseResult.

    Raises
    ------
    ValueError
        If the response is missing required fields or is malformed.
    """
    # Validate top-level structure
    if not response or not isinstance(response, dict):
        raise ValueError(f"Expected dict response, got {type(response)}")

    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError(f"Missing or invalid 'usage' field in response")

    input_tokens: int = usage.get("inputTokens", 0)
    # ... rest
```

For Stage 1, add a comment noting the assumption. For Stage 2, add validation.

---

### 8. No explicit import of langgraph in tests; mocking may not work correctly

**File:** `backend/tests/test_agents_foundation.py`, line 139–144
**Issue:** Test `test_build_graph_compiles()` imports `build_graph` and checks for `.invoke` method. However, there is no mock for langgraph's StateGraph, so this test will fail if langgraph is not installed.

**Why it matters:** The test will pass in an environment with langgraph but fail in CI if langgraph is missing or pinned incorrectly. The error message will be misleading.

**Recommendation:** Add an explicit check in the test or in conftest.py:
```python
import pytest

# At the top of test_agents_foundation.py
try:
    import langgraph
except ImportError:
    pytest.skip("langgraph not installed", allow_module_level=True)
```

Or, ensure langgraph is in requirements.txt (it should be). Add a comment:
```python
# NOTE: test_build_graph_compiles requires langgraph to be installed.
# See requirements.txt and docs/architecture/phase5-architecture.md §1 (Risk #1).
```

---

### 9. ToolCallError detail may contain sensitive information (stack traces, full URLs)

**File:** `backend/agents/tools/client.py`, lines 128–135
**Issue:** When a backend API call fails, the error detail is extracted from the response body. If the backend returns a stack trace or internal error message, this detail is included in ToolCallError and will be logged/returned to the LLM.

**Why it matters:** Leaking stack traces or internal URLs to the LLM (and eventually logs) is a security and privacy concern.

**Recommendation:** Sanitize error details before including in ToolCallError. Or, log the full detail but return a generic message to the LLM.

**Suggested fix:**
```python
@staticmethod
def _parse(response: httpx.Response, tool_name: str) -> Any:
    """Parse response or raise ToolCallError on non-2xx."""
    if response.is_success:
        ...

    # Extract detail safely
    try:
        payload = response.json()
        detail = payload.get("detail", response.text)
    except (json.JSONDecodeError, AttributeError):
        detail = response.text

    # Sanitize: truncate very long details (likely stack traces)
    if len(detail) > 500:
        detail = detail[:500] + "... (truncated)"

    raise ToolCallError(tool_name, response.status_code, str(detail))
```

For Stage 1, add a comment noting the assumption. For Stage 2, implement sanitization.

---

### 10. Stub node implementations use hardcoded "stub" strings instead of error codes

**File:** `backend/agents/backtest_diagnostics.py`, line 34
`backend/agents/generation_comparator.py`, line 33
**Issue:** Stub nodes return `diagnosis_summary="stub"` and `comparison_recommendation="stub"`. These are string literals that will confuse downstream code. A real diagnostics node should return structured recommendations, not the word "stub."

**Why it matters:** If Stage 2 code checks `if diagnosis_summary == "stub"`, that is fragile. If the supervisor routes based on `comparison_recommendation`, the "stub" value will break routing logic.

**Recommendation:** Use a more explicit marker:
```python
# In backtest_diagnostics.py
return {
    "next_node": "supervisor",
    "diagnosis_summary": "[PLACEHOLDER — Stage 1 stub, no LLM diagnosis yet]",
    "discard": False,
    "errors": prior_errors + ["backtest_diagnostics: stub — not yet implemented"],
}
```

Or, define a constant:
```python
_STUB_MARKER = "[STAGE_1_STUB]"

return {
    "diagnosis_summary": f"{_STUB_MARKER} no LLM analysis available",
    ...
}
```

For Stage 1, this is cosmetic. For Stage 2, ensure stub markers are distinct from real values.

---

## CLEAN — No Issues Found In

### Well-Designed Modules

- **`backend/agents/state.py`** — AgentState is well-structured, clearly commented by domain, with two initialization helpers. LangGraph TypedDict contract respected.

- **`backend/agents/graph.py`** — Minimal, focused, correct LangGraph wiring. Unconditional return edges and conditional supervisor routing are properly defined.

- **`backend/agents/supervisor.py`** — Routing logic is clear, deterministic, and easy to trace. Comments on each branch are helpful.

- **`backend/agents/tools/client.py`** — Async HTTP client is clean, minimal, well-separated from schema and executor logic. ToolCallError is well-defined.

- **`backend/agents/tools/schemas.py`** — All Pydantic v2 models are correct. `model_validator` for strategy requirement is a good example of input validation. No use of deprecated `.parse_obj()` or `__fields__`.

- **`backend/agents/tools/backtest.py` and `strategy.py`** — Tool executors are simple and focused. Each function has a clear docstring mapping to the backend endpoint. Input/output types are explicit.

- **`backend/agents/providers/logging.py`** — Structured JSON logging is clean. Each event type has a dedicated method with clear parameters. Loguru integration is correct. No coupling to BedrockAdapter (exception: BedrockAdapter bypasses this logger in Stage 1).

- **`backend/agents/providers/bedrock.py`** — BedrockAdapter is well-designed. Lazy client initialization avoids bootstrap issues. Async/executor pattern is correct. Tool use extraction is non-invasive.

- **`backend/tests/test_agents_foundation.py`** — Tests are focused, deterministic, and well-organized by concern (state, bedrock, supervisor, graph, schemas, client). Happy-path coverage is good for Stage 1.

---

### API Integration

- **`backend/schemas/requests.py`** — Phase 4 schemas are clean and reusable. New Phase 5 agent schemas add to the same file without disrupting existing contracts. BacktestJobRequest validator is a good pattern.

- **`apps/api/routes/strategies.py` and `backtests.py`** — Existing routes are well-structured and simple. No changes were needed to support agent tool calls; the API contracts are sufficient.

---

## Summary of Findings by Severity

| Severity | Count | Category |
|---|---|---|
| **BLOCKER** | 0 | Ready to merge |
| **WARNING** | 5 | Document or refactor iteration counter, deduplicate state init, consolidate logging, validate tool input in Stage 2, prevent invalid next_node strings |
| **SUGGESTION** | 10 | Polish AgentLogger session_id, document tool schema split, add error-path tests, clarify supervisor routing, consider nested types in Stage 2, populate __init__.py exports, harden response parsing, check langgraph import, sanitize error details, improve stub markers |
| **CLEAN** | 8+ | Well-designed modules need no changes |

---

## Recommendations for Stage 2

1. **LLM Integration:** Implement StrategyResearcher, BacktestDiagnostics, GenerationComparator nodes with real Bedrock calls. Replace stub markers with proper schema-typed outputs.

2. **Error Handling:** Add retry loops for tool calls, network timeouts, and LLM failures. Implement the risk mitigations in phase5-architecture.md §7.

3. **Logging Consolidation:** Move BedrockAdapter logging to AgentLogger; ensure all events use the same JSON format and session/trace correlation.

4. **Schema Deduplication:** Decide whether to import from requests.py or maintain separate tool/api schemas. Document the boundary.

5. **State Deduplication:** Refactor DEFAULT_STATE and make_default_state to use a shared initialization path.

6. **Experiment Layer:** Implement backend/lab/ modules (experiment_registry.py, evaluation.py, mutation.py) per the architecture spec. Wire experiment CRUD tools into supervisor routing.

7. **Test Coverage:** Add error-path tests, LLM failure simulations, and integration tests with mock API server.

---

## Conclusion

Phase 5 Stage 1 is a solid foundation. The architecture is clear, the code is modular and maintainable, and the design respects LangGraph contracts. The implementation closely follows the spec in `docs/architecture/phase5-architecture.md` with minor deviations (noted as WARNINGS).

No blockers. Ready for Stage 2 LLM integration and experimental layer development.

**Recommendation:** ✅ **APPROVE** for merge.
