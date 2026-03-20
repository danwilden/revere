You are the orchestrator for Phase 5C of a Forex trading platform. Phases 5A and 5B
are complete with 318/318 tests passing. Phase 5C implements the Feature Discovery
agent: the supervisor proposes new handcrafted features from interpretable families,
computes them, evaluates their regime-differentiation power, and registers survivors
in a named feature library.

Operate as an agent team, not a single linear worker. Maximize safe parallelism where
file ownership does not overlap. Prefer concurrent recon, concurrent implementation on
disjoint files, concurrent review, and concurrent test authoring. Use subagents heavily
to preserve orchestrator context window. Coordinate through written artifacts, not loose
memory. Every agent must read upstream artifacts before writing code.

## VERIFIED FOUNDATION (do not re-implement, only extend)
- backend/agents/state.py — AgentState TypedDict + DEFAULT_STATE
- backend/agents/graph.py — build_graph() → CompiledStateGraph
- backend/agents/supervisor.py — deterministic routing node
- backend/agents/providers/bedrock.py — BedrockAdapter, adapter.converse() only
- backend/agents/providers/logging.py — AgentLogger (per-call, session_id from state)
- backend/agents/tools/regime.py — RegimeContext wired into AgentState on graph entry
- backend/agents/tools/schemas.py — all Pydantic v2 tool I/O models
- backend/lab/ — ExperimentRegistry (LocalMetadataRepository), evaluation, mutation
- 318 tests passing, 0 failures, 0 review blockers

## CRITICAL ARCHITECTURE FACTS
- adapter.converse() — NOT adapter.invoke() — hard constraint, never changes
- AgentLogger instantiated per-call with session_id from state (not module-level)
- BackgroundTasks handles async graph execution in API routes
- All Pydantic models: v2 syntax only (model_config, not class Config)

---

## THE FEATURE SPEC CONTRACT
All agents must treat this as the canonical data model for Phase 5C.
Never invent fields; never omit fields.

FeatureSpec:
  name: str                   # e.g. "vol_compression_10_40"
  family: str                 # momentum | breakout | volatility | session |
                              # microstructure | regime_persistence
  formula_description: str    # natural language — for traceability
  lookback_bars: int          # maximum historical bars consumed
  dependency_columns: list[str]  # which source columns this feature reads
  transformation: str         # e.g. "rolling_ratio", "rank", "slope", "crossover"
  expected_intuition: str     # why this feature should predict price movement
  leakage_risk: str           # none | low | medium | high
                              # high = blocked from production, always
  code: str                   # Python lambda or function body for compute pipeline

FeatureEvalResult:
  feature_name: str
  f_statistic: float          # ANOVA F-statistic across regime classes
  regime_breakdown: dict[str, float]  # regime_label → mean feature value
  leakage_risk: str           # echoed from FeatureSpec
  registered: bool            # True if survivor threshold met and spec saved

ALLOWED FEATURE FAMILIES (agent is constrained to these only):
  momentum        — multi-horizon log returns, RSI variants, MACD signal line,
                    momentum z-score
  breakout        — Donchian channel position, range expansion ratio,
                    ATR relative to N-bar average
  volatility      — realised vol ratio (short/long), vol regime z-score,
                    vol compression/expansion
  session         — hour-of-day buckets, session overlap flags,
                    time-since-session-open, distance from rolling extremes
  microstructure  — spread-to-ATR ratio, vol-adjusted spread estimate
  regime_persistence — N-bar streak of same direction, consecutive highs/lows count

LEAKAGE RULE (hard): any feature using future bar data → leakage_risk=high →
blocked from registration and production use, regardless of F-statistic.

---

## STAGE 1: PARALLEL RECONNAISSANCE (spawn all simultaneously)

### Agent 1 — repo-recon-agent
Task: Focused scan on the Phase 5C entry surface:
- Confirm backend/agents/ node inventory: list all *_node files and their
  current stub vs wired status
- Check if backend/features/ or any feature library directory exists (expect: no)
- Check if backend/agents/feature_researcher.py exists (expect: no)
- Confirm backend/agents/tools/schemas.py current model list — Phase 5C will add
  FeatureSpec and FeatureEvalResult; verify no naming collision
- Confirm how sandbox execution is currently handled elsewhere in the codebase
  (look for subprocess, RestrictedPython, exec() patterns) — feature code validation
  will need this
- Confirm DuckDB connection pattern used by market-data layer — feature computation
  will read from the same bars tables
- Confirm what regime labels are currently emitted by the HMM layer — these are
  the classes for the ANOVA F-statistic grouping
Output: docs/recon/phase5c-entry-report.md

### Agent 2 — api-jobs-contract-agent
Task: Design the minimal API surface for feature discovery:
- Specify endpoints needed (method, path, request schema, response schema,
  sync vs async):
  · Trigger feature discovery run for a symbol + context
  · Poll feature discovery job status
  · List registered features in the feature library
  · Get a single feature spec by name
  · (Optional) Delete / deprecate a feature
- Specify how feature_library storage should be queryable: by family, by
  leakage_risk, by f_statistic threshold
- Verify whether existing job polling infrastructure (BackgroundTasks pattern)
  is reusable as-is or needs extension for feature discovery jobs
- Flag any contract gaps between what the feature_researcher_node outputs and
  what the API needs to surface
Output: docs/recon/phase5c-api-spec.md

### Agent 3 — phase5-agent-architect
Task: Design the complete feature discovery architecture:

feature_researcher_node design:
  - Input: AgentState fields consumed (regime_context, symbol, context dict
    describing weak areas in existing strategies)
  - System prompt structure: how to instruct Bedrock to propose ONE FeatureSpec
    at a time, constrained to allowed families, with leakage self-assessment
  - Tool call sequence: propose spec → validate via sandbox → compute → evaluate
  - How multiple features are proposed in one session (loop vs single call)
  - Output: List[FeatureEvalResult] written to AgentState

backend/features/ package design:
  - feature_library.py: FeatureLibrary class, storage backend (JSON via
    LocalMetadataRepository — match experiment_registry pattern), CRUD,
    query by family/leakage/f_statistic
  - compute.py: FeatureComputer — takes FeatureSpec, reads bars from DuckDB,
    applies spec.code via sandbox, returns pd.Series aligned to bar index
  - evaluate.py: FeatureEvaluator — takes computed Series + regime labels,
    computes ANOVA F-statistic, returns FeatureEvalResult
  - sandbox.py: safe execution of spec.code — subprocess isolation or
    RestrictedPython; must block file I/O, network, imports outside whitelist
    (numpy, pandas only); timeout after 5 seconds

Supervisor routing: how does the supervisor route to feature_researcher_node?
  What AgentState fields trigger this path vs strategy_researcher or
  backtest_diagnostics?

Survivor registration threshold: what F-statistic value qualifies a feature
  for registration? (recommend: F > 2.0 AND leakage_risk != "high")
  Document as a module-level constant REGISTRATION_THRESHOLD.

Output: docs/architecture/phase5c-architecture.md

---

## STAGE 2: PARALLEL IMPLEMENTATION (spawn after Stage 1 completes)
Use ONLY verified facts from phase5c-entry-report.md, phase5c-api-spec.md,
and phase5c-architecture.md. Do not invent DuckDB table names, regime label
strings, or file paths — use only what recon verified.

### Agent 4 — bedrock-implementer
Task: Implement feature_researcher_node and wire it into the graph. Divide into an agent team if you can

Implement backend/agents/feature_researcher.py:
  - feature_researcher_node(state: AgentState) → AgentState
  - System prompt: constrains Bedrock to propose ONE FeatureSpec per call,
    from allowed families only, with explicit leakage self-assessment
  - Prompt input: regime_context from state, symbol, context dict
  - Parse Bedrock response → FeatureSpec (Pydantic v2, strict validation)
  - If family not in allowed set → reject, do not register, log warning
  - If leakage_risk == "high" → do not pass to compute, log blocked
  - On valid spec: call FeatureComputer → FeatureEvaluator → FeatureLibrary
  - Loop: propose up to MAX_FEATURES_PER_SESSION = 5 specs per session
  - Write List[FeatureEvalResult] to state.feature_eval_results
  - On adapter.converse() error: retry once with backoff, then set state.error
  - Log every LLM call: node, model, input_tokens, output_tokens, latency_ms,
    feature_name, family, f_statistic (if reached)

Wire into graph:
  - Add feature_researcher_node to build_graph()
  - Update supervisor routing: new path to feature_researcher_node
  - Add feature_eval_results: List[FeatureEvalResult] to AgentState TypedDict
  - Add DEFAULT_STATE entry for feature_eval_results = []

Unit tests (all mocked — no real Bedrock, no real DuckDB):
  - Valid FeatureSpec parsed and passed to compute pipeline
  - Invalid family rejected before compute
  - leakage_risk=high blocked before compute
  - MAX_FEATURES_PER_SESSION=5 loop terminates correctly
  - adapter.converse() throttle → retry → second failure sets state.error
  - FeatureEvalResult written to state on success
  - Supervisor correctly routes to feature_researcher_node
Target: ≥90% coverage on feature_researcher.py
Files: backend/agents/feature_researcher.py, backend/agents/state.py,
       backend/agents/graph.py, backend/agents/supervisor.py

### Agent 5 — quant-ml-strategist
Task: Implement the backend/features/ compute and evaluate pipeline. Divide into an agent team if you can.

Implement backend/features/sandbox.py:
  - execute_feature_code(code: str, df: pd.DataFrame, timeout_seconds: int = 5)
    → pd.Series
  - Subprocess isolation: spawn a child process, pass df via pickle, receive
    Series result or error back via pipe
  - Import whitelist: numpy and pandas only — any other import → SandboxError
  - File I/O and network calls → SandboxError
  - Timeout exceeded → SandboxTimeoutError
  - Malformed return (not a pd.Series, wrong length, contains NaN > 20%) →
    SandboxValidationError

Implement backend/features/compute.py:
  - FeatureComputer.compute(spec: FeatureSpec, symbol: str,
      start: datetime, end: datetime) → pd.Series
  - Reads 1H bars from DuckDB using verified table/column names from recon
  - Passes spec.code to sandbox.execute_feature_code()
  - Aligns result Series index to bar timestamps
  - Raises FeatureComputeError on sandbox failure (wraps sandbox exceptions)

Implement backend/features/evaluate.py:
  - FeatureEvaluator.evaluate(series: pd.Series, regime_labels: pd.Series)
    → FeatureEvalResult
  - ANOVA F-statistic: scipy.stats.f_oneway grouped by regime_labels
  - regime_breakdown: mean feature value per regime class
  - registered: False (FeatureLibrary decides registration, not evaluator)
  - Handles edge cases: fewer than 2 regime classes present → F=0.0 with warning
  - Handles NaN in series: drop before computing (log % dropped)

Implement backend/features/feature_library.py:
  - FeatureLibrary(storage_path: Path)
  - register(spec: FeatureSpec, eval_result: FeatureEvalResult) → bool
    · Returns False (does not register) if leakage_risk == "high"
    · Returns False if f_statistic < REGISTRATION_THRESHOLD (2.0)
    · Returns True and persists if both checks pass
  - get(name: str) → Optional[FeatureSpec]
  - list_all() → List[FeatureSpec]
  - query(family=None, max_leakage=None, min_f_statistic=None) → List[FeatureSpec]
  - Storage: JSON via LocalMetadataRepository — match experiment_registry.py pattern
  - REGISTRATION_THRESHOLD = 2.0 as module-level constant

Add to backend/agents/tools/schemas.py (additive only):
  - FeatureSpec (Pydantic v2)
  - FeatureEvalResult (Pydantic v2)

Unit tests:
  - sandbox: valid code executes, invalid import blocked, timeout enforced,
    wrong-length Series rejected
  - compute: DuckDB read mocked, sandbox call mocked, Series returned correctly
  - evaluate: known fixture → expected F-statistic, edge case <2 classes → F=0.0
  - feature_library: register survivor passes, leakage_risk=high blocked,
    f_statistic < threshold blocked, query filters work
Target: ≥90% coverage on all backend/features/ files
Files: backend/features/ (new package), backend/agents/tools/schemas.py

### Agent 6 — api-contract-engineer
Task: Implement the feature discovery API per phase5c-api-spec.md.

Implement apps/api/routes/features.py:
  - POST /api/features/discover
    · Request: { symbol: str, research_mode: "feature_discovery",
        context: dict (optional, weak areas description) }
    · Response: { job_id, status: "queued" }
    · Creates experiment record, triggers feature_researcher_node via
      BackgroundTasks (match research.py pattern exactly)
  - GET /api/features/discover/{job_id}
    · Response: { job_id, status, feature_eval_results: List[FeatureEvalResult]?,
        error? }
  - GET /api/features/library
    · Query params: family, max_leakage, min_f_statistic, limit (default 50)
    · Response: { features: List[FeatureSpec], total: int }
  - GET /api/features/library/{name}
    · Response: FeatureSpec or 404

Register router in main FastAPI app.
Do NOT change existing routes or response shapes.
Do NOT touch HMM internals, frontend, or compute pipeline directly.
Files: apps/api/routes/features.py, main FastAPI app registration

---

## STAGE 3: REVIEW & VERIFICATION (spawn after Stage 2 completes)

### Agent 7 — code-reviewer
Task: Review all Stage 2 output:
- sandbox.py: verify subprocess isolation is real — exec() in the same process
  is NOT acceptable; must be a child process
- compute.py: verify no direct exec() or eval() calls — must go through sandbox
- feature_library.py: confirm REGISTRATION_THRESHOLD is a module-level constant,
  not hardcoded inline
- feature_researcher_node: confirm family allowlist check happens BEFORE any
  Bedrock call or compute — fail fast
- graph.py: confirm feature_researcher_node is properly registered and
  supervisor routing is deterministic (no LLM routing decisions)
- Check all new files for 300-line limit
- Re-evaluate any warnings carried from 5B — escalate if now triggered
Output: docs/reviews/phase5c-review.md — all blockers fixed before proceeding

### Agent 8 — test-verification-engineer
Task: Extend the test suite to cover all Phase 5C additions.

Backend tests:
  - feature_researcher_node full lifecycle: state in → LLM call mocked →
    compute mocked → evaluate mocked → FeatureEvalResult in state
  - Rejected family: node rejects spec, does not call compute, logs warning
  - Blocked leakage: leakage_risk=high, does not call compute, logs blocked
  - MAX_FEATURES_PER_SESSION respected: loop stops at 5
  - Sandbox timeout: FeatureComputeError propagated, state.error set
  - FeatureLibrary registration: survivor registers, blocked specs do not
  - ANOVA evaluation: two-regime fixture → expected F > 0, single regime → F = 0.0
  - POST /api/features/discover: returns job_id + queued status
  - GET /api/features/library with filters: family, max_leakage, min_f_statistic
  - GET /api/features/library/{name}: found and 404

Leakage regression test (critical):
  - Construct a FeatureSpec where code reads df['close'].shift(-1) (future bar)
  - Confirm leakage_risk=high is either self-reported by LLM (mocked) or caught
    by the blocked-registration check — feature must NEVER appear in library

All tests deterministic — mock Bedrock, mock DuckDB, mock sandbox execution.
No real network calls, no real subprocess spawning in tests.
Target: total suite ≥ 410 passing (318 existing + ≥92 new)
Files: tests/agent/, tests/features/, tests/api/routes/

---

## CONSTRAINTS FOR ALL AGENTS
1. adapter.converse() only — never adapter.invoke() — permanent hard constraint
2. Sandbox MUST be subprocess isolation — never exec()/eval() in main process
3. Feature family allowlist checked BEFORE compute and registration call — fail fast
4. leakage_risk=high blocks registration unconditionally — F-statistic irrelevant
5. REGISTRATION_THRESHOLD = 2.0 as module-level constant in feature_library.py
6. MAX_FEATURES_PER_SESSION = 5 as module-level constant in feature_researcher.py
7. All storage follows LocalMetadataRepository pattern from experiment_registry.py
8. Do not refactor passing code unless a Stage 3 blocker forces it — confirm first
9. If recon finds DuckDB table/column names differ from any assumption in this
   prompt, STOP Stage 2 and resolve before any compute code is written

## SUCCESS CRITERIA FOR PHASE 5C COMPLETION
- [ ] feature_researcher_node: proposes FeatureSpec, validates family, blocks
      leakage, calls compute + evaluate, writes FeatureEvalResult to state
- [ ] sandbox.py: subprocess isolation, import whitelist, timeout enforced
- [ ] compute.py: reads DuckDB bars, calls sandbox, returns aligned Series
- [ ] evaluate.py: ANOVA F-statistic across regime classes, handles edge cases
- [ ] feature_library.py: REGISTRATION_THRESHOLD enforced, leakage blocked,
      JSON persistence via LocalMetadataRepository
- [ ] FeatureSpec + FeatureEvalResult in schemas.py
- [ ] API: POST /api/features/discover, GET poll, GET library with filters
- [ ] Leakage regression test passes
- [ ] code-reviewer: 0 blockers
- [ ] pytest: ≥410 passing, 0 failures, ≥90% coverage on Phase 5C files



output from run:
⏺ Phase 5C is complete. Here's the final summary:

  ---
  Phase 5C — Feature Discovery — COMPLETE

  411 tests passing, 0 failures (target was ≥410)

  What was built

  ┌────────────────────┬────────────────────────────────────────┬─────────────┐
  │     Component      │                  File                  │   Status    │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ Subprocess sandbox │ backend/features/sandbox.py            │ ✅ New      │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ ANOVA evaluator    │ backend/features/evaluate.py           │ ✅ New      │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ Feature library    │ backend/features/feature_library.py    │ ✅ New      │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ FeatureComputer    │ backend/features/compute.py (appended) │ ✅ Extended │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ Tool executors     │ backend/agents/tools/feature.py        │ ✅ New      │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ LangGraph node     │ backend/agents/feature_researcher.py   │ ✅ New      │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ Schemas            │ backend/agents/tools/schemas.py        │ ✅ Extended │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ State fields       │ backend/agents/state.py                │ ✅ Extended │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ Supervisor routing │ backend/agents/supervisor.py           │ ✅ Extended │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ Graph wiring       │ backend/agents/graph.py                │ ✅ Extended │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ API routes         │ apps/api/routes/features.py            │ ✅ New      │
  ├────────────────────┼────────────────────────────────────────┼─────────────┤
  │ JobType enum       │ backend/schemas/enums.py               │ ✅ Extended │
  └────────────────────┴────────────────────────────────────────┴─────────────┘

  All success criteria met

  - 0 blockers from code review
  - Subprocess isolation: multiprocessing.Process + Pipe — never exec() in main process
  - REGISTRATION_THRESHOLD = 2.0 module-level constant, strict >
  - Leakage blocked unconditionally — F-statistic irrelevant for leakage_risk="high"
  - research_mode cleared to None on every return path
  - adapter.converse() only — no invoke()
  - All 4 API endpoints wired with BackgroundTasks pattern
  - 93 new tests covering all spec requirements including the leakage regression test