# Phase 5B API Contract Specification

**Author:** API Contract Engineer (Agent 2)
**Date:** 2026-03-15
**Status:** DRAFT — mapping only, no implementation

---

## Part 1 — Current API Surface Audit

### 1. How backtest jobs are currently submitted

**Endpoint:** `POST /api/backtests/jobs`
**Response status:** 202
**Response model:** `JobCreatedResponse`

`BacktestJobRequest` fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `strategy_id` | `str \| None` | conditional | either this or `inline_strategy` must be present |
| `inline_strategy` | `dict[str, Any] \| None` | conditional | bare definition dict |
| `instrument` | `str` | required | e.g. `"EUR_USD"` |
| `timeframe` | `Timeframe` (enum) | required | e.g. `"H4"` |
| `test_start` | `datetime` | required | |
| `test_end` | `datetime` | required | |
| `spread_pips` | `float` | optional | default `2.0` |
| `commission_per_unit` | `float` | optional | default `0.0` |
| `slippage_pips` | `float` | optional | default `0.5` |
| `pip_size` | `float` | optional | default `0.0001`; use `0.01` for JPY pairs |
| `feature_run_id` | `str \| None` | optional | |
| `model_id` | `str \| None` | optional | HMM model for regime label join |

A `model_validator` rejects requests where both `strategy_id` and `inline_strategy` are `None` with a 422.

`JobCreatedResponse` fields:

| Field | Type |
|---|---|
| `job_id` | `str` |
| `status` | `JobStatus` (enum) |

---

### 2. How jobs are polled

**Universal cross-type poller:** `GET /api/jobs/{job_id}`
**Type-specific poller:** `GET /api/backtests/jobs/{job_id}`

Both return `JobResponse`:

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | |
| `job_type` | `str` | `"BACKTEST"`, `"INGESTION"`, etc. |
| `status` | `JobStatus` | `QUEUED \| RUNNING \| SUCCEEDED \| FAILED \| CANCELLED` |
| `progress_pct` | `float` | 0.0–100.0 |
| `stage_label` | `str` | human-readable current stage |
| `requested_by` | `str` | |
| `created_at` | `datetime` | |
| `started_at` | `datetime \| None` | |
| `completed_at` | `datetime \| None` | |
| `error_code` | `str \| None` | |
| `error_message` | `str \| None` | user-safe message |
| `params_json` | `dict[str, Any]` | job input params snapshot |
| `result_ref` | `str \| None` | on SUCCEEDED: the `backtest_run_id` |
| `logs_ref` | `str \| None` | |

Polling convention: callers should use `GET /api/jobs/{job_id}` as the universal poller. The type-specific endpoint is equivalent but exists for discoverability.

---

### 3. Backtest result artifacts

After a job reaches `SUCCEEDED`, the following are accessible:

| Resource | Endpoint | Key detail |
|---|---|---|
| Run summary + metrics | `GET /api/backtests/runs/{run_id}` | `result_ref` on the job response IS the `run_id` |
| Trade log | `GET /api/backtests/runs/{run_id}/trades` | |
| Equity + drawdown curve | `GET /api/backtests/runs/{run_id}/equity` | artifact at `backtests/{run_id}/equity.json` |

The artifact key pattern is `backtests/{backtest_run_id}/equity.json`. The `run_id` is obtained from `JobResponse.result_ref` once the job is in `SUCCEEDED` state.

---

### 4. Strategy CRUD endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/strategies` | Create strategy (201). Returns `StrategyResponse`. |
| `GET` | `/api/strategies` | List all strategies. Returns `StrategyListResponse`. |
| `GET` | `/api/strategies/{strategy_id}` | Get single strategy. Returns `StrategyResponse`. |
| `POST` | `/api/strategies/{strategy_id}/validate` | Validate `definition_json`. Returns `StrategyValidateResponse`. |

**Writable fields at creation** (`StrategyCreateRequest`):

| Field | Type | Required |
|---|---|---|
| `name` | `str` | required |
| `description` | `str` | optional, default `""` |
| `strategy_type` | `StrategyType` (enum) | required |
| `definition_json` | `dict[str, Any]` | optional, default `{}` |
| `tags` | `list[str]` | optional, default `[]` |

**There is no PATCH or PUT on strategies.** There is no endpoint to update `name`, `description`, `tags`, or `definition_json` after creation. There is no endpoint to toggle `active_flag`. The strategy record is effectively immutable after `POST /api/strategies`. The `version` field is set at creation and is never incremented through the API.

---

### 5. Existing experiment or research concepts in the backend

**None.** There is no `Experiment`, `ResearchSession`, or `ResearchRun` model, table, route, or service anywhere in the current backend. The agent layer in `backend/agents/` uses `experiment_id` as a field on `AgentState`, but this is an in-memory TypedDict field with no persistence backing it. There is no metadata repository method for experiments, no artifact key convention for experiments, and no API route under any prefix.

`AgentResearchRequest` and `AgentSessionResponse` schemas exist in `backend/schemas/requests.py` but no route currently uses them — they are schema stubs only.

---

## Part 2 — Phase 5B Endpoint Specifications

### Design constraints

- All new endpoints follow the `thin route, service-owned logic` pattern established by existing routes.
- All async long-running flows use the existing `JobManager` state machine (`QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED`) and `GET /api/jobs/{job_id}` as the universal poller.
- No new job type is invented unless strictly necessary. Research runs map to a new `JobType.RESEARCH` enum value.
- Experiment records are persisted through `MetadataRepository` (new methods required, but storage implementation is not owned by this spec).
- Field names use snake_case throughout.
- `status` fields on experiments use a dedicated `ExperimentStatus` enum, not reusing `JobStatus`, because experiments are long-lived containers that outlive any individual job.

---

### Enum additions required

```
ExperimentStatus: "active" | "paused" | "completed" | "archived"
JobType:          add "RESEARCH" to existing enum
```

---

### Endpoint 1 — GET /api/experiments

**Purpose:** List all experiment records, newest first.
**Sync/async:** Synchronous read.
**Agent consumer:** Not consumed by an agent node; used by the frontend and by agent tools that enumerate prior experiments.

**Request:**

| Parameter | Type | Location | Required | Notes |
|---|---|---|---|---|
| `limit` | `int` | query | optional | default `20` |
| `status` | `str \| None` | query | optional | filter by `ExperimentStatus` value |

**Response model: `ExperimentListResponse`**

```
ExperimentListResponse
  experiments: list[ExperimentRecord]
  count: int
```

```
ExperimentRecord
  id: str                          # UUID
  name: str                        # human label, e.g. "EUR_USD H4 seed exploration"
  description: str                 # free text
  instrument: str                  # e.g. "EUR_USD"
  timeframe: str                   # e.g. "H4"
  test_start: datetime
  test_end: datetime
  model_id: str | None             # HMM model scoped to this experiment
  feature_run_id: str | None       # feature run scoped to this experiment
  status: ExperimentStatus         # "active" | "paused" | "completed" | "archived"
  created_at: datetime
  updated_at: datetime
  requested_by: str
  generation_count: int            # how many research iterations have run
  best_strategy_id: str | None     # strategy_id of highest-scoring candidate so far
  best_backtest_run_id: str | None # backtest run ID of the best result
  tags: list[str]
```

---

### Endpoint 2 — GET /api/experiments/{id}

**Purpose:** Fetch a single experiment by ID including its iteration history.
**Sync/async:** Synchronous read.
**Agent consumer:** `generation_comparator` node reads this to compare the current candidate against prior generations.

**Path parameters:**

| Parameter | Type | Required |
|---|---|---|
| `id` | `str` | required |

**Response model: `ExperimentDetailResponse`**

```
ExperimentDetailResponse
  experiment: ExperimentRecord     # full record (same shape as in list)
  iterations: list[ExperimentIteration]
```

```
ExperimentIteration
  id: str                          # UUID
  experiment_id: str
  generation: int                  # 0 = seed; increments per mutation cycle
  strategy_id: str | None
  backtest_run_id: str | None
  hypothesis: str | None           # LLM-generated rationale for this iteration
  mutation_plan: str | None        # LLM description of what changed from prior gen
  diagnosis_summary: str | None    # diagnostics agent output
  comparison_recommendation: str | None  # "continue" | "archive" | "discard"
  discard: bool | None
  created_at: datetime
  completed_at: datetime | None
```

**Error responses:**

- `404` if experiment not found

---

### Endpoint 3 — POST /api/experiments

**Purpose:** Create a new experiment record. Does not start a research run. The experiment is a container; research runs are started separately via `POST /api/research/run`.
**Sync/async:** Synchronous write (201).
**Agent consumer:** Not consumed by an agent node directly. Created by the API caller (user or orchestrator) before the first research run.

**Request model: `ExperimentCreateRequest`**

```
ExperimentCreateRequest
  name: str                        # required
  description: str                 # optional, default ""
  instrument: str                  # required, e.g. "EUR_USD"
  timeframe: str                   # required, e.g. "H4"
  test_start: datetime             # required
  test_end: datetime               # required
  model_id: str | None             # optional
  feature_run_id: str | None       # optional
  tags: list[str]                  # optional, default []
  requested_by: str                # optional, default "system"
```

**Response model: `ExperimentResponse`**

```
ExperimentResponse
  experiment: ExperimentRecord     # newly created record; status = "active"
```

**Validation:**

- `test_end` must be after `test_start` — return 422 if violated.
- `instrument` must be a non-empty string — return 422 if blank.
- `timeframe` must be a non-empty string — return 422 if blank.

---

### Endpoint 4 — PATCH /api/experiments/{id}/status

**Purpose:** Transition an experiment's status. The only writable field via PATCH is `status`. All other experiment fields are immutable after creation.
**Sync/async:** Synchronous write (200).
**Agent consumer:** Not consumed by agent nodes. Used by the orchestrator or user to pause, complete, or archive an experiment.

**Path parameters:**

| Parameter | Type | Required |
|---|---|---|
| `id` | `str` | required |

**Request model: `ExperimentStatusUpdateRequest`**

```
ExperimentStatusUpdateRequest
  status: ExperimentStatus         # required; one of "active" | "paused" | "completed" | "archived"
```

**Response model: `ExperimentResponse`**

```
ExperimentResponse
  experiment: ExperimentRecord     # updated record
```

**Validation / error responses:**

- `404` if experiment not found.
- `422` if `status` value is not a valid `ExperimentStatus`.
- `409` if the transition is not permitted. Permitted transitions:
  - `active` → `paused`, `completed`, `archived`
  - `paused` → `active`, `archived`
  - `completed` → `archived`
  - `archived` → no transitions allowed (terminal)

---

### Endpoint 5 — POST /api/research/run

**Purpose:** Launch a new research iteration against an existing experiment. This is the async entry point: it creates a `JobType.RESEARCH` job, fires a background thread that invokes the LangGraph graph, and returns immediately with a `job_id`. The graph runs `supervisor → strategy_researcher → backtest_diagnostics` (and optionally `generation_comparator`) as a single synchronous invocation within the background thread.
**Sync/async:** Async job (202). Poller: `GET /api/jobs/{job_id}`.
**Agent consumer:** This endpoint IS the entry point for the graph. The route handler invokes `build_graph().invoke(initial_state)` inside the background thread.

**Request model: `ResearchRunRequest`**

```
ResearchRunRequest
  experiment_id: str               # required; must reference an existing active experiment
  task: str                        # required; "generate_seed" | "mutate" | "review"
  parent_iteration_id: str | None  # optional; links to a prior ExperimentIteration for mutation lineage
  requested_by: str                # optional, default "system"
  max_iterations: int              # optional, default 3; caps the graph's internal loop for this run
```

**Response model: `JobCreatedResponse`** (reuse existing schema)

```
JobCreatedResponse
  job_id: str
  status: JobStatus                # always "QUEUED" at creation
```

**Validation / error responses:**

- `404` if `experiment_id` does not reference an existing experiment.
- `422` if `task` is not one of `"generate_seed"`, `"mutate"`, `"review"`.
- `409` if the experiment's `status` is `"archived"` or `"completed"` — research cannot be started against a terminal experiment.

**Job lifecycle notes:**

- `result_ref` on the completed job holds the `experiment_id` (not a backtest run ID, since a research run may produce multiple iterations).
- `stage_label` values during execution: `"initializing"`, `"generating_strategy"`, `"running_backtest"`, `"diagnosing"`, `"comparing"`, `"persisting"`.
- On `SUCCEEDED`, the new `ExperimentIteration` records are queryable via `GET /api/experiments/{id}`.

---

### Endpoint 6 — GET /api/research/{experiment_id}/status

**Purpose:** Return a lightweight status snapshot for the most recent active research job against an experiment. This is a convenience endpoint for polling the "is there a research run in progress?" question without knowing the `job_id`. The frontend and orchestrator use this when they want to know whether to show a spinner on a given experiment.
**Sync/async:** Synchronous read.
**Agent consumer:** Not consumed by agent nodes. Frontend/orchestrator facing.

**Path parameters:**

| Parameter | Type | Required |
|---|---|---|
| `experiment_id` | `str` | required |

**Response model: `ResearchStatusResponse`**

```
ResearchStatusResponse
  experiment_id: str
  active_job: ActiveResearchJob | None   # None if no in-progress job exists
  last_completed_job: CompletedResearchJob | None
  iteration_count: int                   # total iterations run against this experiment
```

```
ActiveResearchJob
  job_id: str
  status: JobStatus                      # QUEUED | RUNNING
  progress_pct: float
  stage_label: str
  started_at: datetime | None
  task: str                              # "generate_seed" | "mutate" | "review"
```

```
CompletedResearchJob
  job_id: str
  status: JobStatus                      # SUCCEEDED | FAILED | CANCELLED
  completed_at: datetime
  task: str
  error_message: str | None              # None unless FAILED
```

**Error responses:**

- `404` if `experiment_id` does not reference an existing experiment.

---

## Part 3 — Schema additions required in backend/schemas/requests.py

The following schemas must be added. Names are fixed — implementation agents must use them exactly.

```
ExperimentRecord              (shared inner model)
ExperimentIteration           (shared inner model)
ExperimentListResponse
ExperimentDetailResponse
ExperimentCreateRequest
ExperimentResponse
ExperimentStatusUpdateRequest
ResearchRunRequest
ResearchStatusResponse
ActiveResearchJob             (inner model)
CompletedResearchJob          (inner model)
```

`JobCreatedResponse` is already defined and is reused for `POST /api/research/run`.

---

## Part 4 — Enum additions required in backend/schemas/enums.py

```python
class ExperimentStatus(str, Enum):
    ACTIVE    = "active"
    PAUSED    = "paused"
    COMPLETED = "completed"
    ARCHIVED  = "archived"
```

Add `RESEARCH = "RESEARCH"` to the existing `JobType` enum.

---

## Part 5 — Router registration in apps/api/main.py

Two new routers must be registered under Phase 5:

```python
# Phase 5
from apps.api.routes import experiments as experiments_routes, research as research_routes
app.include_router(experiments_routes.router, prefix="/api/experiments", tags=["experiments"])
app.include_router(research_routes.router, prefix="/api/research", tags=["research"])
```

Route files to create:
- `apps/api/routes/experiments.py`
- `apps/api/routes/research.py`

---

## Part 6 — Agent node to endpoint mapping

| Agent node | Endpoints consumed |
|---|---|
| `strategy_researcher` | `POST /api/strategies`, `POST /api/strategies/{id}/validate` |
| `backtest_diagnostics` | `POST /api/backtests/jobs`, `GET /api/jobs/{job_id}`, `GET /api/backtests/runs/{run_id}`, `GET /api/backtests/runs/{run_id}/trades` |
| `generation_comparator` | `GET /api/experiments/{id}` (reads prior iterations) |
| `supervisor` | No direct API calls; reads/writes `AgentState` only |
| Research route handler | `POST /api/research/run` (entry point that fires `build_graph().invoke()`) |

---

## Part 7 — Open decisions (must be resolved before implementation)

1. **MetadataRepository methods.** The following new methods are needed on `MetadataRepository` (and `LocalMetadataRepository`). The storage engineer owns the implementation; this spec names them:
   - `save_experiment(experiment: dict) -> None`
   - `get_experiment(experiment_id: str) -> dict | None`
   - `list_experiments(status: str | None, limit: int) -> list[dict]`
   - `update_experiment(experiment_id: str, patch: dict) -> None`
   - `save_experiment_iteration(iteration: dict) -> None`
   - `list_experiment_iterations(experiment_id: str) -> list[dict]`
   - `list_research_jobs_for_experiment(experiment_id: str, limit: int) -> list[dict]`

2. **Graph invocation mode.** `POST /api/research/run` fires `build_graph().invoke(state)` synchronously inside a background thread, matching the existing backtest job pattern. If the graph is slow (multiple LLM calls), callers must tolerate the job staying in `RUNNING` for extended periods. The `GET /api/jobs/{job_id}` polling contract handles this correctly already.

3. **ExperimentIteration persistence timing.** Iterations should be written to the metadata store inside the background thread as each LangGraph graph node completes (progressive persistence), not only at the end. This ensures that if the job fails mid-graph, partial iteration data is recoverable. This is a storage engineer concern but affects the research job runner design.

4. **`best_strategy_id` / `best_backtest_run_id` promotion.** The `generation_comparator` node is responsible for determining the best result. The research job runner should update the experiment record after `generation_comparator` runs. The promotion logic (which metric wins) is owned by the comparator agent, not this spec.
