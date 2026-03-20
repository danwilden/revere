# Phase 5C Entry Reconnaissance Report

**Date:** 2026-03-15
**Purpose:** Ground-truth inventory for Phase 5C planning. All facts drawn directly from repository files.

---

## 1. backend/agents/ Node Inventory

All source files in `/Users/danwilden/Developer/Medallion/backend/agents/` (excluding `__pycache__`):

| File | Role | Bedrock Status |
|---|---|---|
| `__init__.py` | Package init | N/A |
| `state.py` | AgentState TypedDict + DEFAULT_STATE + make_default_state | N/A |
| `graph.py` | LangGraph StateGraph builder (`build_graph()`) | N/A |
| `supervisor.py` | Deterministic routing node | N/A — no LLM calls, rule-based only |
| `strategy_researcher.py` | Multi-turn tool-call loop node | FULLY WIRED — `adapter.converse()` in multi-turn loop, ThrottlingException backoff |
| `backtest_diagnostics.py` | Single-call diagnostics node | FULLY WIRED — `adapter.converse()` once, retry once on parse failure, hardcoded NO_EDGE fallback |
| `generation_comparator.py` | Single-call comparison node | FULLY WIRED — `adapter.converse()` once, retry once on parse failure |
| `providers/bedrock.py` | BedrockAdapter (boto3 Converse API) | Implementation |
| `providers/logging.py` | AgentLogger | N/A |
| `tools/schemas.py` | Pydantic v2 tool models | N/A |
| `tools/client.py` | MedallionClient HTTP wrapper | N/A |
| `tools/backtest.py` | Backtest tool executors | N/A |
| `tools/strategy.py` | Strategy tool executors | N/A |
| `tools/regime.py` | `get_regime_context`, `load_regime_context_from_state` | N/A |

**`backend/agents/feature_researcher.py` does not exist.** It is the primary deliverable to create in Phase 5C.

---

## 2. backend/features/ Existence

**WARNING — the Phase 5C prompt assumption ("expected: no") is WRONG.**

`/Users/danwilden/Developer/Medallion/backend/features/` EXISTS and contains:

- `__init__.py`
- `compute.py` — fully implemented feature pipeline

`compute.py` exports: `compute_features(df, ...)`, `run_feature_pipeline(...)`, `load_feature_matrix(...)`, `FEATURE_CODE_VERSION = "v1.0"`.

**Any Phase 5C plan that attempts to create this directory or overwrite compute.py will conflict with existing code. The feature researcher must import from `backend.features.compute`, not create it. `FeatureComputer` class must be APPENDED to the existing compute.py, not a new file.**

---

## 3. backend/agents/feature_researcher.py Existence

Confirmed absent. Does not exist anywhere in the repository.

---

## 4. backend/agents/tools/schemas.py — All Pydantic Model Class Names

Currently defined in `/Users/danwilden/Developer/Medallion/backend/agents/tools/schemas.py`:

**Input models:** `ListStrategiesInput`, `CreateStrategyInput`, `ValidateStrategyInput`, `SubmitBacktestInput`, `PollJobInput`, `GetBacktestRunInput`, `GetBacktestTradesInput`, `GetEquityCurveInput`, `ListBacktestRunsInput`, `GetHmmModelInput`, `GetRegimeContextInput`

**Output / record models:** `StrategyRecord`, `ValidateStrategyOutput`, `SubmitBacktestOutput`, `PollJobOutput`, `BacktestRunDetail`, `PerformanceMetric`, `GetBacktestRunOutput`, `TradeRecord`, `GetBacktestTradesOutput`, `EquityPoint`, `GetEquityCurveOutput`, `BacktestRunSummary`, `ListBacktestRunsOutput`, `StrategyCandidate`, `DiagnosticSummary`, `ComparisonResult`, `HmmModelStateStats`, `GetHmmModelOutput`, `RegimeSnapshot`, `RegimeContext`

**Enum:** `FailureTaxonomy`

**Collision check:** `FeatureSpec` and `FeatureEvalResult` are NOT present. No naming collision. Safe to add.

---

## 5. Sandbox Execution Patterns

**`subprocess` usage — execution isolation:**

- `/Users/danwilden/Developer/Medallion/backend/strategies/sandbox.py`
  - Line 27: `import subprocess`
  - Line 100: `proc = subprocess.run([sys.executable, tmp_path], input=stdin_payload, capture_output=True, text=True, timeout=timeout_secs)`
  - Line 134: `except subprocess.TimeoutExpired:`
  - Pattern: user code written to a temp `.py` file, executed as subprocess, communicates via JSON stdin/stdout, default timeout 5.0s.

- `/Users/danwilden/Developer/Medallion/backend/jobs/dukascopy_download.py`
  - Line 25: `import subprocess` — invokes dukascopy-node CLI. Unrelated to code isolation.

**RestrictedPython:** No occurrences in the codebase.

**exec() / eval():** No occurrences in application code.

**Implication for Phase 5C:** `run_sandboxed()` in `backend/strategies/sandbox.py` is the canonical template. The feature sandbox must use subprocess isolation. The spec calls for `multiprocessing.Process` + `Pipe` (df via pickle) rather than the strategies approach of writing a temp .py file + JSON stdin/stdout — both approaches are valid subprocess isolation.

---

## 6. DuckDB Connection Pattern

**File:** `/Users/danwilden/Developer/Medallion/backend/data/duckdb_store.py`

**Connection:** `duckdb.connect(self._path)` called in `__init__`, stored as `self._conn`. No context manager. Connection is held open for instance lifetime. DDL runs at init via `self._conn.execute(_DDL)`.

**Five table names (exact):**
1. `bars_1m`
2. `bars_agg`
3. `features`
4. `feature_runs`
5. `regime_labels`

**bars_1m columns (exact, DDL order):**
`instrument_id` (TEXT NOT NULL), `timestamp_utc` (TIMESTAMP NOT NULL), `open` (DOUBLE NOT NULL), `high` (DOUBLE NOT NULL), `low` (DOUBLE NOT NULL), `close` (DOUBLE NOT NULL), `volume` (DOUBLE DEFAULT 0), `source` (TEXT NOT NULL), `quality_flag` (TEXT DEFAULT 'ok')
Primary key: `(instrument_id, timestamp_utc)`

**bars_agg columns:** `instrument_id`, `timeframe`, `timestamp_utc`, `open`, `high`, `low`, `close`, `volume`, `source`, `derivation_version`
Primary key: `(instrument_id, timeframe, timestamp_utc)`

**features columns (tall format):** `instrument_id`, `timeframe`, `timestamp_utc`, `feature_run_id`, `feature_name`, `feature_value`
Primary key: `(instrument_id, timeframe, timestamp_utc, feature_run_id, feature_name)`

**feature_runs columns:** `id`, `feature_set_name`, `code_version`, `parameters_json`, `start_date`, `end_date`, `created_at`

**regime_labels columns:** `model_id`, `instrument_id`, `timeframe`, `timestamp_utc`, `state_id`, `regime_label`, `state_probabilities_json`
Primary key: `(model_id, instrument_id, timeframe, timestamp_utc)`

---

## 7. Exact Regime Label Strings

From `/Users/danwilden/Developer/Medallion/backend/models/labeling.py`, module-level constants collected in `ALL_LABELS`:

1. `"TREND_BULL_LOW_VOL"`
2. `"TREND_BULL_HIGH_VOL"`
3. `"TREND_BEAR_LOW_VOL"`
4. `"TREND_BEAR_HIGH_VOL"`
5. `"RANGE_MEAN_REVERT"`
6. `"CHOPPY_SIGNAL"`
7. `"CHOPPY_NOISE"`

Edge case labels (not in ALL_LABELS): `"state_{N}_empty"` for states with zero bars; `"{base_label}_{i}"` suffix overflow when all 7 canonical labels are consumed.

**WARNING — volatility threshold:** The MEMORY.md handoff describes the threshold as "top 60th percentile." The code uses the **median** of `mean_volatility` across valid states. ANOVA grouping should use median, not 60th percentile.

**WARNING — session feature:** MEMORY.md documents session as "0=Asia, 1=London, 2=NY, 3=London/NY overlap" (4 buckets). The actual implementation in `compute.py` has **5 buckets**: 0=Asia (00-07 UTC), 1=London (08-12 UTC), 2=Overlap (13-16 UTC), 3=NY (17-20 UTC), 4=Off-hours (21-23 UTC). Any feature researcher prompt describing session values must use the 5-bucket implementation definition.

---

## 8. LocalMetadataRepository Pattern

**Files:** `backend/data/local_metadata.py` (or `repositories.py`), `backend/lab/experiment_registry.py`

**Storage mechanics:**
- Base dir passed at init; each store name → one JSON file `{base_path}/{store_name}.json`
- In-memory dict cache `self._stores`, loaded lazily; protected by `threading.RLock()`
- Atomic writes: write `.tmp` then `tmp.replace(path)`
- Records keyed by `record["id"]` (exception: instruments keyed by `record["symbol"]`)

**Four primitives required by any new registry class:**
- `_upsert(store_name, record)` — writes `store[record["id"]] = record`, saves
- `_get(store_name, key) -> dict | None`
- `_update(store_name, key, updates)` — merges via `dict.update()`, saves
- `_list(store_name) -> list[dict]`

**ExperimentRegistry pattern to replicate exactly:**
- Constructor takes `metadata_repo: Any` — does not instantiate `LocalMetadataRepository` itself
- `_STORE = "experiments"` class constant
- `create()` builds a Pydantic model, calls `self._repo._upsert(self._STORE, record.model_dump(mode="json"))`
- `get()` calls `self._repo._get(self._STORE, id)` then `SomeRecord.model_validate(raw)`
- `list_recent()` calls `self._repo._list(self._STORE)`, filters, sorts by `created_at`

**For FeatureLibrary:** Use `_STORE = "features"` (or `"feature_library"`). Key records by `feature_name` (not `id`) since feature names are unique natural keys. This requires a slight adaptation of `_upsert` or storing records as `{name: record}` dict.

---

## 9. AgentState TypedDict — All Fields + DEFAULT_STATE

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/state.py`

`AgentState(TypedDict, total=False)` — all fields are optional.

**Complete field list by category:**

Session context: `session_id: str`, `trace_id: str`, `requested_by: str`, `created_at: str`

Experiment scope: `instrument: str`, `timeframe: str`, `test_start: str`, `test_end: str`, `model_id: str | None`, `feature_run_id: str | None`

Experiment registry: `experiment_id: str | None`, `parent_experiment_id: str | None`, `generation: int`

LLM content: `hypothesis: str | None`, `mutation_plan: str | None`

Strategy artifacts: `strategy_id: str | None`, `strategy_definition: dict[str, Any] | None`

Backtest artifacts: `job_id: str | None`, `backtest_run_id: str | None`, `backtest_metrics: dict[str, Any] | None`, `backtest_trades: list[dict] | None`, `equity_curve: list[dict] | None`

Diagnosis artifacts: `diagnosis_summary: str | None`, `recommended_mutations: list[str] | None`, `discard: bool | None`

Comparison artifacts: `comparison_summary: str | None`, `comparison_recommendation: str | None`

Phase 5B extended: `regime_context: dict[str, Any] | None`, `strategy_candidates: list[dict[str, Any]] | None`, `selected_candidate_id: str | None`, `diagnostic_summary: dict[str, Any] | None`, `comparison_result: dict[str, Any] | None`

Robustness: `robustness_passed: bool | None`, `robustness_report: dict[str, Any] | None`

Flow control: `next_node: str`, `task: str`, `iteration: int`, `errors: list[str]`, `human_approval_required: bool`

**DEFAULT_STATE defaults:** `instrument="EUR_USD"`, `timeframe="H4"`, `test_start="2024-01-01"`, `test_end="2024-06-01"`, `generation=0`, `next_node="supervisor"`, `task="generate_seed"`, `iteration=0`, `errors=[]`, `human_approval_required=False`; all artifact fields `None`.

**Phase 5C additions required:**
- `feature_eval_results: list[dict[str, Any]] | None` — new field, default `None`
- `research_mode: str | None` — new routing trigger field, default `None`

Both must be added to: `AgentState` TypedDict, `DEFAULT_STATE` dict, and `make_default_state()` function.

---

## 10. build_graph() and Supervisor Routing

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/graph.py`

**Graph topology:**
- Entry: `supervisor`
- Registered nodes: `supervisor`, `strategy_researcher`, `backtest_diagnostics`, `generation_comparator`
- Conditional edges from `supervisor` using `route_next(state) -> str` which reads `state["next_node"]`
- Path map: `{"strategy_researcher": "strategy_researcher", "backtest_diagnostics": "backtest_diagnostics", "generation_comparator": "generation_comparator", "END": END}`
- All three worker nodes return to `supervisor` unconditionally via `add_edge`

**Supervisor routing logic (priority order, from supervisor.py):**

1. `task == "done"` OR `iteration >= 10` → `"END"`
2. `task == "generate_seed"` → `"strategy_researcher"`
3. `backtest_run_id` set AND `diagnosis_summary` is None → `"backtest_diagnostics"`
4. `diagnosis_summary` set AND `discard is True` → `"END"`
5. `generation >= 1` AND `backtest_run_id` set AND `diagnosis_summary` set AND `discard is not True` AND `comparison_result is None` → `"generation_comparator"`
6. `diagnosis_summary` set AND `discard is False` → `"strategy_researcher"`
7. `task == "mutate"` AND `backtest_run_id` set → `"strategy_researcher"`
8. Fallback → `"strategy_researcher"`

**Phase 5C graph changes required:**
- Add `graph.add_node("feature_researcher", feature_researcher_node)` in `build_graph()`
- Add `"feature_researcher": "feature_researcher"` to the conditional edge path map
- Add `graph.add_edge("feature_researcher", "supervisor")`
- Add routing condition `research_mode == "discover_features"` at priority position 2 in supervisor

---

## Summary of Planning Implications

**Safe to build on:**
- `backend/agents/feature_researcher.py` does not exist — safe to create
- `FeatureSpec` and `FeatureEvalResult` names are clear in schemas.py — no collision
- Sandbox pattern (`run_sandboxed`) available as reference; feature sandbox uses multiprocessing.Process + Pipe
- All DuckDB table/column names are verified exact (see Section 6)
- All 7 regime label strings are verified exact (see Section 7)
- `_upsert`/`_get`/`_update`/`_list` primitives are the correct storage API
- `AgentState total=False` — adding new fields is backward compatible
- `build_graph()` is the only registration point for new nodes

**Must respect before writing any compute code:**
- `backend/features/` already exists — do NOT recreate it. Append `FeatureComputer` to existing `compute.py`.
- Session feature has 5 buckets in code (0–4), not 4 as documented in MEMORY.md.
- Volatility boundary is median, not 60th percentile, in `auto_label_states`.
- `diagnosis_summary` (str) and `diagnostic_summary` (dict) are separate state fields — do not confuse them.
