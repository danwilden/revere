"""API request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from backend.schemas.enums import DataSource, ExperimentStatus, JobStatus, JobType, SignalType, StrategyType, Timeframe, TradeSide
from backend.schemas.models import AutoMLJobRecord, JobRun


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestionJobRequest(BaseModel):
    instruments: list[str]
    source: DataSource
    start_date: datetime
    end_date: datetime


class DukascopyDownloadRequest(BaseModel):
    instruments: list[str]
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


class MaterializeSignalRequest(BaseModel):
    instrument_id: str
    timeframe: str
    start: str   # ISO date string
    end: str     # ISO date string


class CreateRiskFilterRequest(BaseModel):
    name: str
    description: str
    rules_node: dict   # standard rules DSL node


class SignalContextResponse(BaseModel):
    instrument_id: str
    timeframe: str
    start: str
    end: str
    available_fields: list[str]


class CreateSignalRequest(BaseModel):
    name: str
    signal_type: str
    # HMM fields
    model_id: str | None = None
    feature_run_id: str | None = None
    # AutoML fields
    automl_job_id: str | None = None
    # Risk filter fields
    rules_node: dict | None = None
    metadata: dict = {}


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
    session_id: str | None = None
    """Optional chat session ID that triggered this backtest; used for completion notifications."""

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
# Strategy response schemas
# ---------------------------------------------------------------------------

class StrategyResponse(BaseModel):
    """Typed response for a single strategy record."""
    id: str
    name: str
    description: str
    strategy_type: StrategyType
    definition_json: dict[str, Any]
    version: int
    created_at: datetime
    active_flag: bool
    tags: list[str]


class StrategyListResponse(BaseModel):
    strategies: list[StrategyResponse]
    count: int


class StrategyValidateResponse(BaseModel):
    valid: bool
    errors: list[str]


# ---------------------------------------------------------------------------
# Typed inner record models (used by backtest response schemas)
# ---------------------------------------------------------------------------

class BacktestRunRecord(BaseModel):
    """Typed version of a BacktestRun row."""
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


class PerformanceMetricRecord(BaseModel):
    """Typed version of a PerformanceMetric row."""
    id: str
    backtest_run_id: str
    metric_name: str
    metric_value: float | None
    segment_type: str
    segment_key: str


class TradeRecord(BaseModel):
    """Typed version of a Trade row."""
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


class JobRecord(BaseModel):
    """Typed version of a JobRun row (for list responses)."""
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


# ---------------------------------------------------------------------------
# Model (HMM) response schemas
# ---------------------------------------------------------------------------

class ModelRecordResponse(BaseModel):
    """Typed response for a single HMM model record."""
    id: str
    model_type: str
    instrument_id: str
    timeframe: str
    training_start: datetime
    training_end: datetime
    parameters_json: dict[str, Any]
    artifact_ref: str | None = None
    label_map_json: dict[str, str]
    created_at: datetime
    status: JobStatus


class ModelListResponse(BaseModel):
    models: list[ModelRecordResponse]
    count: int


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
    jobs: list[JobRecord]
    count: int


class BacktestRunSummaryResponse(BaseModel):
    run: BacktestRunRecord
    metrics: list[PerformanceMetricRecord]


class BacktestTradesResponse(BaseModel):
    run_id: str
    trades: list[TradeRecord]
    count: int


class EquityPoint(BaseModel):
    timestamp: str
    equity: float
    drawdown: float


class BacktestEquityResponse(BaseModel):
    run_id: str
    equity_curve: list[EquityPoint]


class BacktestRunListResponse(BaseModel):
    runs: list[BacktestRunRecord]
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


# ---------------------------------------------------------------------------
# Experiments — API-facing schemas (Phase 5B)
# ---------------------------------------------------------------------------

class ExperimentRecord(BaseModel):
    """API response shape for an experiment record.

    This is the richer API-layer view used by GET /api/experiments and
    GET /api/experiments/{id}.  The persistence model (lab.ExperimentRecord)
    is a separate, narrower model keyed on agent state fields.
    """
    id: str
    name: str
    description: str
    instrument: str
    timeframe: str
    test_start: datetime
    test_end: datetime
    model_id: str | None = None
    feature_run_id: str | None = None
    status: ExperimentStatus
    created_at: datetime
    updated_at: datetime
    requested_by: str
    generation_count: int
    best_strategy_id: str | None = None
    best_backtest_run_id: str | None = None
    tags: list[str] = []
    robustness_job_id: str | None = None
    tier: str | None = None          # "validated" when approved via /approve
    discard_reason: str | None = None


class ExperimentIteration(BaseModel):
    id: str
    experiment_id: str
    generation: int
    strategy_id: str | None = None
    backtest_run_id: str | None = None
    hypothesis: str | None = None
    mutation_plan: str | None = None
    diagnosis_summary: str | None = None
    comparison_recommendation: str | None = None
    discard: bool | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ExperimentListResponse(BaseModel):
    experiments: list[ExperimentRecord]
    count: int


class ExperimentDetailResponse(BaseModel):
    experiment: ExperimentRecord
    iterations: list[ExperimentIteration]


class ExperimentCreateRequest(BaseModel):
    name: str
    description: str = ""
    instrument: str
    timeframe: str
    test_start: datetime
    test_end: datetime
    model_id: str | None = None
    feature_run_id: str | None = None
    tags: list[str] = []
    requested_by: str = "system"

    @model_validator(mode="after")
    def _validate_date_range(self) -> "ExperimentCreateRequest":
        if self.test_end <= self.test_start:
            raise ValueError("test_end must be after test_start")
        if not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string")
        if not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string")
        return self


class ExperimentResponse(BaseModel):
    experiment: ExperimentRecord


class ExperimentStatusUpdateRequest(BaseModel):
    status: ExperimentStatus


# ---------------------------------------------------------------------------
# Research run — trigger schema (Phase 5B)
# ---------------------------------------------------------------------------

class ResearchRunRequest(BaseModel):
    instrument: str
    timeframe: str
    test_start: str
    test_end: str
    task: str = "generate_seed"
    model_id: str | None = None
    feature_run_id: str | None = None
    parent_experiment_id: str | None = None
    requested_by: str = "api"
    max_iterations: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def _require_parent_for_mutate(self) -> "ResearchRunRequest":
        if self.task == "mutate" and self.parent_experiment_id is None:
            raise ValueError("parent_experiment_id required when task is 'mutate'")
        return self


class ResearchRunResponse(BaseModel):
    experiment_id: str
    session_id: str
    status: str
    created_at: str


# ---------------------------------------------------------------------------
# Research status — convenience polling schema (Phase 5B)
# ---------------------------------------------------------------------------

class ActiveResearchJob(BaseModel):
    job_id: str
    status: JobStatus
    progress_pct: float
    stage_label: str
    started_at: datetime | None = None
    task: str


class CompletedResearchJob(BaseModel):
    job_id: str
    status: JobStatus
    completed_at: datetime
    task: str
    error_message: str | None = None


class ResearchStatusResponse(BaseModel):
    experiment_id: str
    active_job: ActiveResearchJob | None
    last_completed_job: CompletedResearchJob | None
    iteration_count: int


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


class ResolveFeatureRunRequest(BaseModel):
    """Request body for POST /api/features/runs/resolve.

    Finds an existing feature run that fully covers the requested instrument/
    timeframe/date range, or creates a new one synchronously.
    """
    instrument: str   # e.g. "EUR_USD"
    timeframe: str    # e.g. "H1"
    start_date: str   # ISO date string YYYY-MM-DD
    end_date: str     # ISO date string YYYY-MM-DD


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


# ---------------------------------------------------------------------------
# AutoML — Phase 5D
# ---------------------------------------------------------------------------

class AutoMLJobRequest(BaseModel):
    instrument_id: str
    timeframe: str
    feature_run_id: str
    model_id: str
    train_end_date: str
    test_end_date: str
    target_type: str = "direction"
    target_horizon_bars: int = 1
    max_runtime_seconds: int = 3600


class AutoMLJobStatusResponse(BaseModel):
    job_run: JobRun
    automl_record: AutoMLJobRecord


# ---------------------------------------------------------------------------
# Robustness battery — Phase 5F
# ---------------------------------------------------------------------------

class HoldoutResult(BaseModel):
    backtest_run_id: str
    test_start: datetime
    test_end: datetime
    net_return_pct: float | None
    sharpe_ratio: float | None
    max_drawdown_pct: float | None
    trade_count: int
    passed: bool
    block_reason: str | None = None


class WalkForwardWindow(BaseModel):
    window_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    backtest_run_id: str
    net_return_pct: float | None
    sharpe_ratio: float | None
    trade_count: int
    passed: bool


class WalkForwardResult(BaseModel):
    windows: list[WalkForwardWindow]
    windows_passed: int
    windows_total: int
    passed: bool
    block_reason: str | None = None


class CostStressVariant(BaseModel):
    multiplier: float
    backtest_run_id: str
    net_return_pct: float | None
    sharpe_ratio: float | None
    passed: bool


class CostStressResult(BaseModel):
    variants: list[CostStressVariant]
    passed: bool
    block_reason: str | None = None


class ParamSensitivityStep(BaseModel):
    param_name: str
    param_value: float
    backtest_run_id: str
    net_return_pct: float | None
    sharpe_ratio: float | None


class ParamSensitivityResult(BaseModel):
    steps: list[ParamSensitivityStep]
    return_range_pct: float
    base_net_return_pct: float
    passed: bool
    block_reason: str | None = None


class RobustnessResult(BaseModel):
    experiment_id: str
    battery_job_id: str
    computed_at: datetime
    holdout: HoldoutResult
    walk_forward: WalkForwardResult
    cost_stress: CostStressResult
    param_sensitivity: ParamSensitivityResult
    promoted: bool
    block_reasons: list[str] = Field(default_factory=list)


class DiscardExperimentRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class RobustnessStatusResponse(BaseModel):
    experiment_id: str
    job_id: str | None = None
    job_status: str | None = None
    progress_pct: float | None = None
    error_message: str | None = None
    result: RobustnessResult | None = None


# ---------------------------------------------------------------------------
# Chat — Phase 6
# ---------------------------------------------------------------------------

class ChatMessageContext(BaseModel):
    experiment_id: str | None = None
    strategy_id: str | None = None
    backtest_id: str | None = None
    conversation_mode: str | None = None


class ChatSessionCreateRequest(BaseModel):
    initial_context: ChatMessageContext | None = None
    title: str = ""


class ChatSendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    context: ChatMessageContext | None = None


class ChatSessionResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionResponse]
    count: int


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str  # "user" | "assistant"
    content: str
    total_tokens: int | None
    actions_json: list[dict]
    created_at: datetime


class ChatMessageListResponse(BaseModel):
    messages: list[ChatMessageResponse]
    count: int
