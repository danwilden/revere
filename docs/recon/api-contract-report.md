# API Contract Reconnaissance Report
**Date:** 2026-03-15
**Scope:** All routes in `apps/api/routes/`, all schemas in `backend/schemas/`, job internals in `backend/jobs/`
**Purpose:** Pre-Phase 5 (Agentic Layer) contract audit. Research only.

---

## 1. Complete Endpoint Inventory

### Router Prefix Registrations (`apps/api/main.py`)

| Router file | Prefix | Tags |
|---|---|---|
| `ingestion.py` | `/api/ingestion` | ingestion |
| `instruments.py` | `/api/instruments` | instruments |
| `market_data.py` | `/api/market-data` | market-data |
| `models.py` | `/api/models` | models |
| `signals.py` | `/api/signals` | signals |
| `strategies.py` | `/api/strategies` | strategies |
| `backtests.py` | `/api/backtests` | backtests |
| `jobs.py` | `/api/jobs` | jobs |
| `dukascopy.py` | `/api/dukascopy` | dukascopy |

Also: `GET /health` (inline in main.py) — raw dict `{status, environment, timestamp}`, no `response_model`.

---

### Endpoint Table

| Method | Full path | Request schema | Response schema | Status codes |
|---|---|---|---|---|
| GET | /health | — | none | 200 |
| POST | /api/ingestion/jobs | `IngestionJobRequest` | `JobCreatedResponse` | 202 |
| GET | /api/ingestion/jobs/{job_id} | — | **none** | 200, 404 |
| GET | /api/ingestion/jobs | limit: int=20 | **none** | 200 |
| GET | /api/instruments | defaults_only: bool=False | **none** | 200 |
| GET | /api/instruments/defaults | — | **none** | 200 |
| GET | /api/market-data/ranges | instrument?: str | **none** | 200, 404 |
| GET | /api/market-data/bars | instrument, timeframe, start, end, limit | **none** | 200, 400 |
| POST | /api/models/hmm/jobs | `HMMTrainingRequest` | `JobCreatedResponse` | 202 |
| GET | /api/models/hmm/jobs/{job_id} | — | **none** | 200, 404 |
| GET | /api/models/hmm | — | **none** | 200 |
| GET | /api/models/hmm/{model_id} | — | **none** | 200, 404 |
| POST | /api/models/hmm/{model_id}/label | `LabelMapUpdateRequest` | **none** | 200, 400, 404 |
| POST | /api/signals | `SignalCreateRequest` | **none** | 201, 400 |
| GET | /api/signals | — | **none** | 200 |
| GET | /api/signals/{signal_id} | — | **none** | 200, 404 |
| POST | /api/signals/{signal_id}/materialize | `SignalMaterializeRequest` | **none** | 200, 400, 404, 501 |
| POST | /api/strategies | `StrategyCreateRequest` | **none** | 201 |
| GET | /api/strategies | — | **none** | 200 |
| GET | /api/strategies/{strategy_id} | — | **none** | 200, 404 |
| POST | /api/strategies/{strategy_id}/validate | `StrategyValidateRequest` | **none** | 200 |
| POST | /api/backtests/jobs | `BacktestJobRequest` | `JobCreatedResponse` | 202, 422 |
| GET | /api/backtests/jobs/{job_id} | — | `JobResponse` | 200, 404 |
| GET | /api/backtests/jobs | limit: int=20 | `BacktestJobListResponse` | 200 |
| GET | /api/backtests/runs/{run_id} | — | `BacktestRunSummaryResponse` | 200, 404 |
| GET | /api/backtests/runs/{run_id}/trades | — | `BacktestTradesResponse` | 200, 404 |
| GET | /api/backtests/runs/{run_id}/equity | — | `BacktestEquityResponse` | 200, 404 |
| GET | /api/backtests/runs | limit: int=20 | `BacktestRunListResponse` | 200 |
| GET | /api/jobs/{job_id} | — | `JobResponse` | 200, 404 |
| POST | /api/jobs/{job_id}/cancel | — | `JobResponse` | 200, 404, 409 |
| POST | /api/dukascopy/jobs | `DukascopyDownloadRequest` | `JobCreatedResponse` | 202 |
| GET | /api/dukascopy/jobs/{job_id} | — | **none** | 200, 404 |
| GET | /api/dukascopy/jobs | limit: int=20 | **none** | 200 |

---

### Key Request Schemas

**`IngestionJobRequest`:** `instruments: list[str]`, `source: DataSource`, `start_date: datetime`, `end_date: datetime`

**`HMMTrainingRequest`:** `instrument: str`, `timeframe: Timeframe`, `train_start: datetime`, `train_end: datetime`, `num_states: int = 7`, `feature_set_name: str = "default_v1"`

**`LabelMapUpdateRequest`:** `label_map: dict[str, str]` (keys are string-integer state IDs)

**`SignalCreateRequest`:** `name: str`, `signal_type: SignalType`, `definition_json: dict = {}`, `source_model_id: str | None`

**`SignalMaterializeRequest`:** `instrument: str`, `timeframe: Timeframe`, `start_date: datetime`, `end_date: datetime`

**`StrategyCreateRequest`:** `name: str`, `description: str = ""`, `strategy_type: StrategyType`, `definition_json: dict = {}`, `tags: list[str] = []`

**`StrategyValidateRequest`:** `definition_json: dict`, `strategy_type: StrategyType`

**`BacktestJobRequest`:** `strategy_id: str | None`, `inline_strategy: dict | None`, `instrument: str`, `timeframe: Timeframe`, `test_start: datetime`, `test_end: datetime`, `spread_pips: float = 2.0`, `commission_per_unit: float = 0.0`, `slippage_pips: float = 0.5`, `pip_size: float = 0.0001`, `feature_run_id: str | None`, `model_id: str | None`. Model validator raises 422 if both `strategy_id` and `inline_strategy` are null.

**`DukascopyDownloadRequest`:** `instruments: list[str]`, `start_date: datetime`, `end_date: datetime`

### Key Response Schemas (where `response_model=` is declared)

**`JobCreatedResponse`:** `job_id: str`, `status: JobStatus`

**`JobResponse`:** `id`, `job_type`, `status`, `progress_pct`, `stage_label`, `requested_by`, `created_at`, `started_at?`, `completed_at?`, `error_code?`, `error_message?`, `params_json`, `result_ref?`, `logs_ref?`

**`BacktestEquityResponse`:** `run_id: str`, `equity_curve: list[EquityPoint]`
**`EquityPoint`:** `timestamp: str`, `equity: float`, `drawdown: float`

**`BacktestRunSummaryResponse`:** `run: dict[str, Any]` (untyped inner), `metrics: list[dict[str, Any]]` (untyped inner)

**`BacktestTradesResponse`:** `run_id: str`, `trades: list[dict[str, Any]]` (untyped), `count: int`

**`BacktestJobListResponse`:** `jobs: list[dict[str, Any]]` (untyped), `count: int`

**`BacktestRunListResponse`:** `runs: list[dict[str, Any]]` (untyped), `count: int`

---

## 2. Job Contract Mechanics

### `JobRun` Model Fields

```
id: str (UUID)
job_type: JobType
status: JobStatus (QUEUED at creation)
progress_pct: float
stage_label: str
requested_by: str (default "system")
created_at: datetime
started_at: datetime | None
completed_at: datetime | None
error_code: str | None
error_message: str | None
params_json: dict[str, Any]
result_ref: str | None   # backtest: backtest_run_id; HMM: model_id
logs_ref: str | None
```

### State Machine

```
QUEUED → RUNNING → SUCCEEDED
                 → FAILED
                 → CANCELLED  (only from QUEUED or RUNNING; 409 guard in cancel route)
```

`create()` returns a `JobRun` Pydantic object. All mutating methods (`start`, `progress`, `succeed`, `fail`, `cancel`) return None. `get()` and `list()` return raw dicts from the metadata store.

### Backtest Job Stages (progress checkpoints)

0% QUEUED → `start` → 20% loading_data → 40% running_engine → 80% persisting_results → 100% SUCCEEDED
On succeed: `result_ref = backtest_run_id`

### HMM Training Job Stages

0% QUEUED → `start` → 10% Computing features → 30% Training HMM model → 80% Applying semantic labels → 100% SUCCEEDED
On succeed: `result_ref = model_id`

### Canonical Polling Endpoint

`GET /api/jobs/{job_id}` — use this for all job types. It has the only `response_model=JobResponse` annotation across all type-specific pollers.

---

## 3. Result Artifact Paths

**Backtest equity artifact**
- Key: `backtests/{backtest_run_id}/equity.json`
- Format: JSON array of `{timestamp: str, equity: float, drawdown: float}` (one entry per bar)
- Written by: `run_backtest_job()` via `artifact_repo.save()`
- Retrieved via: `GET /api/backtests/runs/{run_id}/equity`
- Also stored in: `BacktestRun.result_ref` (the equity key) and `JobRun.result_ref` (the backtest_run_id)

**HMM model artifact**
- Key: `models/hmm/{model_id}.joblib`
- Format: `joblib.dump()` of `{"model": GaussianHMM, "feature_cols": list[str]}`
- Written by: `train_hmm()` inside `backend/models/hmm_regime.py`
- No direct download endpoint — consumed server-side by `load_model_artifact()`
- Metadata (including `artifact_ref`) accessible via `GET /api/models/hmm/{model_id}`

**Regime labels** — stored in DuckDB `regime_labels` table, not as artifacts.

---

## 4. Schema Gaps

### 4a. Routes missing `response_model=` (21 endpoints)

Every route outside the backtests/jobs router is missing `response_model=`. Full list: `/health`, all ingestion GET endpoints, all instruments endpoints, all market-data endpoints, all models GET endpoints, the label POST, all signals endpoints, all strategies endpoints, both dukascopy GET endpoints.

### 4b. Typed response models with untyped inner fields

`BacktestRunSummaryResponse.run`, `BacktestRunSummaryResponse.metrics`, `BacktestTradesResponse.trades`, `BacktestJobListResponse.jobs`, `BacktestRunListResponse.runs` — all are `dict[str, Any]` or `list[dict]`, giving OpenAPI no schema for the contained objects.

### 4c. Inconsistent error shapes

- 422 errors (Pydantic failures): `{detail: list[{loc, msg, type}]}`
- 404/400/409 errors (HTTPException): `{detail: str}`
- `POST /api/models/hmm/{model_id}/label` raises manual `HTTPException(400)` for label map validation that would be 422 if handled in Pydantic — same failure, different status depending on catch location.
- No global error envelope or error middleware.

### 4d. Per-type job pollers untyped

`GET /api/ingestion/jobs/{id}`, `GET /api/models/hmm/jobs/{id}`, `GET /api/dukascopy/jobs/{id}` all return the same data as `GET /api/jobs/{id}` but lack `response_model=JobResponse`.

### 4e. `market-data/bars` timeframe as raw string

Accepted as `str = "M1"` with manual `Timeframe(timeframe.upper())` inside the handler — mistyped values produce 400 instead of 422, and no OpenAPI enum constraint is generated.

### 4f. Dead agent schemas

`AgentResearchRequest` and `AgentSessionResponse` in `backend/schemas/requests.py` have no routes. Phase 5 pre-stubs already exist.

---

## 5. Recommended Phase 5 Tool Set

| # | Tool name | Method + Path | Purpose |
|---|---|---|---|
| 1 | `list_strategies` | GET `/api/strategies` | Discover persisted named strategies |
| 2 | `create_strategy` | POST `/api/strategies` | Persist a new rules-engine or Python strategy |
| 3 | `validate_strategy` | POST `/api/strategies/{strategy_id}/validate` | Pre-flight check before backtest submission |
| 4 | `submit_backtest` | POST `/api/backtests/jobs` | Launch a backtest; receive job_id |
| 5 | `poll_job` | GET `/api/jobs/{job_id}` | Universal poller; extract `result_ref` (= `run_id`) on SUCCEEDED |
| 6 | `get_backtest_run` | GET `/api/backtests/runs/{run_id}` | Full run summary + all performance metrics |
| 7 | `get_backtest_trades` | GET `/api/backtests/runs/{run_id}/trades` | Complete trade log for analysis |
| 8 | `get_equity_curve` | GET `/api/backtests/runs/{run_id}/equity` | Bar-by-bar equity and drawdown series |
| 9 | `list_backtest_runs` | GET `/api/backtests/runs` | Browse previous runs for comparison |

**job_id → run_id resolution:** After `submit_backtest` returns `job_id`, poll `poll_job` until `status == "succeeded"`. `result_ref` in the response contains the `backtest_run_id`. Pass this to tools 6, 7, and 8.

---

## 6. Typed Python Client Models for Phase 5 Agent

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, model_validator


class Timeframe(str, Enum):
    M1 = "M1"; H1 = "H1"; H4 = "H4"; D = "D"

class StrategyType(str, Enum):
    PYTHON = "python"; RULES_ENGINE = "rules_engine"; HYBRID = "hybrid"

class JobStatus(str, Enum):
    QUEUED = "queued"; RUNNING = "running"; SUCCEEDED = "succeeded"
    FAILED = "failed"; CANCELLED = "cancelled"

class TradeSide(str, Enum):
    LONG = "long"; SHORT = "short"


# ── Tool 1: list_strategies ───────────────────────────────────────────────────
class StrategyRecord(BaseModel):
    id: str
    name: str
    description: str
    strategy_type: StrategyType
    definition_json: dict[str, Any]
    version: int
    created_at: datetime
    active_flag: bool
    tags: list[str]
# Output: list[StrategyRecord]


# ── Tool 2: create_strategy ───────────────────────────────────────────────────
class CreateStrategyInput(BaseModel):
    name: str
    description: str = ""
    strategy_type: StrategyType
    definition_json: dict[str, Any] = {}
    tags: list[str] = []
# Output: StrategyRecord


# ── Tool 3: validate_strategy ─────────────────────────────────────────────────
class ValidateStrategyInput(BaseModel):
    strategy_id: str                    # path param
    definition_json: dict[str, Any]
    strategy_type: StrategyType

class ValidateStrategyOutput(BaseModel):
    valid: bool
    errors: list[str]


# ── Tool 4: submit_backtest ───────────────────────────────────────────────────
class SubmitBacktestInput(BaseModel):
    strategy_id: str | None = None
    inline_strategy: dict[str, Any] | None = None
    instrument: str
    timeframe: Timeframe
    test_start: datetime
    test_end: datetime
    spread_pips: float = 2.0
    commission_per_unit: float = 0.0
    slippage_pips: float = 0.5
    pip_size: float = 0.0001  # use 0.01 for JPY pairs
    feature_run_id: str | None = None
    model_id: str | None = None

    @model_validator(mode="after")
    def _require_strategy(self) -> "SubmitBacktestInput":
        if self.strategy_id is None and self.inline_strategy is None:
            raise ValueError("Either strategy_id or inline_strategy must be provided")
        return self

class SubmitBacktestOutput(BaseModel):
    job_id: str
    status: JobStatus


# ── Tool 5: poll_job ─────────────────────────────────────────────────────────
class PollJobInput(BaseModel):
    job_id: str  # path param

class PollJobOutput(BaseModel):
    id: str
    job_type: str
    status: JobStatus
    progress_pct: float
    stage_label: str
    requested_by: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    params_json: dict[str, Any]
    result_ref: str | None = None   # SUCCEEDED backtest: this is backtest_run_id
    logs_ref: str | None = None


# ── Tool 6: get_backtest_run ──────────────────────────────────────────────────
class GetBacktestRunInput(BaseModel):
    run_id: str  # path param

class BacktestRunDetail(BaseModel):
    id: str
    job_id: str | None = None
    strategy_id: str | None = None
    inline_definition: dict[str, Any] | None = None
    instrument_id: str
    timeframe: str
    test_start: datetime
    test_end: datetime
    parameters_json: dict[str, Any]
    cost_model_json: dict[str, Any]
    status: JobStatus
    created_at: datetime
    result_ref: str | None = None
    oracle_regime_labels: bool

class PerformanceMetric(BaseModel):
    id: str
    backtest_run_id: str
    metric_name: str
    metric_value: float | None   # None = undefined (e.g. Sharpe with zero trades)
    segment_type: str            # "overall" or "regime"
    segment_key: str             # "all" or a regime label string

class GetBacktestRunOutput(BaseModel):
    run: BacktestRunDetail
    metrics: list[PerformanceMetric]


# ── Tool 7: get_backtest_trades ───────────────────────────────────────────────
class GetBacktestTradesInput(BaseModel):
    run_id: str  # path param

class TradeRecord(BaseModel):
    id: str
    backtest_run_id: str
    instrument_id: str
    entry_time: datetime
    exit_time: datetime | None = None
    side: TradeSide
    quantity: float
    entry_price: float
    exit_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    pnl: float
    pnl_pct: float
    holding_period: int
    entry_reason: str
    exit_reason: str
    regime_at_entry: str
    regime_at_exit: str

class GetBacktestTradesOutput(BaseModel):
    run_id: str
    trades: list[TradeRecord]
    count: int


# ── Tool 8: get_equity_curve ──────────────────────────────────────────────────
class GetEquityCurveInput(BaseModel):
    run_id: str  # path param

class EquityPoint(BaseModel):
    timestamp: str   # ISO datetime string
    equity: float    # closed-trade P&L only (unrealized excluded)
    drawdown: float  # drawdown from peak (negative or 0.0)

class GetEquityCurveOutput(BaseModel):
    run_id: str
    equity_curve: list[EquityPoint]


# ── Tool 9: list_backtest_runs ────────────────────────────────────────────────
class ListBacktestRunsInput(BaseModel):
    limit: int = 20  # query param

class BacktestRunSummary(BaseModel):
    id: str
    job_id: str | None = None
    strategy_id: str | None = None
    instrument_id: str
    timeframe: str
    test_start: datetime
    test_end: datetime
    status: JobStatus
    created_at: datetime

class ListBacktestRunsOutput(BaseModel):
    runs: list[BacktestRunSummary]
    count: int
```

---

## Priority Gap Summary

| Priority | Gap | Impact on Phase 5 |
|---|---|---|
| HIGH | `GET/POST /api/strategies` missing `response_model=` | Agent parses strategy records as unvalidated dicts |
| HIGH | `BacktestRunSummaryResponse.run` is `dict[str, Any]` | Agent must handle arbitrary dict shape for run detail |
| HIGH | `BacktestTradesResponse.trades` is `list[dict[str, Any]]` | Trade field access is unguarded |
| MEDIUM | `GET /api/models/hmm/{id}` missing `response_model=` | Model metadata (artifact_ref, label_map) untyped |
| MEDIUM | Per-type job pollers missing `response_model=JobResponse` | Non-blocking — use `/api/jobs/{id}` instead |
| LOW | `market-data/bars` timeframe as raw string param | Not in Phase 5 tool set |
| LOW | Dead `AgentResearchRequest` / `AgentSessionResponse` schemas | No runtime impact; clutter |
