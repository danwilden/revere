"""AgentState TypedDict — the shared state object that flows through the LangGraph supervisor graph."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict


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

    # ── Phase 5B extended artifacts ───────────────────────────────────────────
    regime_context: dict[str, Any] | None         # populated by strategy_researcher on first call
    strategy_candidates: list[dict[str, Any]] | None  # accumulated candidates across generations
    selected_candidate_id: str | None             # winner from generation_comparator
    diagnostic_summary: dict[str, Any] | None     # structured DiagnosticSummary dict
    comparison_result: dict[str, Any] | None      # structured ComparisonResult dict

    # ── Robustness artifacts ──────────────────────────────────────────────────
    robustness_passed: bool | None
    robustness_report: dict[str, Any] | None

    # ── Feature discovery artifacts ───────────────────────────────────────────
    feature_eval_results: list[dict[str, Any]] | None   # FeatureEvalResult dicts from session
    research_mode: str | None                           # None | "discover_features" | "automl_evaluation"

    # ── AutoML artifacts ──────────────────────────────────────────────────────
    automl_job_id: str | None           # AutoMLJobRecord.id for model_researcher
    model_evaluation: dict | None       # ModelEvaluation dict after evaluation

    # ── DIME marker layer ─────────────────────────────────────────────────────
    marker_scores: dict | None          # serialized MarkerScores (dopamine/serotonin/noradrenaline/amygdala)
    marker_action: str | None           # "explore" | "exploit" | "lock" | "continue"
    composite_score: float | None       # weighted sum of the four marker signals

    # ── Flow control ─────────────────────────────────────────────────────────
    next_node: str                     # supervisor writes; conditional edge reads
    task: str                          # "generate_seed" | "mutate" | "review" | "done"
    iteration: int                     # guard against infinite loops (max: 10)
    errors: list[str]                  # accumulated non-fatal errors
    human_approval_required: bool      # set True before promotion to "validated"


def DEFAULT_STATE(session_id: str) -> AgentState:
    """Return a minimal valid initial AgentState for unit tests and quick starts.

    Uses placeholder scope values so every required field has a non-None value.
    For production use, call :func:`make_default_state` with real scope params.

    Parameters
    ----------
    session_id:
        A fixed session identifier to use (useful for test assertions).
    """
    return AgentState(
        session_id=session_id,
        trace_id=str(uuid.uuid4()),
        requested_by="system",
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        instrument="EUR_USD",
        timeframe="H4",
        test_start="2024-01-01",
        test_end="2024-06-01",
        model_id=None,
        feature_run_id=None,
        experiment_id=None,
        parent_experiment_id=None,
        generation=0,
        hypothesis=None,
        mutation_plan=None,
        strategy_id=None,
        strategy_definition=None,
        job_id=None,
        backtest_run_id=None,
        backtest_metrics=None,
        backtest_trades=None,
        equity_curve=None,
        diagnosis_summary=None,
        recommended_mutations=None,
        discard=None,
        comparison_summary=None,
        comparison_recommendation=None,
        regime_context=None,
        strategy_candidates=None,
        selected_candidate_id=None,
        diagnostic_summary=None,
        comparison_result=None,
        robustness_passed=None,
        robustness_report=None,
        feature_eval_results=None,
        research_mode=None,
        automl_job_id=None,
        model_evaluation=None,
        marker_scores=None,
        marker_action=None,
        composite_score=None,
        next_node="supervisor",
        task="generate_seed",
        iteration=0,
        errors=[],
        human_approval_required=False,
    )


def make_default_state(
    instrument: str,
    timeframe: str,
    test_start: str,
    test_end: str,
    task: str = "generate_seed",
    requested_by: str = "system",
) -> AgentState:
    """Return a minimal valid initial AgentState dict for a new research session.

    Parameters
    ----------
    instrument:
        OANDA instrument symbol, e.g. ``"EUR_USD"``.
    timeframe:
        Bar timeframe string, e.g. ``"H4"``.
    test_start:
        ISO date string for the backtest window start, e.g. ``"2023-01-01"``.
    test_end:
        ISO date string for the backtest window end, e.g. ``"2024-01-01"``.
    task:
        Initial task directive for the supervisor.  Default ``"generate_seed"``.
    requested_by:
        Originator identifier.  Default ``"system"``.

    Returns
    -------
    AgentState
        A TypedDict-compatible dict with all required scalar fields populated.
    """
    return AgentState(
        session_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        requested_by=requested_by,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        instrument=instrument,
        timeframe=timeframe,
        test_start=test_start,
        test_end=test_end,
        model_id=None,
        feature_run_id=None,
        experiment_id=None,
        parent_experiment_id=None,
        generation=0,
        hypothesis=None,
        mutation_plan=None,
        strategy_id=None,
        strategy_definition=None,
        job_id=None,
        backtest_run_id=None,
        backtest_metrics=None,
        backtest_trades=None,
        equity_curve=None,
        diagnosis_summary=None,
        recommended_mutations=None,
        discard=None,
        comparison_summary=None,
        comparison_recommendation=None,
        regime_context=None,
        strategy_candidates=None,
        selected_candidate_id=None,
        diagnostic_summary=None,
        comparison_result=None,
        robustness_passed=None,
        robustness_report=None,
        feature_eval_results=None,
        research_mode=None,
        automl_job_id=None,
        model_evaluation=None,
        marker_scores=None,
        marker_action=None,
        composite_score=None,
        next_node="supervisor",
        task=task,
        iteration=0,
        errors=[],
        human_approval_required=False,
    )
