"""Research API routes — Phase 5B agentic research trigger.

POST /api/research/run             — create experiment record + fire graph (202)
GET  /api/research/runs/{id}       — fetch single experiment record
GET  /api/research/runs            — list recent experiment records
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.agents.graph import build_graph
from backend.agents.state import make_default_state
from backend.deps import get_experiment_registry
from backend.lab.experiment_registry import ExperimentRecord, ExperimentRegistry, ExperimentStatus
from backend.schemas.requests import ResearchRunRequest, ResearchRunResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run", response_model=ResearchRunResponse, status_code=202)
async def trigger_research_run(
    body: ResearchRunRequest,
    background_tasks: BackgroundTasks,
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> ResearchRunResponse:
    """Create an experiment record and fire the research graph asynchronously."""
    generation = 0
    if body.task == "mutate" and body.parent_experiment_id is not None:
        try:
            parent = registry.get(body.parent_experiment_id)
            generation = parent.generation + 1
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Parent experiment '{body.parent_experiment_id}' not found",
            )

    # Build a session_id from the initial graph state so it is consistent
    initial_state = make_default_state(
        instrument=body.instrument,
        timeframe=body.timeframe,
        test_start=body.test_start,
        test_end=body.test_end,
        task=body.task,
        requested_by=body.requested_by,
    )
    session_id: str = initial_state["session_id"]

    record = registry.create(
        session_id=session_id,
        instrument=body.instrument,
        timeframe=body.timeframe,
        test_start=body.test_start,
        test_end=body.test_end,
        task=body.task,
        requested_by=body.requested_by,
        model_id=body.model_id,
        feature_run_id=body.feature_run_id,
        parent_id=body.parent_experiment_id,
        generation=generation,
    )

    background_tasks.add_task(
        _run_research_graph,
        experiment_id=record.id,
        session_id=session_id,
        body=body,
        initial_state=initial_state,
        registry=registry,
    )

    return ResearchRunResponse(
        experiment_id=record.id,
        session_id=session_id,
        status=record.status.value,
        created_at=record.created_at,
    )


@router.get("/runs/{experiment_id}", response_model=ExperimentRecord)
async def get_research_run(
    experiment_id: str,
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> ExperimentRecord:
    """Return a single experiment record by ID."""
    try:
        return registry.get(experiment_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not found",
        )


@router.get("/runs", response_model=list[ExperimentRecord])
async def list_research_runs(
    limit: int = Query(default=20, ge=1, le=100),
    instrument: str | None = Query(default=None),
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> list[ExperimentRecord]:
    """List recent experiment records, newest first."""
    return registry.list_recent(limit=limit, instrument=instrument)


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

async def _run_research_graph(
    experiment_id: str,
    session_id: str,
    body: ResearchRunRequest,
    initial_state: dict,
    registry: ExperimentRegistry,
) -> None:
    """Invoke the LangGraph research graph and persist results.

    graph.invoke() is synchronous — it is dispatched via run_in_executor to
    avoid blocking the FastAPI event loop.
    """
    # Overlay experiment-specific fields onto the pre-built state
    initial_state["session_id"] = session_id
    initial_state["experiment_id"] = experiment_id
    initial_state["model_id"] = body.model_id
    initial_state["feature_run_id"] = body.feature_run_id
    initial_state["generation"] = (
        registry.get(experiment_id).generation
    )
    if body.parent_experiment_id is not None:
        initial_state["parent_experiment_id"] = body.parent_experiment_id

    registry.update_status(experiment_id, ExperimentStatus.RUNNING)

    try:
        graph = build_graph()
        loop = asyncio.get_event_loop()
        final_state = await loop.run_in_executor(
            None,
            lambda: graph.invoke(initial_state),
        )
        _write_graph_result(experiment_id, final_state, registry)
        # Fire memory extraction as best-effort background task
        asyncio.create_task(_write_memory_async(experiment_id))
    except Exception as exc:
        logger.exception("Research graph failed for experiment %s", experiment_id)
        registry.update_status(
            experiment_id,
            ExperimentStatus.FAILED,
            error_message=str(exc),
        )


def _write_graph_result(
    experiment_id: str,
    final_state: dict,
    registry: ExperimentRegistry,
) -> None:
    """Determine terminal status and persist all metrics from the final graph state.

    Marker scores and composite_score from the DIME mark_node are merged into the
    final_state_snapshot so they are preserved alongside standard backtest fields.
    """
    discard = final_state.get("discard")
    comparison_recommendation = final_state.get("comparison_recommendation")
    backtest_run_id = final_state.get("backtest_run_id")

    if discard is True:
        terminal_status = ExperimentStatus.FAILED
    elif comparison_recommendation == "continue":
        terminal_status = ExperimentStatus.SUCCEEDED
    elif backtest_run_id is not None:
        terminal_status = ExperimentStatus.ARCHIVED
    else:
        terminal_status = ExperimentStatus.FAILED

    # Extract metrics safely
    metrics: dict = final_state.get("backtest_metrics") or {}
    diagnostic_summary: dict = final_state.get("diagnostic_summary") or {}

    # Build snapshot with marker context explicitly captured alongside standard fields
    snapshot = dict(final_state)
    marker_scores = final_state.get("marker_scores")
    composite_score = final_state.get("composite_score")
    marker_action = final_state.get("marker_action")
    if marker_scores is not None or composite_score is not None:
        snapshot["_marker_context"] = {
            "marker_scores": marker_scores,
            "marker_action": marker_action,
            "composite_score": composite_score,
        }

    registry.update_status(
        experiment_id,
        terminal_status,
        hypothesis=final_state.get("hypothesis"),
        strategy_id=final_state.get("strategy_id"),
        backtest_run_id=backtest_run_id,
        sharpe=_safe_float(metrics.get("sharpe_ratio")),
        max_drawdown_pct=_safe_float(metrics.get("max_drawdown_pct")),
        win_rate=_safe_float(metrics.get("win_rate")),
        total_trades=_safe_int(metrics.get("total_trades")),
        failure_taxonomy=diagnostic_summary.get("failure_taxonomy"),
        comparison_recommendation=comparison_recommendation,
        error_message=_first_error(final_state.get("errors")),
        final_state_snapshot=snapshot,
    )


def _safe_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_error(errors: object) -> str | None:
    if isinstance(errors, list) and errors:
        return errors[0]
    return None


async def _write_memory_async(experiment_id: str) -> None:
    """Best-effort memory extraction — never raises."""
    try:
        from backend.agents.memory_writer import write_memory_for_experiment
        from backend.agents.providers.bedrock import BedrockAdapter
        from backend.deps import get_memory_store, get_metadata_repo
        await write_memory_for_experiment(
            experiment_id=experiment_id,
            metadata_repo=get_metadata_repo(),
            memory_store=get_memory_store(),
            bedrock_adapter=BedrockAdapter(),
        )
    except Exception as exc:
        logger.warning("_write_memory_async failed for %s: %s", experiment_id, exc)
