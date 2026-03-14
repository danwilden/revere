"""API request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from backend.schemas.enums import DataSource, JobStatus, SignalType, StrategyType, Timeframe


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestionJobRequest(BaseModel):
    instruments: list[str]
    source: DataSource
    start_date: datetime
    end_date: datetime


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


# ---------------------------------------------------------------------------
# HMM
# ---------------------------------------------------------------------------

class HMMTrainingRequest(BaseModel):
    instrument: str
    timeframe: Timeframe
    train_start: datetime
    train_end: datetime
    num_states: int = 7
    feature_set_name: str = "default_v1"


class LabelMapUpdateRequest(BaseModel):
    label_map: dict[str, str]
    """Maps state_id (as string key) to a semantic label string.
    Example: {"0": "TREND_BULL_LOW_VOL", "1": "RANGE_MEAN_REVERT"}
    Keys must be string representations of integer state IDs.
    """


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class SignalCreateRequest(BaseModel):
    name: str
    signal_type: SignalType
    definition_json: dict[str, Any] = {}
    source_model_id: str | None = None


class SignalMaterializeRequest(BaseModel):
    instrument: str
    timeframe: Timeframe
    start_date: datetime
    end_date: datetime


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

class StrategyCreateRequest(BaseModel):
    name: str
    description: str = ""
    strategy_type: StrategyType
    definition_json: dict[str, Any] = {}
    tags: list[str] = []


class StrategyValidateRequest(BaseModel):
    definition_json: dict[str, Any]
    strategy_type: StrategyType


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------

class BacktestJobRequest(BaseModel):
    strategy_id: str | None = None
    inline_strategy: dict[str, Any] | None = None
    instrument: str
    timeframe: Timeframe
    test_start: datetime
    test_end: datetime
    spread_pips: float = 2.0
    commission_per_unit: float = 0.0
    slippage_pips: float = 0.5
    pip_size: float = 0.0001
    """Price value of one pip.  Use 0.0001 for standard 4-decimal pairs (EUR/USD,
    GBP/USD, AUD/USD) and 0.01 for JPY pairs (USD/JPY).  Defaults to 0.0001.
    Providing the wrong value will silently scale all spread/slippage costs by
    100x, so the frontend must always send this explicitly for JPY instruments."""
    feature_run_id: str | None = None
    """Optional feature run to join into the backtest frame."""
    model_id: str | None = None
    """Optional HMM model whose regime labels are joined into the backtest frame."""

    @model_validator(mode="after")
    def _require_strategy(self) -> "BacktestJobRequest":
        """Reject requests that supply neither strategy_id nor inline_strategy.

        Catching this at validation time (422) is preferable to discovering it
        inside the background job thread where it would silently fail the job.
        """
        if self.strategy_id is None and self.inline_strategy is None:
            raise ValueError(
                "Either strategy_id or inline_strategy must be provided"
            )
        return self


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class AgentResearchRequest(BaseModel):
    prompt: str
    agent_type: str = "strategy"
    context: dict[str, Any] = {}


class AgentSessionResponse(BaseModel):
    session_id: str
    status: str


# ---------------------------------------------------------------------------
# Backtest response schemas
# ---------------------------------------------------------------------------

class BacktestJobListResponse(BaseModel):
    jobs: list[dict[str, Any]]
    count: int


class BacktestRunSummaryResponse(BaseModel):
    run: dict[str, Any]
    metrics: list[dict[str, Any]]


class BacktestTradesResponse(BaseModel):
    run_id: str
    trades: list[dict[str, Any]]
    count: int


class EquityPoint(BaseModel):
    timestamp: str
    equity: float
    drawdown: float


class BacktestEquityResponse(BaseModel):
    run_id: str
    equity_curve: list[EquityPoint]


class BacktestRunListResponse(BaseModel):
    runs: list[dict[str, Any]]
    count: int


# ---------------------------------------------------------------------------
# Generic job response schema
# ---------------------------------------------------------------------------

class JobResponse(BaseModel):
    """Standardized shape returned by all job-polling endpoints.

    Matches the JobRun Pydantic model stored in metadata; typed here so
    FastAPI can generate an OpenAPI schema for GET /api/jobs/{job_id} and
    GET /api/backtests/jobs/{job_id}.
    """
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
    result_ref: str | None = None
    logs_ref: str | None = None
