"""Typed Pydantic v2 input/output models for all Phase 5 agent tools."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Reuse canonical enums — do NOT redefine them here.
from backend.schemas.enums import JobStatus, StrategyType, Timeframe, TradeSide


# ---------------------------------------------------------------------------
# Tool 1: list_strategies
# ---------------------------------------------------------------------------

class ListStrategiesInput(BaseModel):
    """No required parameters — lists all persisted strategies."""


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


# ---------------------------------------------------------------------------
# Tool 2: create_strategy
# ---------------------------------------------------------------------------

class CreateStrategyInput(BaseModel):
    name: str
    description: str = ""
    strategy_type: StrategyType
    definition_json: dict[str, Any] = {}
    tags: list[str] = []

# Output: StrategyRecord (shared above)


# ---------------------------------------------------------------------------
# Tool 3: validate_strategy
# ---------------------------------------------------------------------------

class ValidateStrategyInput(BaseModel):
    strategy_id: str       # path param
    definition_json: dict[str, Any]
    strategy_type: StrategyType


class ValidateStrategyOutput(BaseModel):
    valid: bool
    errors: list[str]


# ---------------------------------------------------------------------------
# Tool 4: submit_backtest
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tool 5: poll_job
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tool 6: get_backtest_run
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tool 7: get_backtest_trades
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tool 8: get_equity_curve
# ---------------------------------------------------------------------------

class GetEquityCurveInput(BaseModel):
    run_id: str  # path param


class EquityPoint(BaseModel):
    timestamp: str   # ISO datetime string
    equity: float    # closed-trade P&L only (unrealized excluded)
    drawdown: float  # drawdown from peak (negative or 0.0)


class GetEquityCurveOutput(BaseModel):
    run_id: str
    equity_curve: list[EquityPoint]


# ---------------------------------------------------------------------------
# Tool 9: list_backtest_runs
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase 5B: StrategyCandidate — output of strategy_researcher_node
# ---------------------------------------------------------------------------

class StrategyCandidate(BaseModel):
    candidate_id: str
    hypothesis: str
    strategy_id: str | None = None
    strategy_definition: dict[str, Any]
    backtest_run_id: str | None = None
    metrics: dict[str, float | None] = {}
    trade_count: int | None = None
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    win_rate: float | None = None
    generation: int = 0
    created_at: str
    validation_errors: list[str] = []
    error: str | None = None

    @field_validator("trade_count", mode="before")
    @classmethod
    def _coerce_trade_count_none_to_zero(cls, v: int | None) -> int:
        """Coerce None to 0 so LLM output with trade_count: null after failed backtests validates."""
        return 0 if v is None else v


# ---------------------------------------------------------------------------
# Phase 5B: DiagnosticSummary — structured output of backtest_diagnostics_node
# ---------------------------------------------------------------------------

class FailureTaxonomy(str, Enum):
    ZERO_TRADES = "zero_trades"
    TOO_FEW_TRADES = "too_few_trades"
    EXCESSIVE_DRAWDOWN = "excessive_drawdown"
    POOR_SHARPE = "poor_sharpe"
    OVERFITTING_SIGNAL = "overfitting_signal"
    WRONG_REGIME_FILTER = "wrong_regime_filter"
    ENTRY_TOO_RESTRICTIVE = "entry_too_restrictive"
    EXIT_TOO_EARLY = "exit_too_early"
    EXIT_TOO_LATE = "exit_too_late"
    NO_EDGE = "no_edge"
    POSITIVE = "positive"


class DiagnosticSummary(BaseModel):
    failure_taxonomy: FailureTaxonomy
    root_cause: str
    recommended_mutations: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    discard: bool


# ---------------------------------------------------------------------------
# Phase 5B: ComparisonResult — output of generation_comparator_node
# ---------------------------------------------------------------------------

class ComparisonResult(BaseModel):
    winner_id: str | None
    winner_strategy_id: str | None
    rationale: str
    score_delta: float | None
    recommendation: str       # "continue" | "archive" | "discard"
    scores: dict[str, float]  # candidate_id -> composite score


# ---------------------------------------------------------------------------
# Phase 5B: get_hmm_model tool schemas
# ---------------------------------------------------------------------------

class GetHmmModelInput(BaseModel):
    model_id: str


class HmmModelStateStats(BaseModel):
    state_id: int
    label: str | None = None
    mean_return: float | None = None
    mean_adx: float | None = None
    mean_volatility: float | None = None
    frequency_pct: float | None = None


class GetHmmModelOutput(BaseModel):
    id: str
    instrument_id: str
    timeframe: str
    num_states: int
    label_map: dict[str, str]
    state_stats: list[HmmModelStateStats]
    feature_run_id: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# Phase 5B: Regime context schemas — used by regime tool and strategy_researcher
# ---------------------------------------------------------------------------

class RegimeSnapshot(BaseModel):
    """A single bar's regime state."""
    timestamp: str          # ISO datetime
    state_id: int
    label: str | None = None
    probability: float | None = None   # probability of this state at this bar


class GetRegimeContextInput(BaseModel):
    """Input for the get_regime_context tool executor."""
    model_id: str
    instrument: str
    timeframe: str


class RegimeContext(BaseModel):
    """Full regime context for a symbol — passed to strategy_researcher_node."""
    model_id: str
    instrument: str
    timeframe: str
    num_states: int
    label_map: dict[str, str]           # state_id (str) -> semantic label
    state_stats: list[dict[str, Any]]   # raw state statistics list
    current_regime_label: str | None = None    # most recent bar's label
    regime_probabilities: dict[str, float] = {}  # label -> probability
    signal_bank_snapshot: dict[str, float] = {}  # signal_name -> latest value
    error: str | None = None            # set if context loading failed


# ---------------------------------------------------------------------------
# Phase 5C: Feature discovery schemas
# ---------------------------------------------------------------------------

ALLOWED_FAMILIES: set[str] = {
    "momentum",
    "breakout",
    "volatility",
    "session",
    "microstructure",
    "regime_persistence",
}


class FeatureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    family: str                  # must be one of ALLOWED_FAMILIES
    formula_description: str
    lookback_bars: int
    dependency_columns: list[str]
    transformation: str
    expected_intuition: str
    leakage_risk: str            # "none" | "low" | "medium" | "high"
    code: str                    # Python code; must assign pd.Series to 'result'


class FeatureEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_name: str
    f_statistic: float
    regime_breakdown: dict[str, Any]   # regime_label -> mean feature value
    leakage_risk: str
    registered: bool


class ProposeFeatureInput(BaseModel):
    spec: dict[str, Any]   # raw FeatureSpec dict from LLM


class ProposeFeatureOutput(BaseModel):
    valid: bool
    errors: list[str]
    spec: dict[str, Any] | None = None


class ComputeFeatureInput(BaseModel):
    feature_name: str
    code: str
    instrument: str
    timeframe: str
    start: str    # ISO date
    end: str      # ISO date


class ComputeFeatureOutput(BaseModel):
    feature_name: str
    success: bool
    series_length: int = 0
    sample_values: list[float] = []
    error: str | None = None


class EvaluateFeatureInput(BaseModel):
    feature_name: str
    instrument: str
    timeframe: str
    start: str
    end: str
    model_id: str


class EvaluateFeatureOutput(BaseModel):
    feature_name: str
    f_statistic: float
    regime_breakdown: dict[str, float]
    leakage_risk: str
    passes_threshold: bool   # f_statistic > REGISTRATION_THRESHOLD and leakage_risk != "high"


class RegisterFeatureInput(BaseModel):
    feature_name: str


class RegisterFeatureOutput(BaseModel):
    feature_name: str
    registered: bool
    reason: str   # "registered" | "below_threshold" | "leakage_blocked" | "already_exists"


# ---------------------------------------------------------------------------
# Phase 5D: AutoML tool schemas — Bedrock tool definitions (JSON schema format)
# ---------------------------------------------------------------------------

# These are the tool spec dicts passed directly to the Bedrock Converse API
# toolConfig.  They follow the same shape as RESEARCHER_TOOLS in
# feature_researcher.py (toolSpec wrapper with name/description/inputSchema).

AUTOML_TOOLS: list[dict] = [
    {
        "toolSpec": {
            "name": "launch_automl_job",
            "description": (
                "Launch a SageMaker Autopilot v2 job on a pre-built feature matrix. "
                "Use this to start AutoML training for a given signal mining task. "
                "Returns job_name and initial status."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Unique identifier for this AutoML job (used as SageMaker job name).",
                        },
                        "target_type": {
                            "type": "string",
                            "enum": ["direction", "return_bucket"],
                            "description": (
                                "'direction' for binary classification (long/short), "
                                "'return_bucket' for multiclass classification."
                            ),
                        },
                        "train_s3_uri": {
                            "type": "string",
                            "description": "S3 URI pointing to the training dataset (CSV or S3 prefix).",
                        },
                        "output_s3_prefix": {
                            "type": "string",
                            "description": "S3 prefix where SageMaker writes AutoML outputs.",
                        },
                    },
                    "required": ["job_id", "target_type", "train_s3_uri", "output_s3_prefix"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_automl_job_status",
            "description": (
                "Poll the status of a running SageMaker AutoML job. "
                "Returns status ('running'|'completed'|'failed'), accepted flag, "
                "failure_reason, and best_candidate details."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "automl_job_id": {
                            "type": "string",
                            "description": "SageMaker AutoML job name to describe.",
                        },
                    },
                    "required": ["automl_job_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_automl_candidates",
            "description": (
                "Retrieve all evaluated candidates for a completed SageMaker AutoML job. "
                "Returns a list of candidate dicts with metrics, pipeline steps, and AUC-ROC scores."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "automl_job_id": {
                            "type": "string",
                            "description": "SageMaker AutoML job name.",
                        },
                    },
                    "required": ["automl_job_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "convert_to_signal",
            "description": (
                "Convert the best accepted AutoML candidate into a Medallion Signal record. "
                "Only callable after model_researcher has evaluated and accepted the job. "
                "Returns signal_id and signal_name on success."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "automl_job_id": {
                            "type": "string",
                            "description": "SageMaker AutoML job name whose best candidate to promote.",
                        },
                    },
                    "required": ["automl_job_id"],
                }
            },
        }
    },
]


# Pydantic input models for the four AutoML tools — used for validation
# in any tool dispatcher that wants typed inputs.

class LaunchAutoMLJobInput(BaseModel):
    job_id: str
    target_type: str   # "direction" | "return_bucket"
    train_s3_uri: str
    output_s3_prefix: str
    target_column: str = "label"
    max_runtime_seconds: int = 3600


class GetAutoMLJobStatusInput(BaseModel):
    automl_job_id: str


class GetAutoMLCandidatesInput(BaseModel):
    automl_job_id: str


class ConvertToSignalInput(BaseModel):
    automl_job_id: str
