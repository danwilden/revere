# Phase 5 Architecture — Agentic Research Layer
# Medallion Platform | Revere Analytics
# Date: 2026-03-15
# Status: Stage 1 Foundation Design

---

## CRITICAL DECISION REQUIRED: LangGraph vs AWS Strands

The orchestration prompt and `02_platform_plan.md §11.7.1` specify **LangGraph**.
`requirements.txt` has `strands-agents>=0.1.0` (AWS Strands SDK).
This document is written for **LangGraph** per the active orchestration spec.
If AWS Strands is chosen instead, sections 3, 4, 6, and 7 require rewriting.

---

## 1. Package Layout

All new Phase 5 code lives in `backend/agents/` (existing stub) and `backend/lab/` (new).
No new FastAPI app is created — Phase 5 routes are added to `apps/api/routes/`.

```
backend/agents/
│  __init__.py                   # "Agent layer for Phase 5 autonomous research"
│  state.py                      # AgentState TypedDict — shared graph state
│  graph.py                      # LangGraph StateGraph definition and compilation
│  supervisor.py                 # ResearchSupervisor node — decides next action
│  strategy_researcher.py        # StrategyResearcher node — hypothesis → strategy
│  backtest_diagnostics.py       # BacktestDiagnostics node — explain outcomes
│  generation_comparator.py      # GenerationComparator node — compare experiments
│  robustness_reviewer.py        # RobustnessReviewer node — kill weak candidates
│  experiment_librarian.py       # ExperimentLibrarian node — dedup + lineage
│  feature_researcher.py         # FeatureResearcher node — propose features (5C)
│  model_researcher.py           # ModelResearcher node — AutoML interpretation (5D)
│
├── tools/
│   __init__.py                  # "Deterministic tool executors for agent nodes"
│   client.py                    # Typed HTTP client wrapping backend API (httpx)
│   backtest.py                  # submit_backtest, poll_job, get_backtest_run,
│   │                            #   get_backtest_trades, get_equity_curve,
│   │                            #   list_backtest_runs
│   strategy.py                  # create_strategy, validate_strategy, list_strategies
│   experiment.py                # create_experiment, update_experiment,
│   │                            #   get_experiment, list_experiments, get_lineage
│   schemas.py                   # All Pydantic input/output models for tools
│
└── providers/
    __init__.py                  # "LLM provider adapters"
    bedrock.py                   # BedrockAdapter — wraps boto3 Converse API
    logging.py                   # Structured node/LLM/tool call logger

backend/lab/
    __init__.py                  # "Experiment registry and evaluation infrastructure"
    experiment_registry.py       # Experiment CRUD via LocalMetadataRepository
    evaluation.py                # EvaluationContract — compute pass/fail per metric
    mutation.py                  # Strategy mutation operations (deterministic)

apps/api/routes/
    experiments.py               # Phase 5A experiment CRUD + lineage routes
    research.py                  # Phase 5A supervisor trigger + job polling
    # chat.py                    # Phase 5 chat (later)
    # automl.py                  # Phase 5D AutoML jobs (later)
```

**Boundary:** `backend/agents/` owns orchestration. `backend/lab/` owns persistent experiment state. `apps/api/routes/` owns HTTP surfaces. The existing `backend/strategies/`, `backend/backtest/`, `backend/models/` are owned by Phases 3–4 and must NOT be modified by Phase 5 agents.

---

## 2. Supervisor Graph Topology

```
                        ┌─────────────────────────────┐
                        │      ResearchSupervisor      │
                        │  (decide what to work on)    │
                        └──────────┬──────────────────┘
                                   │ routes to:
          ┌────────────────────────┼──────────────────────────┐
          │                        │                          │
          ▼                        ▼                          ▼
  ┌───────────────┐    ┌──────────────────────┐   ┌────────────────────┐
  │  Strategy     │    │  Backtest Diagnostics │   │  Generation        │
  │  Researcher   │    │  (explain outcome)    │   │  Comparator        │
  │  (build/mutate│    └──────────┬────────────┘   └────────┬───────────┘
  │   strategy)   │               │                         │
  └───────┬───────┘               │ recommendation          │ comparison
          │                       ▼                         ▼
          │ strategy_id    ┌──────────────┐        ┌────────────────────┐
          ▼                │  Supervisor   │◄───────│  Experiment        │
   [API: create +          │  (loop back)  │        │  Librarian         │
    validate strategy]     └──────┬───────┘        │  (dedup + lineage) │
          │                       │                 └────────────────────┘
          ▼                       │ if candidate
   [API: submit_backtest]         ▼
          │               ┌──────────────────┐
          │               │  Robustness      │
          ▼               │  Reviewer        │
   [API: poll_job]         │  (kill weak)     │
          │               └──────────────────┘
          ▼
   [API: get_backtest_run
         get_trades
         get_equity]
```

### Routing Logic

The supervisor node reads `AgentState` and outputs a `next_node: str` routing decision:

| State condition | Routes to |
|---|---|
| `task == "generate_seed"` | `strategy_researcher` |
| `task == "mutate"` AND `backtest_result` exists | `strategy_researcher` (with parent context) |
| `backtest_result` is fresh AND not yet diagnosed | `backtest_diagnostics` |
| `diagnosis` exists AND `discard == False` | `strategy_researcher` (mutation) |
| `diagnosis` exists AND `discard == True` | `experiment_librarian` (archive) |
| two experiments with same parent exist | `generation_comparator` |
| comparator result recommends candidate | `robustness_reviewer` |
| robustness passes | `experiment_librarian` (promote) |
| `task == "done"` OR max_iterations reached | `END` |

### Conditional Edge Function

```python
def route_next(state: AgentState) -> str:
    return state["next_node"]  # supervisor writes this field
```

All edges from supervisor use this function. All other nodes return to supervisor.

---

## 3. Typed AgentState (LangGraph TypedDict)

```python
from __future__ import annotations
from typing import Any, TypedDict
from datetime import datetime


class AgentState(TypedDict, total=False):
    # ── Session context ──────────────────────────────────────────────────────
    session_id: str                     # UUID for this research session
    trace_id: str                       # UUID for structured log correlation
    requested_by: str                   # "system" | user identifier
    created_at: str                     # ISO datetime

    # ── Experiment scope ─────────────────────────────────────────────────────
    instrument: str                     # e.g. "EUR_USD"
    timeframe: str                      # e.g. "H4"
    test_start: str                     # ISO date
    test_end: str                       # ISO date
    model_id: str | None                # HMM model to use for regime labels
    feature_run_id: str | None          # feature run to use

    # ── Experiment registry ──────────────────────────────────────────────────
    experiment_id: str | None           # current active experiment UUID
    parent_experiment_id: str | None    # lineage: seed or prior generation
    generation: int                     # mutation generation (0 = seed)

    # ── LLM-generated content ────────────────────────────────────────────────
    hypothesis: str | None             # natural language strategy hypothesis
    mutation_plan: str | None          # LLM description of what to change

    # ── Strategy artifacts ────────────────────────────────────────────────────
    strategy_id: str | None            # created strategy UUID
    strategy_definition: dict[str, Any] | None  # rules_engine JSON

    # ── Backtest artifacts ────────────────────────────────────────────────────
    job_id: str | None                 # current backtest job_id
    backtest_run_id: str | None        # result_ref after job SUCCEEDED
    backtest_metrics: dict[str, Any] | None   # keyed metric_name → value
    backtest_trades: list[dict] | None
    equity_curve: list[dict] | None

    # ── Diagnosis artifacts ───────────────────────────────────────────────────
    diagnosis_summary: str | None
    recommended_mutations: list[str] | None
    discard: bool | None               # diagnostics recommendation

    # ── Comparison artifacts ──────────────────────────────────────────────────
    comparison_summary: str | None
    comparison_recommendation: str | None  # "continue" | "archive" | "discard"

    # ── Robustness artifacts ──────────────────────────────────────────────────
    robustness_passed: bool | None
    robustness_report: dict[str, Any] | None

    # ── Flow control ─────────────────────────────────────────────────────────
    next_node: str                     # supervisor writes; conditional edge reads
    task: str                          # "generate_seed" | "mutate" | "review" | "done"
    iteration: int                     # guard against infinite loops (max: 10)
    errors: list[str]                  # accumulated non-fatal errors
    human_approval_required: bool      # set True before promotion to "validated"
```

---

## 4. Tool Contracts

All tools in `backend/agents/tools/` call the verified backend API via an `httpx` client.
Input/output Pydantic models are defined in `backend/agents/tools/schemas.py`.
The full typed schema set is documented in `docs/recon/api-contract-report.md §6`.

### Tool summary

| Tool | Module | Input model | Output model | Backend endpoint |
|---|---|---|---|---|
| `list_strategies` | `strategy.py` | `ListStrategiesInput` | `list[StrategyRecord]` | GET /api/strategies |
| `create_strategy` | `strategy.py` | `CreateStrategyInput` | `StrategyRecord` | POST /api/strategies |
| `validate_strategy` | `strategy.py` | `ValidateStrategyInput` | `ValidateStrategyOutput` | POST /api/strategies/{id}/validate |
| `submit_backtest` | `backtest.py` | `SubmitBacktestInput` | `SubmitBacktestOutput` | POST /api/backtests/jobs |
| `poll_job` | `backtest.py` | `PollJobInput` | `PollJobOutput` | GET /api/jobs/{id} |
| `get_backtest_run` | `backtest.py` | `GetBacktestRunInput` | `GetBacktestRunOutput` | GET /api/backtests/runs/{id} |
| `get_backtest_trades` | `backtest.py` | `GetBacktestTradesInput` | `GetBacktestTradesOutput` | GET /api/backtests/runs/{id}/trades |
| `get_equity_curve` | `backtest.py` | `GetEquityCurveInput` | `GetEquityCurveOutput` | GET /api/backtests/runs/{id}/equity |
| `list_backtest_runs` | `backtest.py` | `ListBacktestRunsInput` | `ListBacktestRunsOutput` | GET /api/backtests/runs |

Phase 5A also requires experiment CRUD tools backed by `backend/lab/experiment_registry.py`
(internal calls, not HTTP):

| Tool | Input | Output |
|---|---|---|
| `create_experiment` | `CreateExperimentInput` | `ExperimentRecord` |
| `update_experiment` | `UpdateExperimentInput` | `ExperimentRecord` |
| `get_experiment` | `experiment_id: str` | `ExperimentRecord` |
| `get_lineage` | `experiment_id: str` | `ExperimentLineage` |

### Bedrock tool spec format

Tools are exposed to the Bedrock Converse API as JSON schema specs:

```python
TOOLS = [
    {
        "toolSpec": {
            "name": "submit_backtest",
            "description": "Launch a backtest job for a strategy. Returns job_id.",
            "inputSchema": {
                "json": SubmitBacktestInput.model_json_schema()
            }
        }
    },
    # ... one entry per tool
]
```

---

## 5. Deterministic vs LLM-Driven Classification

| Step | Classification | Owner |
|---|---|---|
| Choose next experiment to run | **LLM** | Supervisor node (Bedrock) |
| Write strategy hypothesis (natural language) | **LLM** | StrategyResearcher |
| Generate `rules_engine` JSON from hypothesis | **LLM + tool call** | StrategyResearcher → `create_strategy` |
| Validate strategy definition | **Deterministic** | `validate_strategy` tool |
| Submit backtest job | **Deterministic** | `submit_backtest` tool |
| Poll job until complete | **Deterministic** | `poll_job` tool (retry loop) |
| Retrieve metrics, trades, equity | **Deterministic** | `get_backtest_run` etc. |
| Diagnose failure modes | **LLM** | BacktestDiagnostics (Bedrock) |
| Select mutation type and params | **LLM** | BacktestDiagnostics → StrategyResearcher |
| Apply mutation to strategy JSON | **Deterministic** | `backend/lab/mutation.py` |
| Compute metric deltas between generations | **Deterministic** | `backend/lab/evaluation.py` |
| Write comparison narrative | **LLM** | GenerationComparator |
| Continue/archive/discard decision | **LLM** | GenerationComparator |
| Robustness battery execution | **Deterministic** | RobustnessReviewer (multi-backtest) |
| Robustness pass/fail adjudication | **Deterministic** | `backend/lab/evaluation.py` gates |
| Lineage dedup check | **Deterministic** | ExperimentLibrarian (Jaccard) |
| Promotion to validated tier | **LLM + human gate** | Supervisor → human_approval_required |

---

## 6. Observability Hooks

All structured log events are emitted via `backend/agents/providers/logging.py` using `loguru`.
Every event includes `trace_id`, `session_id`, `timestamp_utc`, `event_type`.

### Event types

```python
# Node lifecycle
{"event": "node_enter",  "node": str, "trace_id": str, "state_keys": list[str]}
{"event": "node_exit",   "node": str, "trace_id": str, "duration_ms": int, "next_node": str}
{"event": "node_error",  "node": str, "trace_id": str, "error": str}

# LLM calls
{"event": "llm_call",    "model": str, "trace_id": str, "node": str,
 "input_tokens": int, "output_tokens": int, "latency_ms": int,
 "tool_use": bool, "tool_name": str | None}

# Tool calls
{"event": "tool_call",   "tool": str, "trace_id": str, "node": str,
 "input": dict, "output_summary": str, "latency_ms": int, "success": bool}

# State transitions
{"event": "state_update", "trace_id": str, "field": str, "new_value_summary": str}
```

Logs are written to stderr (JSON lines). In Phase 7 production these pipe to CloudWatch.

---

## 7. Risk Register

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | **Framework conflict** — LangGraph not in requirements.txt | HIGH — Stage 2 blocked | Resolve before Stage 2: add `langgraph>=0.2` or switch to Strands and rewrite this doc |
| 2 | **LLM hallucinated strategy JSON** fails validation | MED — wasted backtest cycle | `validate_strategy` tool is always called before `submit_backtest`; errors returned to LLM for retry (max 3) |
| 3 | **Experiment loop diverges** — supervisor picks the same branch forever | MED — infinite cost | `iteration` field in AgentState; hard stop at 10 iterations per session |
| 4 | **Bedrock Converse tool-use deserialization** — tool input JSON doesn't match Pydantic model | LOW-MED | All tool inputs validated with `model.model_validate(tool_input)` before execution; validation errors returned as tool_result error |
| 5 | **Stale job not detected** — polling loop misses FAILED state | LOW | `poll_job` timeout: if `status == "running"` for > 600s, treat as failed; `error_code = "POLL_TIMEOUT"` |

---

## 8. Phased Build Order (Stage 1 Foundation)

Dependencies flow top-to-bottom. Each step is independently testable.

```
Step 1: backend/agents/tools/schemas.py
        └── All Pydantic input/output models (from api-contract-report §6)
        └── No dependencies on graph or LLM

Step 2: backend/agents/tools/client.py
        └── httpx async client with base_url from settings
        └── Depends on: Step 1

Step 3: backend/agents/tools/backtest.py + strategy.py
        └── Tool executor functions — call client, parse with schemas
        └── Depends on: Steps 1, 2

Step 4: backend/agents/providers/bedrock.py
        └── BedrockAdapter: converse(), converse_stream(), extract_tool_use()
        └── Depends on: boto3 (already in requirements.txt)

Step 5: backend/agents/providers/logging.py
        └── Structured event logger (loguru wrapper)
        └── No dependencies

Step 6: backend/agents/state.py
        └── AgentState TypedDict
        └── No dependencies

Step 7: backend/lab/experiment_registry.py
        └── Experiment CRUD via LocalMetadataRepository
        └── Depends on: backend/data/local_metadata.py (existing)

Step 8: backend/agents/supervisor.py
        └── Supervisor node logic (routing only, no LLM in Stage 1)
        └── Depends on: Steps 5, 6

Step 9: backend/agents/graph.py
        └── StateGraph: wire all nodes + conditional edges
        └── Depends on: Steps 6, 7, 8

Step 10: backend/agents/strategy_researcher.py  (PLACEHOLDER — LLM calls stubbed)
         backend/agents/backtest_diagnostics.py (PLACEHOLDER)
         backend/agents/generation_comparator.py (PLACEHOLDER)
         └── Depends on: Steps 3, 4, 5, 6, 9
```

**Stage 1 success definition:** Graph can be imported, instantiated, and invoked with a
test input that routes through supervisor → strategy_researcher (stub) → END without error.
All LLM calls are mocked; all tool calls hit a mock httpx server.

---

## 9. Ownership Boundaries

| Concern | Owner |
|---|---|
| `Experiment` CRUD (create, read, update, promote) | `backend/lab/experiment_registry.py` |
| `Strategy` CRUD | existing `backend/strategies/` + `apps/api/routes/strategies.py` |
| Backtest job launch + polling | existing `apps/api/routes/backtests.py` + `jobs.py` |
| LLM routing decisions | `backend/agents/supervisor.py` |
| Bedrock API calls | `backend/agents/providers/bedrock.py` |
| HTTP tool calls to backend API | `backend/agents/tools/` |
| Experiment evaluation (metric gates) | `backend/lab/evaluation.py` |
| Strategy mutation (deterministic) | `backend/lab/mutation.py` |
| Phase 5 HTTP surfaces | `apps/api/routes/experiments.py` + `research.py` |
| Frontend research views | Phase 6 work — not Phase 5 |
| AWS infrastructure | Phase 7 work — not Phase 5 |
| HMM internals | Phase 2 — do not modify |
| Backtest engine internals | Phase 4 — do not modify |
