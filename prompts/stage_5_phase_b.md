You are the orchestrator for Phase 5B of a Forex trading platform. Stage 1 is 
complete with 250/250 tests passing. Your job is to coordinate specialized 
subagents to wire real LLM calls into the supervisor graph, build the experiment 
registry, and expose the research API.

## VERIFIED FOUNDATION (do not re-implement, only extend)
- backend/agents/state.py — AgentState TypedDict + DEFAULT_STATE
- backend/agents/graph.py — build_graph() → CompiledStateGraph
- backend/agents/supervisor.py — deterministic routing (no LLM)
- backend/agents/strategy_researcher.py — STUB, ready for wiring
- backend/agents/backtest_diagnostics.py — STUB, ready for wiring
- backend/agents/generation_comparator.py — STUB, ready for wiring
- backend/agents/tools/schemas.py — 9 Pydantic v2 I/O models
- backend/agents/tools/client.py — MedallionClient + ToolCallError
- backend/agents/tools/backtest.py, strategy.py — tool executors
- backend/agents/providers/bedrock.py — BedrockAdapter (boto3 Converse API)
- backend/agents/providers/logging.py — structured JSON event logger
- All HIGH/MEDIUM API gaps closed; 5 deferred warnings (address in review)

## STAGE 1: PARALLEL RECONNAISSANCE (spawn simultaneously)

### Agent 1 — repo-recon-agent
Task: Focused scan on the Phase 5B entry surface only:
- Confirm all 5 Stage 1 entry checklist items exist and match expected contracts:
  · backend/agents/strategy_researcher.py — verify stub interface signature
  · backend/agents/backtest_diagnostics.py — verify stub interface signature
  · backend/agents/generation_comparator.py — verify stub interface signature
  · backend/agents/providers/bedrock.py — verify BedrockAdapter.invoke() signature
    and which Converse API parameters it exposes
  · backend/agents/tools/schemas.py — list all 9 model names and their fields
- Check if backend/lab/ directory exists (expect: no)
- Check if apps/api/routes/experiments.py or research.py exist (expect: no)
- Confirm current test count is exactly 250 and all pass
- Flag any deferred warnings from Stage 1 code review that touch Phase 5B files
Output: docs/recon/phase5b-entry-report.md

### Agent 2 — api-jobs-contract-agent
Task: Map the job/polling contract specifically for experiment and research flows:
- How are backtest jobs currently submitted and polled? (verify exact endpoint shapes)
- What result artifacts does a completed backtest job expose?
- What strategy CRUD endpoints exist? What fields are writable post-creation?
- Is there any existing experiment or research concept in the backend? (expect: no)
- Design the minimal endpoint set needed for Phase 5B (see Implementation targets below)
  — do not implement yet, just specify: method, path, request schema, response schema,
    job-or-sync, and which agent node consumes it
Output: docs/recon/phase5b-api-spec.md

### Agent 3 — phase5-agent-architect
Task: Finalize the LLM wiring design and experiment layer architecture:
- Design the full strategy_researcher_node implementation:
  · System prompt, input construction from AgentState, tool call sequence
  · How it reads HMM regime context from state
  · How it writes candidate strategies back to state
  · Error handling and retry policy with BedrockAdapter
- Design the backtest_diagnostics_node implementation:
  · How it reads backtest result artifacts from state
  · Prompt structure for zero-trade diagnosis, drawdown analysis, metric interpretation
  · How it writes diagnostic summary + recommended mutations back to state
- Design the experiment registry (backend/lab/):
  · experiment_registry.py — data model (ExperimentRecord), storage (DuckDB or 
    SQLite), CRUD operations, status enum
  · evaluation.py — how experiments are scored: metric extraction, comparison baseline,
    ranking function
  · mutation.py — strategy mutation primitives: parameter perturbation, rule 
    substitution, regime filter injection
- Design supervisor trigger flow: POST /api/research/run →
  creates experiment record → triggers supervisor graph → streams status
Output: docs/architecture/phase5b-architecture.md

---

## STAGE 2: PARALLEL IMPLEMENTATION (spawn after Stage 1 completes)
Use ONLY verified facts from phase5b-entry-report.md, phase5b-api-spec.md, and
phase5b-architecture.md. Do not invent signatures, field names, or endpoint paths.

### Agent 4 — bedrock-implementer
Task: Wire real Bedrock LLM calls into the three stub agent nodes.
Implement strategy_researcher_node:
- Replace stub body with real BedrockAdapter.invoke() call
- Construct prompt from AgentState: regime context, signal bank snapshot, 
  existing strategy list
- Parse structured response into List[StrategyCandidate] (Pydantic v2)
- Write candidates to state.strategy_candidates
- Log: model id, input tokens, output tokens, latency, node name
- On ToolCallError or Bedrock throttle: retry once with exponential backoff, 
  then set state.error and return — never raise to the graph

Implement backtest_diagnostics_node:
- Replace stub body with real BedrockAdapter.invoke() call
- Construct prompt from AgentState: backtest result artifact, trade list, 
  equity curve summary, metric dict
- Parse response into DiagnosticSummary (Pydantic v2): 
  failure_taxonomy, root_cause, recommended_mutations List[str], confidence float
- Write to state.diagnostic_summary
- Same logging and error policy as above

Implement generation_comparator_node:
- Compare two or more StrategyCandidate objects using a structured Bedrock call
- Output ComparisonResult (Pydantic v2): winner_id, rationale, score_delta
- Write to state.comparison_result

Unit tests (all mocked — no real Bedrock calls in tests):
- strategy_researcher: valid response parse, malformed JSON response handled,
  throttle triggers retry, second failure sets state.error
- backtest_diagnostics: zero-trade result diagnosed, drawdown result diagnosed
- generation_comparator: two-candidate comparison, tie-handling
Target: ≥90% coverage on all three nodes
Files: backend/agents/ only

### Agent 5 — api-contract-engineer
Task: Implement the experiment and research API endpoints per phase5b-api-spec.md.

Implement backend/lab/:
- experiment_registry.py:
  · ExperimentRecord Pydantic v2 model: id, status (enum), strategy_id, 
    backtest_job_id, diagnostic_summary, created_at, updated_at
  · ExperimentRegistry class: create(), get(), update_status(), list_recent(n=20)
  · Storage: DuckDB table experiments (reuse existing DuckDB connection pattern)
- evaluation.py:
  · score_experiment(record: ExperimentRecord) → ExperimentScore
  · compare_experiments(a, b) → ComparisonResult
  · Metrics extracted: sharpe, max_drawdown, win_rate, total_trades
- mutation.py:
  · perturb_parameters(strategy_dict, magnitude=0.1) → strategy_dict
  · substitute_rule(strategy_dict, rule_index, new_rule) → strategy_dict
  · inject_regime_filter(strategy_dict, regime_label) → strategy_dict

Implement apps/api/routes/experiments.py:
- GET  /api/experiments — list recent experiments (query param: limit, status)
- GET  /api/experiments/{id} — get single experiment record
- POST /api/experiments — create experiment record (no graph trigger yet)
- PATCH /api/experiments/{id}/status — update status

Implement apps/api/routes/research.py:
- POST /api/research/run — create experiment + trigger supervisor graph async
  · Request: { strategy_id, backtest_job_id, research_mode: "diagnose"|"improve"|"compare" }
  · Response: { experiment_id, status: "queued" }
- GET  /api/research/{experiment_id}/status — poll experiment + graph status
  · Response: { experiment_id, status, diagnostic_summary?, strategy_candidates?, 
    comparison_result?, error? }

Register both routers in the main FastAPI app.
Do NOT touch HMM internals, ingestion, or frontend.
Files: backend/lab/, apps/api/routes/experiments.py, apps/api/routes/research.py

### Agent 6 — quant-ml-strategist
Task: Provide regime context to the supervisor graph for strategy_researcher_node.
- Implement get_regime_context(symbol: str, as_of: datetime) → RegimeContext:
  · Loads the persisted HMM model for the symbol
  · Returns: current_regime_label, regime_probabilities Dict[str,float], 
    regime_history List[RegimeSnapshot] (last 10 bars),
    signal_bank_snapshot Dict[str, float] (latest values)
- Expose as a backend/agents/tools/regime.py tool executor
- Add RegimeContext and RegimeSnapshot to backend/agents/tools/schemas.py
- Write to AgentState.regime_context on graph entry (supervisor node reads this)
- Unit tests: mock HMM model load, verify RegimeContext fields populated correctly
Do NOT touch ingestion, backtesting engine, or frontend.
Files: backend/agents/tools/regime.py, backend/agents/tools/schemas.py (additive only)

---

## STAGE 3: REVIEW & VERIFICATION (spawn after Stage 2 completes)

### Agent 7 — code-reviewer
Task: Review all Stage 2 output. Prioritize the 5 deferred warnings from Stage 1:
- Re-evaluate each deferred warning against new code — escalate to blocker if 
  now triggered by Phase 5B additions
- Check all three wired nodes: LLM prompt construction defensiveness, 
  structured output parsing safety, error state propagation correctness
- Check experiment registry: no raw SQL string construction, no missing indexes 
  on status/created_at columns
- Check research routes: no blocking I/O in async route handlers, 
  background task pattern used correctly
- Flag any file now exceeding 300 lines
Output: docs/reviews/phase5b-review.md — blockers must be fixed before Stage 4

### Agent 8 — test-verification-engineer
Task: Extend the test suite to cover all Phase 5B additions.
Required test coverage:
- Experiment registry: create → list → update_status → get (full lifecycle)
- ExperimentRecord Pydantic validation: all required fields, enum enforcement
- evaluation.py: score_experiment with known metric fixture, comparison ordering
- mutation.py: parameter perturbation stays within valid range, rule substitution
  produces valid DSL, regime filter injection adds correct field
- POST /api/research/run: returns experiment_id + queued status, creates DB record
- GET /api/research/{id}/status: reflects state after graph node completion (mocked graph)
- regime.py tool: RegimeContext fields, signal_bank_snapshot populated
- Full graph integration: DEFAULT_STATE → supervisor routes to strategy_researcher 
  → node executes (mocked Bedrock) → state.strategy_candidates populated
All tests deterministic — mock Bedrock, mock DuckDB where needed, 
no real network calls.
Target: total suite ≥ 310 passing (250 existing + ≥60 new)
Files: tests/agent/, tests/lab/, tests/api/routes/

---

## CONSTRAINTS FOR ALL AGENTS
1. All new code extends the verified Stage 1 foundation — no refactoring of 
   passing code unless a blocker forces it (confirm with orchestrator first)
2. All Pydantic models: v2 syntax only (model_config, not class Config)
3. All async route handlers: no blocking calls — use BackgroundTasks or 
   asyncio.create_task for graph execution
4. Every LLM call logged via providers/logging.py: 
   { event, node, model, input_tokens, output_tokens, latency_ms, experiment_id }
5. BedrockAdapter is the only path to any LLM — no direct boto3 calls in nodes
6. All prompts stored as module-level constants (not inline f-strings) 
   for reviewability
7. ExperimentRecord IDs: UUID4, generated at creation, never reassigned
8. If any recon agent finds the stub signatures differ from what phase5b-architecture 
   assumes, STOP Stage 2 and resolve the conflict first

## SUCCESS CRITERIA FOR PHASE 5B COMPLETION
- [ ] strategy_researcher_node: real Bedrock call, structured output, error handling
- [ ] backtest_diagnostics_node: real Bedrock call, DiagnosticSummary populated
- [ ] generation_comparator_node: real Bedrock call, ComparisonResult populated
- [ ] backend/lab/ exists: experiment_registry, evaluation, mutation all implemented
- [ ] GET|POST /api/experiments and GET|POST /api/research/run all return correct shapes
- [ ] regime.py tool wired into AgentState on graph entry
- [ ] code-reviewer: 0 blockers (deferred warnings re-evaluated)
- [ ] pytest: ≥310 passing, 0 failures, ≥90% coverage on Phase 5B files