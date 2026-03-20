"""Typed Pydantic v2 input/output models for chat agent tools.

Seven tools total:
  1. get_experiment              — read a single experiment record
  2. get_backtest_result         — read backtest run + metrics
  3. get_strategy_definition     — read a strategy definition
  4. list_recent_experiments     — list experiments (newest first)
  5. search_experiments          — keyword search across experiments
  6. check_data_availability     — check market data coverage for an instrument/timeframe/range
  7. execute_proposed_action     — write: submit a backtest job (security-critical)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Tool 1: get_experiment
# ---------------------------------------------------------------------------

class GetExperimentInput(BaseModel):
    experiment_id: str


class GetExperimentOutput(BaseModel):
    experiment: dict[str, Any]  # full ExperimentRecord as dict
    iterations: list[dict[str, Any]] = []  # iteration history from detail endpoint


# ---------------------------------------------------------------------------
# Tool 2: get_backtest_result
# ---------------------------------------------------------------------------

class GetBacktestResultInput(BaseModel):
    backtest_run_id: str


class GetBacktestResultOutput(BaseModel):
    run: dict[str, Any]
    metrics: list[dict[str, Any]]
    per_regime_metrics: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    trade_count: int = 0


# ---------------------------------------------------------------------------
# Tool 3: get_strategy_definition
# ---------------------------------------------------------------------------

class GetStrategyDefinitionInput(BaseModel):
    strategy_id: str


class GetStrategyDefinitionOutput(BaseModel):
    id: str
    name: str
    strategy_type: str
    definition_json: dict[str, Any]
    tags: list[str]


# ---------------------------------------------------------------------------
# Tool 4: list_recent_experiments
# ---------------------------------------------------------------------------

class ListRecentExperimentsInput(BaseModel):
    n: int = 10
    instrument: str | None = None


class ExperimentSummary(BaseModel):
    id: str
    hypothesis: str  # truncated to 100 chars
    status: str
    instrument: str
    timeframe: str
    created_at: str


class ListRecentExperimentsOutput(BaseModel):
    experiments: list[ExperimentSummary]


# ---------------------------------------------------------------------------
# Tool 5: search_experiments
# ---------------------------------------------------------------------------

class SearchExperimentsInput(BaseModel):
    query: str


class SearchExperimentsOutput(BaseModel):
    experiments: list[ExperimentSummary]
    query: str = ""


# ---------------------------------------------------------------------------
# Tool 6: check_data_availability
# ---------------------------------------------------------------------------

class CheckDataAvailabilityInput(BaseModel):
    instrument: str   # e.g. "EUR_USD"
    timeframe: str    # e.g. "H1"
    start_date: str   # YYYY-MM-DD
    end_date: str     # YYYY-MM-DD


class CheckDataAvailabilityOutput(BaseModel):
    has_data: bool
    coverage_start: str | None  # ISO date or None
    coverage_end: str | None    # ISO date or None
    needs_ingestion: bool       # True if data is missing or doesn't cover the requested range
    message: str                # human-readable summary for the LLM


# ---------------------------------------------------------------------------
# Tool 7: execute_proposed_action (write path — security-critical)
# ---------------------------------------------------------------------------

class ExecuteProposedActionInput(BaseModel):
    action_type: str  # must be "submit_backtest" for now; reject others
    strategy_definition: dict[str, Any]  # rules DSL definition_json (inline_strategy)
    instrument: str  # e.g. "EUR_USD" — maps to instrument_id
    timeframe: str  # e.g. "H1" — maps to Timeframe enum
    test_start: str  # ISO date string
    test_end: str  # ISO date string
    feature_run_id: str | None = None
    model_id: str | None = None
    spread_pips: float = 2.0
    slippage_pips: float = 0.5
    commission_per_unit: float = 0.0
    pip_size: float = 0.0001
    description: str = ""  # human label for the run
    session_id: str | None = None  # chat session that triggered this action


class ExecuteProposedActionOutput(BaseModel):
    job_id: str
    backtest_run_id: str | None  # may not be available immediately
    status: str  # "QUEUED"
    message: str  # human-readable confirmation e.g. "Backtest job queued. Job ID: abc123"


# ---------------------------------------------------------------------------
# Tool 8: search_memories
# ---------------------------------------------------------------------------

class SearchMemoriesInput(BaseModel):
    instrument: str | None = None
    timeframe: str | None = None
    tags: list[str] | None = None
    outcome: str | None = None
    limit: int = 10


class MemorySummary(BaseModel):
    id: str
    instrument: str
    timeframe: str
    outcome: str
    theory: str
    learnings: list[str]
    tags: list[str]
    sharpe: float | None
    total_trades: int | None
    created_at: str


class SearchMemoriesOutput(BaseModel):
    memories: list[MemorySummary]
    count: int
