# Phase 5C API Contract Specification — Feature Discovery

**Author:** API Contract Engineer
**Date:** 2026-03-15
**Status:** DRAFT — specification only, no implementation code

---

## Overview

Phase 5C adds a Feature Discovery surface to the platform. The agent node
`feature_researcher_node` (to be built in Phase 5C) will run a feature
evaluation loop, score candidate features against an instrument/timeframe
window, and write results to an in-process `FeatureLibrary` backed by
`LocalMetadataRepository`. The four endpoints defined here expose that
discovery flow to callers as an async job and expose the accumulated library
as queryable read endpoints.

This spec is prescriptive. Stage 2 agents implement against it exactly as
written.

---

## Part 1 — Endpoints to Implement

### 1.1  POST /api/features/discover

**Purpose:** Launch a feature discovery run. Creates a JobManager job of type
`FEATURE_DISCOVERY`, fires a background thread that calls
`feature_researcher_node`, and returns immediately.

**Sync/async:** Async — BackgroundTasks (202). Poll via `GET /api/jobs/{job_id}`.

**Request model:** `FeatureDiscoverRequest`

| Field | Type | Required | Notes |
|---|---|---|---|
| `instrument` | `str` | required | e.g. `"EUR_USD"` |
| `timeframe` | `str` | required | e.g. `"H4"` |
| `eval_start` | `str` | required | ISO date string, e.g. `"2023-01-01"` |
| `eval_end` | `str` | required | ISO date string, e.g. `"2024-01-01"` |
| `feature_run_id` | `str \| None` | optional | existing feature run to evaluate against; if `None` the node generates its own |
| `model_id` | `str \| None` | optional | HMM model ID for regime-split evaluation |
| `families` | `list[str]` | optional | default `[]` (all families); filter which feature families to probe |
| `max_candidates` | `int` | optional | default `20`, ge=1, le=100; cap on features evaluated per run |
| `requested_by` | `str` | optional | default `"api"` |

**Validation:**
- `eval_end` must be strictly after `eval_start` — return 422 if violated.
- `instrument` must be a non-empty string — return 422 if blank.
- `timeframe` must be a non-empty string — return 422 if blank.

**Response model:** `JobCreatedResponse` (existing schema — reuse as-is)

```
JobCreatedResponse
  job_id: str
  status: JobStatus          # always "queued" at creation
```

**Status:** 202

**Job lifecycle:**
- `result_ref` on the completed job holds the `discovery_run_id` (a UUID
  assigned at job creation, written to `params_json`).
- `stage_label` values during execution: `"initializing"`, `"loading_features"`,
  `"evaluating_candidates"`, `"scoring"`, `"persisting_library"`.
- On `SUCCEEDED`, evaluated `FeatureSpec` records are queryable via
  `GET /api/features/library`.

**How it triggers `feature_researcher_node`:**

The route handler creates a `JobManager` job (type `FEATURE_DISCOVERY`), assigns
a `discovery_run_id = str(uuid.uuid4())`, then calls `background_tasks.add_task()`
with a `_run_feature_discovery` coroutine. That coroutine calls `job_manager.start()`
on the job, invokes `feature_researcher_node(state)` synchronously inside
`loop.run_in_executor(None, ...)` (matching the `research.py` pattern), collects
`list[dict]` from `final_state["feature_eval_results"]`, upserts each into the
`FeatureLibrary`, then calls `job_manager.succeed(job_id, result_ref=discovery_run_id)`.
On exception: `job_manager.fail(job_id, str(exc), "FEATURE_DISCOVERY_ERROR")`.

The initial state dict passed to `feature_researcher_node` must include at
minimum: `instrument`, `timeframe`, `eval_start`, `eval_end`, `feature_run_id`,
`model_id`, `families`, `max_candidates`, `discovery_run_id`. The exact
`AgentState` extension is specified in the Contract Gaps section (Part 5).

---

### 1.2  GET /api/features/discover/{job_id}

**Purpose:** Retrieve the status and, once complete, the evaluated feature
results produced by a discovery job. This is the type-specific poller for
feature discovery jobs; the universal poller `GET /api/jobs/{job_id}` also works.

**Path parameters:**

| Parameter | Type | Required |
|---|---|---|
| `job_id` | `str` | required |

**Response model:** `FeatureDiscoverJobResponse`

```
FeatureDiscoverJobResponse
  job_id: str
  status: JobStatus
  progress_pct: float
  stage_label: str
  created_at: datetime
  started_at: datetime | None
  completed_at: datetime | None
  error_code: str | None
  error_message: str | None
  discovery_run_id: str | None       # None until SUCCEEDED; sourced from params_json["discovery_run_id"]
  feature_eval_results: list[FeatureEvalResult]   # empty list until SUCCEEDED; populated by reading FeatureLibrary filtered by discovery_run_id
```

**Error responses:**
- `404` if `job_id` does not exist.
- `404` if the job exists but its `job_type` is not `FEATURE_DISCOVERY` — return a
  user-safe message: `"Job '{job_id}' is not a feature discovery job"`.

**Implementation note:** `feature_eval_results` is populated by the route handler
reading `feature_library.list_by_discovery_run(discovery_run_id)` after the job
reaches `SUCCEEDED`. For all other statuses the list is `[]`.

---

### 1.3  GET /api/features/library

**Purpose:** Query the accumulated feature library — all `FeatureSpec` records
that have been evaluated and persisted across all discovery runs.

**Query parameters:**

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `family` | `str \| None` | optional | `None` | filter by feature family string |
| `max_leakage` | `float \| None` | optional | `None` | return only features with `leakage_score <= max_leakage` |
| `min_f_statistic` | `float \| None` | optional | `None` | return only features with `f_statistic >= min_f_statistic` |
| `limit` | `int` | optional | `50` | ge=1, le=200 |

**Response model:** `FeatureLibraryResponse`

```
FeatureLibraryResponse
  features: list[FeatureSpec]
  count: int
```

**Ordering:** newest `discovered_at` first.

---

### 1.4  GET /api/features/library/{name}

**Purpose:** Retrieve a single `FeatureSpec` by its canonical name.

**Path parameters:**

| Parameter | Type | Required |
|---|---|---|
| `name` | `str` | required |

**Response model:** `FeatureSpec`

**Error responses:**
- `404` if no feature with that name exists in the library. Message:
  `"Feature '{name}' not found in library"`.

**Note:** `name` is the canonical feature identifier (e.g. `"rsi_14"`,
`"ema_slope_20"`) — it is the primary key of the feature library store. If
the same feature is evaluated across multiple discovery runs, the record is
upserted (updated in place) rather than duplicated.

---

## Part 2 — Pydantic Schemas (Pydantic v2)

All schemas go in `backend/schemas/requests.py` alongside the existing
Phase 5B schemas. Names are fixed — do not rename them.

```python
# ---------------------------------------------------------------------------
# Features — Phase 5C
# ---------------------------------------------------------------------------

class FeatureDiscoverRequest(BaseModel):
    instrument: str
    timeframe: str
    eval_start: str
    eval_end: str
    feature_run_id: str | None = None
    model_id: str | None = None
    families: list[str] = []
    max_candidates: int = Field(default=20, ge=1, le=100)
    requested_by: str = "api"

    @model_validator(mode="after")
    def _validate_params(self) -> "FeatureDiscoverRequest":
        if not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string")
        if not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string")
        # eval_start and eval_end are ISO strings — compare lexicographically;
        # the service layer parses them before use
        if self.eval_end <= self.eval_start:
            raise ValueError("eval_end must be after eval_start")
        return self


class FeatureEvalResult(BaseModel):
    """Single feature's evaluation result from feature_researcher_node.

    Mirrors the output structure that feature_researcher_node must write into
    AgentState["feature_eval_results"]. All numeric fields are float | None so
    that a feature that could not be scored does not break serialization.
    """
    name: str                              # canonical feature name, e.g. "rsi_14"
    family: str                            # feature family, e.g. "momentum", "volatility", "trend"
    description: str                       # short human-readable description
    f_statistic: float | None              # ANOVA F-statistic across regime buckets; None if not computable
    p_value: float | None                  # p-value for f_statistic; None if not computable
    leakage_score: float | None            # 0.0 = no leakage, 1.0 = fully forward-looking; None if not assessed
    regime_discriminability: float | None  # mean inter-regime distance (cosine or L2); None if no model_id
    correlation_with_returns: float | None # Pearson r against next-bar log return; None if not computable
    evaluation_notes: str                  # free text from node; empty string if none
    discovery_run_id: str                  # UUID of the discovery job that produced this result


class FeatureSpec(BaseModel):
    """Persisted feature record in the feature library.

    Written by FeatureLibrary.upsert(). Combines FeatureEvalResult with
    provenance and persistence metadata.
    """
    id: str                                # UUID assigned at first upsert; stable across re-evaluations
    name: str                              # canonical feature name — primary key for upsert
    family: str
    description: str
    f_statistic: float | None
    p_value: float | None
    leakage_score: float | None
    regime_discriminability: float | None
    correlation_with_returns: float | None
    evaluation_notes: str
    discovery_run_id: str                  # run that last evaluated this feature
    instrument: str                        # instrument used in last evaluation
    timeframe: str                         # timeframe used in last evaluation
    eval_start: str                        # ISO date string
    eval_end: str                          # ISO date string
    discovered_at: str                     # ISO datetime of first insertion
    last_evaluated_at: str                 # ISO datetime of most recent upsert


class FeatureLibraryResponse(BaseModel):
    features: list[FeatureSpec]
    count: int


class FeatureDiscoverJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress_pct: float
    stage_label: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    discovery_run_id: str | None = None
    feature_eval_results: list[FeatureEvalResult] = []
```

---

## Part 3 — BackgroundTasks Pattern Reuse

The pattern in `apps/api/routes/research.py` is reusable as-is. No extension
is needed. Replicate it exactly as follows.

### The canonical pattern (from research.py)

```python
@router.post("/discover", response_model=JobCreatedResponse, status_code=202)
async def trigger_feature_discovery(
    body: FeatureDiscoverRequest,
    background_tasks: BackgroundTasks,
    job_manager: JobManager = Depends(get_job_manager),
    feature_library: FeatureLibrary = Depends(get_feature_library),
) -> JobCreatedResponse:
    discovery_run_id = str(uuid.uuid4())
    job = job_manager.create(
        job_type=JobType.FEATURE_DISCOVERY,
        requested_by=body.requested_by,
        params={
            "instrument": body.instrument,
            "timeframe": body.timeframe,
            "eval_start": body.eval_start,
            "eval_end": body.eval_end,
            "feature_run_id": body.feature_run_id,
            "model_id": body.model_id,
            "families": body.families,
            "max_candidates": body.max_candidates,
            "discovery_run_id": discovery_run_id,
        },
    )
    background_tasks.add_task(
        _run_feature_discovery,
        job_id=job.id,
        discovery_run_id=discovery_run_id,
        body=body,
        job_manager=job_manager,
        feature_library=feature_library,
    )
    return JobCreatedResponse(job_id=job.id, status=job.status)


async def _run_feature_discovery(
    job_id: str,
    discovery_run_id: str,
    body: FeatureDiscoverRequest,
    job_manager: JobManager,
    feature_library: FeatureLibrary,
) -> None:
    job_manager.start(job_id)
    try:
        initial_state = {
            "instrument": body.instrument,
            "timeframe": body.timeframe,
            "eval_start": body.eval_start,
            "eval_end": body.eval_end,
            "feature_run_id": body.feature_run_id,
            "model_id": body.model_id,
            "families": body.families,
            "max_candidates": body.max_candidates,
            "discovery_run_id": discovery_run_id,
            "feature_eval_results": None,
        }
        loop = asyncio.get_event_loop()
        final_state = await loop.run_in_executor(
            None,
            lambda: feature_researcher_node(initial_state),
        )
        eval_results: list[dict] = final_state.get("feature_eval_results") or []
        for result in eval_results:
            feature_library.upsert(
                FeatureEvalResult.model_validate(result),
                instrument=body.instrument,
                timeframe=body.timeframe,
                eval_start=body.eval_start,
                eval_end=body.eval_end,
            )
        job_manager.succeed(job_id, result_ref=discovery_run_id)
    except Exception as exc:
        logger.exception("Feature discovery failed for job %s", job_id)
        job_manager.fail(job_id, str(exc), "FEATURE_DISCOVERY_ERROR")
```

### Correspondence to research.py

| This file | research.py equivalent | Line(s) in research.py |
|---|---|---|
| `job_manager.start(job_id)` | `registry.update_status(..., RUNNING)` | 141 |
| `loop.run_in_executor(None, lambda: feature_researcher_node(state))` | `loop.run_in_executor(None, lambda: graph.invoke(initial_state))` | 145–149 |
| `job_manager.succeed(job_id, result_ref=discovery_run_id)` | `_write_graph_result(...)` then status update | 150 |
| `logger.exception(...)` + `job_manager.fail(...)` | `logger.exception(...)` + `registry.update_status(..., FAILED)` | 151–157 |

**One intentional difference from research.py:** `feature_researcher_node` is
called directly rather than via `build_graph().invoke()`. Feature discovery does
not need the supervisor loop — it is a single-pass evaluation node. If Phase 5C
implementation determines a graph is required, the pattern extends to
`build_feature_graph().invoke(state)` with no API contract change.

---

## Part 4 — Feature Library Storage

### Storage approach

`FeatureSpec` records are persisted via `LocalMetadataRepository` using the
existing `_upsert` / `_get` / `_list` / `_update` / `_store` / `_save`
primitives — the same approach used by `ExperimentRegistry` in
`backend/lab/experiment_registry.py`.

A new class `FeatureLibrary` must be created at `backend/lab/feature_library.py`.
It is the only layer that reads and writes the `"feature_library"` store name.
Routes do not call `metadata_repo._upsert` / `_get` directly.

### Store name

```python
_STORE = "feature_library"
```

This maps to `data/metadata/feature_library.json` on the local filesystem,
consistent with how all other stores resolve (e.g. `data/metadata/experiments.json`,
`data/metadata/job_runs.json`).

### Primary key convention

The feature library is keyed by `name` (the canonical feature identifier), not
by `id`. On first insert, a UUID `id` is assigned and the record is stored under
`store[name] = record`. On subsequent upsert for the same `name`, the record is
updated in place — scores are refreshed, `last_evaluated_at` is bumped, and `id`
and `discovered_at` are preserved.

This departs from the standard `store[record["id"]] = record` convention used
by `_upsert`. `FeatureLibrary` must implement the key logic directly — calling
`metadata_repo._store("feature_library")[name] = record` then
`metadata_repo._save("feature_library")` — rather than delegating to
`metadata_repo._upsert()`. The storage engineer owns this detail; the API
contract only requires that `GET /api/features/library/{name}` returns the
correct current record.

### Required interface on FeatureLibrary

```python
class FeatureLibrary:
    _STORE = "feature_library"

    def __init__(self, metadata_repo: LocalMetadataRepository) -> None: ...

    def upsert(
        self,
        result: FeatureEvalResult,
        instrument: str,
        timeframe: str,
        eval_start: str,
        eval_end: str,
    ) -> FeatureSpec:
        """Insert or update a feature record keyed by name.

        On first insert: assign a new UUID id and set discovered_at = now.
        On subsequent upsert: preserve id and discovered_at; update all score
        fields and set last_evaluated_at = now.
        """
        ...

    def get(self, name: str) -> FeatureSpec:
        """Return the FeatureSpec for the given canonical name.
        Raises KeyError if not found.
        """
        ...

    def list_all(
        self,
        family: str | None = None,
        max_leakage: float | None = None,
        min_f_statistic: float | None = None,
        limit: int = 50,
    ) -> list[FeatureSpec]:
        """Return FeatureSpec records, newest discovered_at first.

        Filters are applied in order: family, max_leakage, min_f_statistic.
        Features with None for a filter target field are excluded when that
        filter is active (i.e. None values do not pass a numeric filter).
        """
        ...

    def list_by_discovery_run(self, discovery_run_id: str) -> list[FeatureEvalResult]:
        """Return all FeatureEvalResult-shaped records from a specific discovery run.

        Used by GET /api/features/discover/{job_id} to populate
        feature_eval_results after SUCCEEDED.
        """
        ...
```

### Dependency injection

Add the following singleton getter to `backend/deps.py`, after the existing
`get_experiment_registry()` function (lines 37–40):

```python
@lru_cache(maxsize=1)
def get_feature_library():
    from backend.lab.feature_library import FeatureLibrary
    return FeatureLibrary(get_metadata_repo())
```

This matches the `get_experiment_registry()` pattern exactly.

---

## Part 5 — Contract Gaps

### Gap 1: `feature_eval_results` is not in `AgentState`

**Current state:** `AgentState` in `backend/agents/state.py` has no
`feature_eval_results` field. The TypedDict defines fields for the strategy
research and backtest loop only.

**Required addition to `AgentState`:**

```python
# Phase 5C — feature discovery artifacts
feature_eval_results: list[dict[str, Any]] | None
# Populated by feature_researcher_node. Each dict validates against FeatureEvalResult.
```

**Required addition to `DEFAULT_STATE()` and `make_default_state()`:**

```python
feature_eval_results=None,
```

`AgentState` uses `total=False` so this is a non-breaking addition.

**Owner:** The agent state engineer owns `state.py`. This spec names the field
and its type; the agent engineer adds it.

---

### Gap 2: `feature_researcher_node` does not exist

**Current state:** The graph in `backend/agents/graph.py` registers three worker
nodes: `strategy_researcher`, `backtest_diagnostics`, `generation_comparator`.
There is no `feature_researcher_node` anywhere in `backend/agents/`.

**Required addition:** A new callable at `backend/agents/feature_researcher.py`:

```python
def feature_researcher_node(state: dict) -> dict:
    """Evaluate feature candidates and return state with feature_eval_results populated."""
    ...
```

The function must:
- Accept a state dict containing at minimum: `instrument`, `timeframe`,
  `eval_start`, `eval_end`, `feature_run_id`, `model_id`, `families`,
  `max_candidates`, `discovery_run_id`.
- Return the same dict with `feature_eval_results` set to a
  `list[dict]` where each dict validates against `FeatureEvalResult`.
- Not mutate the input dict — return a new dict.

The route handler calls this node directly. It is not wired into the
existing supervisor graph — feature discovery is a separate concern from
strategy research and does not need the supervisor loop.

**Owner:** The agent engineer owns `feature_researcher.py`. This spec names
the interface; the agent engineer implements the logic.

---

### Gap 3: `JobType.FEATURE_DISCOVERY` does not exist

**Current state:** `backend/schemas/enums.py` `JobType` contains:
`INGESTION`, `HMM_TRAINING`, `BACKTEST`, `FEATURE_GENERATION`,
`SIGNAL_MATERIALIZE`, `DUKASCOPY_DOWNLOAD`, `RESEARCH`.

There is an existing `FEATURE_GENERATION` value but it refers to the Phase 2
`run_feature_pipeline` job (compute features from bars). That is a different
operation from Phase 5C agent-driven feature discovery. A new distinct value
is required to avoid ambiguity in job polling and in the
`GET /api/features/discover/{job_id}` type guard.

**Required addition:**

```python
FEATURE_DISCOVERY = "FEATURE_DISCOVERY"
```

Do not reuse `FEATURE_GENERATION`. The `GET /api/features/discover/{job_id}`
route guards on `job["job_type"] == JobType.FEATURE_DISCOVERY.value` and must
return 404 for any other type.

---

### Gap 4: `FeatureEvalResult` is not defined anywhere in the codebase

**Current state:** There is no `FeatureEvalResult` schema in
`backend/agents/tools/schemas.py`, `backend/schemas/requests.py`, or
`backend/lab/`.

**Resolution:** `FeatureEvalResult` is defined in this spec (Part 2) and lives
in `backend/schemas/requests.py`. The `feature_researcher_node` implementation
must produce dicts that validate against this schema. Contract direction: the
API spec defines the schema; the agent engineer conforms to it.

---

### Gap 5: `FeatureSpec.name`-keyed upsert departs from all existing stores

All existing metadata stores key records by UUID `id`:
`store[record["id"]] = record`. `FeatureLibrary` keys by `name` for upsert
deduplication. `FeatureLibrary` must implement this deviation internally and
must not work around it by assigning a new UUID on every upsert (which would
create duplicates) or by using `metadata_repo._upsert()` directly (which would
key by `id` and break name-based lookup).

---

## Part 6 — Router Registration in apps/api/main.py

Add the following block immediately after the Phase 5 block (current lines
96–99 of `apps/api/main.py`):

```python
# Phase 5C — feature discovery
from apps.api.routes import features as features_routes
app.include_router(features_routes.router, prefix="/api/features", tags=["features"])
```

**Route file to create:** `apps/api/routes/features.py`

**Router definition inside features.py:**

```python
router = APIRouter()
```

No `prefix` argument on `APIRouter()` — the prefix is applied at registration
in `main.py`. This matches the `experiments.py` pattern (line 27:
`router = APIRouter()` with no prefix) rather than the `research.py` pattern
(which sets `prefix="/api/research"` on the router itself). The `main.py`-level
prefix approach is preferred here for consistency with the majority of existing
routers.

**Reference — existing Phase 5 block in main.py (lines 96–99):**

```python
# Phase 5 — agentic research layer
from apps.api.routes import experiments as experiments_routes, research as research_routes
app.include_router(experiments_routes.router, prefix="/api/experiments", tags=["experiments"])
app.include_router(research_routes.router, tags=["research"])
```

Note that `research_routes.router` has no `prefix=` at registration because
`research.py` sets its own `prefix="/api/research"` on the `APIRouter`. The
new `features.py` must not self-prefix — it must accept the prefix from `main.py`.

---

## Part 7 — Enum Changes Required

**File:** `backend/schemas/enums.py`

Add one value to `JobType`:

```python
class JobType(str, Enum):
    INGESTION = "ingestion"
    HMM_TRAINING = "hmm_training"
    BACKTEST = "backtest"
    FEATURE_GENERATION = "feature_generation"
    SIGNAL_MATERIALIZE = "signal_materialize"
    DUKASCOPY_DOWNLOAD = "dukascopy_download"
    RESEARCH = "RESEARCH"
    FEATURE_DISCOVERY = "FEATURE_DISCOVERY"    # Phase 5C agent-driven discovery
```

No other enum changes are required for Phase 5C.

---

## Part 8 — Files to Create or Modify

| File | Action | Owner |
|---|---|---|
| `apps/api/routes/features.py` | Create | API engineer |
| `backend/lab/feature_library.py` | Create | Storage/lab engineer |
| `backend/agents/feature_researcher.py` | Create | Agent engineer |
| `backend/schemas/requests.py` | Add 5 schemas (Part 2) | API engineer |
| `backend/schemas/enums.py` | Add `FEATURE_DISCOVERY` to `JobType` | API engineer |
| `backend/agents/state.py` | Add `feature_eval_results` field | Agent engineer |
| `backend/deps.py` | Add `get_feature_library()` | API engineer |
| `apps/api/main.py` | Register features router | API engineer |

---

## Part 9 — Error Response Conventions

All error responses follow the existing FastAPI `HTTPException` convention.
No new error response schema is introduced.

| Condition | Status | Detail format |
|---|---|---|
| Request body fails Pydantic validation | 422 | FastAPI default (field-level errors) |
| `job_id` not found | 404 | `"Job '{job_id}' not found"` |
| `job_id` exists but `job_type != FEATURE_DISCOVERY` | 404 | `"Job '{job_id}' is not a feature discovery job"` |
| `name` not found in library | 404 | `"Feature '{name}' not found in library"` |
| Node raises unhandled exception | job status `FAILED` | user sees `error_message` on job record; full traceback logged via `logger.exception` |

The last row is the critical boundary: the route layer must never surface a
raw Python traceback to the caller. `job_manager.fail(job_id, str(exc),
"FEATURE_DISCOVERY_ERROR")` is the user-facing surface; `logger.exception(...)`
preserves the internal detail. This matches `research.py` lines 151–157 exactly.
