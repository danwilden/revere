from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.schemas.enums import (
    DataSource,
    InstrumentCategory,
    JobStatus,
    JobType,
    QualityFlag,
    SignalType,
    StrategyType,
    Timeframe,
    TradeSide,
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------

class Instrument(BaseModel):
    id: str = Field(default_factory=_new_id)
    symbol: str
    base_currency: str
    quote_currency: str
    category: InstrumentCategory
    pip_size: float
    price_precision: int
    source_symbol_map: dict[str, str] = Field(default_factory=dict)
    active_flag: bool = True


# ---------------------------------------------------------------------------
# Market data bars
# ---------------------------------------------------------------------------

class Bar1m(BaseModel):
    id: str = Field(default_factory=_new_id)
    instrument_id: str
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    source: DataSource
    quality_flag: QualityFlag = QualityFlag.OK


class BarAgg(BaseModel):
    id: str = Field(default_factory=_new_id)
    instrument_id: str
    timeframe: Timeframe
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    source: DataSource
    derivation_version: str = "1"


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class FeatureRun(BaseModel):
    id: str = Field(default_factory=_new_id)
    feature_set_name: str
    code_version: str
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    start_date: datetime
    end_date: datetime
    created_at: datetime = Field(default_factory=_utcnow)


class Feature(BaseModel):
    id: str = Field(default_factory=_new_id)
    instrument_id: str
    timeframe: Timeframe
    timestamp_utc: datetime
    feature_run_id: str
    feature_name: str
    feature_value: float | None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ModelRecord(BaseModel):
    id: str = Field(default_factory=_new_id)
    model_type: str
    instrument_id: str
    timeframe: Timeframe
    training_start: datetime
    training_end: datetime
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    artifact_ref: str | None = None
    label_map_json: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    status: JobStatus = JobStatus.QUEUED


# ---------------------------------------------------------------------------
# Regime labels
# ---------------------------------------------------------------------------

class RegimeLabel(BaseModel):
    id: str = Field(default_factory=_new_id)
    model_id: str
    instrument_id: str
    timeframe: Timeframe
    timestamp_utc: datetime
    state_id: int
    regime_label: str
    state_probabilities_json: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class Signal(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    signal_type: SignalType
    definition_json: dict[str, Any] = Field(default_factory=dict)
    source_model_id: str | None = None
    version: int = 1
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

class Strategy(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    description: str = ""
    strategy_type: StrategyType
    definition_json: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    created_at: datetime = Field(default_factory=_utcnow)
    active_flag: bool = True
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------

class BacktestRun(BaseModel):
    id: str = Field(default_factory=_new_id)
    job_id: str | None = None
    """FK to the JobRun that produced this backtest. Required to join job status → results."""
    strategy_id: str | None = None
    inline_definition: dict[str, Any] | None = None
    """Inline strategy definition for one-off backtests that haven't been saved as a Strategy."""
    instrument_id: str
    timeframe: Timeframe
    test_start: datetime
    test_end: datetime
    parameters_json: dict[str, Any] = Field(default_factory=dict)
    cost_model_json: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=_utcnow)
    result_ref: str | None = None
    oracle_regime_labels: bool = True
    """When True, regime labels used in this backtest were pre-computed over the full
    backtest window using Viterbi/forward-backward, which means the label at bar T had
    access to future bars within the inference window. Results using regime signals should
    be treated as research-grade / oracle for the MVP. Post-MVP: implement causal forward
    filtering and set this flag to False."""


class Trade(BaseModel):
    id: str = Field(default_factory=_new_id)
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
    pnl: float = 0.0
    pnl_pct: float = 0.0
    holding_period: int = 0
    entry_reason: str = ""
    exit_reason: str = ""
    regime_at_entry: str = ""
    regime_at_exit: str = ""


class PerformanceMetric(BaseModel):
    id: str = Field(default_factory=_new_id)
    backtest_run_id: str
    metric_name: str
    metric_value: float | None
    """None when the metric is undefined (e.g. Sharpe with zero trades). Never store NaN."""
    segment_type: str = "overall"
    segment_key: str = "all"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobRun(BaseModel):
    id: str = Field(default_factory=_new_id)
    job_type: JobType
    status: JobStatus = JobStatus.QUEUED
    progress_pct: float = 0.0
    stage_label: str = ""
    requested_by: str = "system"
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    params_json: dict[str, Any] = Field(default_factory=dict)
    result_ref: str | None = None
    logs_ref: str | None = None
