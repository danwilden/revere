## ACTIVE SPEC: Medallion DIME (Detect–Integrate–Mark–Execute)

You are Claude Code implementing a DIME-style agentic research loop **inside this Medallion repository**. You are to orchestrate teams of agents, DONT WRITE THE CODE YOURSELF!, DIME in this context means:

- **Detect**: produce structured diagnostics / feature-eval signals and failure taxonomies from real backtests or feature computations
- **Integrate**: persist the evolving research artifacts (experiments, strategies, feature-library records) using the repository’s metadata + storage abstractions
- **Mark**: write structured “decision” outputs into `AgentState` and rely on the supervisor routing + API terminalization rules
- **Execute**: call the Bedrock-powered specialist nodes that use the repo’s Bedrock tool schema + tool dispatch loops

Hard rules:
- Use the repository’s real modules, contracts, and calling conventions (see “Repository anchors” below).
- Use Bedrock tool calls via `backend/agents/providers/bedrock.py` using `BedrockAdapter.converse()` and stop/retry behavior consistent with the existing agent patterns.

---
## Repository anchors (must use these modules)

LangGraph / supervisor routing:
- Graph build: `backend/agents/graph.py`
- Supervisor + routing: `backend/agents/supervisor.py`
- Agent nodes: `backend/agents/strategy_researcher.py`, `backend/agents/backtest_diagnostics.py`, `backend/agents/generation_comparator.py`, `backend/agents/feature_researcher.py` (Phase 5C)

Bedrock calling:
- Adapter: `backend/agents/providers/bedrock.py`
- Calling: `BedrockAdapter.converse()` (there is no `invoke()` in this repo)

Persistence / “DB” abstractions (no SQLite/aiosqlite in this DIME spec):
- Metadata repo interface: `backend/data/repositories.py`
- Local implementation: `backend/data/local_metadata.py` (`LocalMetadataRepository`)
- Base paths: `backend/config.py`
  - DuckDB: `settings.duckdb_path` (default `data/market.duckdb`)
  - Metadata: `settings.metadata_path` (default `data/metadata`)

Phase 5B “lab” helpers (integration contracts):
- Experiment registry + record: `backend/lab/experiment_registry.py`
- Scoring + compare gates: `backend/lab/evaluation.py`
- Mutation helpers: `backend/lab/mutation.py`

Phase 5B trigger + terminalization (integration + mark completion):
- API route: `apps/api/routes/research.py`
  - `POST /api/research/run` creates experiment record + runs graph in background
  - `_write_graph_result()` maps `discard`, `comparison_recommendation`, `backtest_run_id` to `ExperimentStatus`

Phase 5C feature discovery pipeline (detect + execute for features):
- Feature compute/evaluate/sandbox/library:
  - `backend/features/compute.py` (extension point)
  - `backend/features/sandbox.py`
  - `backend/features/evaluate.py`
  - `backend/features/feature_library.py`
- Feature tools:
  - `backend/agents/tools/feature.py`

---
## DIME layer mapping to Medallion Phases 5B/5C

### 1) Detect (structured signals)
Phase 5B detect:
- `backend/agents/backtest_diagnostics.py` receives strategy backtest outputs and produces a structured `DiagnosticSummary`:
  - `failure_taxonomy`
  - `root_cause`
  - `recommended_mutations` (list[str])
  - `confidence` (0.0-1.0)
  - `discard` (bool)

Phase 5C detect:
- `backend/agents/feature_researcher.py` runs a multi-turn tool loop that produces structured `FeatureEvalResult` objects for proposed features:
  - `f_statistic`
  - `regime_breakdown`
  - `leakage_risk`
  - `registered` (gated by leakage rules + F-statistic threshold inside `FeatureLibrary`)

### 2) Integrate (persistence + artifact wiring)
Integrate means: persist/retrieve artifacts using the repo’s metadata abstractions and ensure the final state is written back for API clients.

Concrete integration responsibilities:
- `ExperimentRecord` is stored as JSON metadata using `ExperimentRegistry` + `LocalMetadataRepository` (see `backend/lab/experiment_registry.py` and `backend/data/local_metadata.py`).
- Final outcomes are persisted in `apps/api/routes/research.py` via `_write_graph_result()` using the terminal status rules.

### 3) Mark (decision outputs + supervisor terminalization)
Mark means: write structured decision fields into `AgentState` so the supervisor can route correctly.

Phase 5B mark fields (state contracts):
- `diagnostic_summary` (dict form of `DiagnosticSummary`)
- `comparison_result` (dict form of `ComparisonResult`)
- `discard` (bool for supervisor gating compatibility)
- `strategy_candidates` (candidate outputs accumulated across generations)

Supervisor finalization:
- Routing logic lives in `backend/agents/supervisor.py`.
- Terminal status is finalized by `apps/api/routes/research.py` `_write_graph_result()` using:
  - `discard is True` -> `ExperimentStatus.FAILED`
  - `comparison_recommendation == "continue"` -> `ExperimentStatus.SUCCEEDED`
  - `backtest_run_id present` -> `ExperimentStatus.ARCHIVED`
  - otherwise -> `ExperimentStatus.FAILED`

### 4) Execute (Bedrock specialist tool loops)
Phase 5B execute:
- `backend/agents/strategy_researcher.py`
  - Must use `BedrockAdapter.converse()` with the repo’s `RESEARCHER_TOOLS` tool specs.
  - Must run a tool loop until `stop_reason == "end_turn"` OR tool retries exceed the implemented limits.
  - Must dispatch tools by tool name, validate input models, execute tool functions, and append `toolResult` messages consistent with the repo’s format.
- `backend/agents/generation_comparator.py`
  - Calls Bedrock to compare candidate strategies.
  - Writes `comparison_result` and routes based on `recommendation`.

Phase 5C execute:
- `backend/agents/feature_researcher.py`
  - Must follow the same Bedrock tool-loop conventions.
  - Uses feature tools: `propose_feature`, `compute_feature`, `evaluate_feature`, `register_feature`.
  - Returns `feature_eval_results` and clears `research_mode` trigger for routing compatibility.

---
## Bedrock tool-loop contract (must match existing repo semantics)

Implementations must follow these conventions:
- Always call `adapter.converse(...)` from `backend/agents/providers/bedrock.py`.
- Provide `system_prompt` as a string constant inside each node module (e.g. `SYSTEM_PROMPT` in the node file).
- Provide `tools` as Bedrock tool specs (from the repo’s Pydantic model schemas where the architecture requires it).
- Multi-turn loop:
  - If `stop_reason == "tool_use"`:
    - Extract `(tool_name, tool_input)` via `BedrockAdapter.extract_tool_use(...)`
    - Validate input against the tool input Pydantic model
    - Execute the tool function
    - Append a toolResult message shaped like the repo expects (see Phase 5B architecture notes for the exact `toolResult` envelope)
    - Continue until end_turn
  - If `stop_reason == "end_turn"`:
    - Parse the final content as JSON and validate it against the expected output schema.

Safety conventions:
- Catch throttling errors and backoff consistent with the repo’s implemented retry policy.
- Never assume tool outputs are non-null; use structured validation and append errors back to the model as tool results when appropriate.

Critical correctness rule:
- There is no `invoke()` method; any attempt to call `invoke()` is a runtime bug in this repo.

---
### Exact `toolResult` message envelope (Phase 5B pattern)
When the LLM requests a tool, append the tool result message in the repo’s expected format:
```json
{
  "role": "user",
  "content": [{
    "toolResult": {
      "toolUseId": "<value from BedrockAdapter.extract_tool_use(...) tool_use['toolUseId']>",
      "content": [{"json": "<serialized output dict or error dict>"}],
      "status": "success" | "error"
    }
  }]
}
```

---
### Expected end-turn JSON outputs (parse `stop_reason == "end_turn"`)
For each node, parse the model’s final content as JSON and validate against the schema below.

`strategy_researcher.py` (StrategyCandidate schema):
```json
{
  "candidate_id": "<UUID>",
  "hypothesis": "<natural language hypothesis>",
  "strategy_id": "<strategy UUID>",
  "strategy_definition": { },
  "backtest_run_id": "<run UUID>",
  "metrics": { },
  "trade_count": <int>,
  "sharpe": <float or null>,
  "max_drawdown_pct": <float or null>,
  "win_rate": <float or null>,
  "generation": <int>
}
```

`backtest_diagnostics.py` (DiagnosticSummary schema):
```json
{
  "failure_taxonomy": "<one of: zero_trades | too_few_trades | excessive_drawdown | poor_sharpe | overfitting_signal | wrong_regime_filter | entry_too_restrictive | exit_too_early | exit_too_late | no_edge | positive>",
  "root_cause": "<2-3 sentence explanation>",
  "recommended_mutations": ["<specific actionable change 1>", "<specific actionable change 2>"],
  "confidence": <float 0.0-1.0>,
  "discard": <true|false>
}
```

`generation_comparator.py` (ComparisonResult schema):
```json
{
  "winner_id": "<candidate_id or null>",
  "winner_strategy_id": "<strategy_id or null>",
  "rationale": "<2-3 sentence explanation>",
  "score_delta": <float or null>,
  "recommendation": "<continue | archive | discard>",
  "scores": { "<candidate_id>": <float> }
}
```

`feature_researcher.py` final summary:
```json
{
  "features_proposed": <int>,
  "features_registered": <int>,
  "results": [
    {
      "feature_name": "<name>",
      "f_statistic": <float>,
      "regime_breakdown": { "<REGIME_LABEL>": <float> },
      "leakage_risk": "<none|low|medium|high>",
      "registered": <true|false>
    }
  ]
}
```

---
## Verification checklist for the generated system

After implementing/adapting DIME, run the repo’s Phase 5B/5C test coverage:
- `pytest backend/tests/test_phase5b* -q`
- `pytest backend/tests/test_phase5c* -q`

If you add/modify any agent routing logic, also run:
- `pytest backend/tests/test_*supervisor* -q`
- any route tests relevant to your changes (e.g. Phase 5B research API tests).

