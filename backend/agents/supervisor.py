"""ResearchSupervisor node — reads AgentState and decides which agent node to invoke next."""
from __future__ import annotations

import time
from typing import Any

from backend.agents.providers.logging import AgentLogger
from backend.agents.state import AgentState

_MAX_ITERATIONS = 10

_REQUIRED_STATE_FIELDS = ("session_id", "iteration", "task", "next_node", "errors")

_logger = AgentLogger()


def _validate_state_entry(state: AgentState, trace_id: str) -> None:
    """Non-raising state guard — logs warnings only, never blocks execution."""
    for f in _REQUIRED_STATE_FIELDS:
        if f not in state:
            _logger.node_error("supervisor", trace_id, f"STATE_VALIDATION: missing '{f}'")
    if not isinstance(state.get("iteration"), int):
        _logger.node_error("supervisor", trace_id, "STATE_VALIDATION: 'iteration' not int")
    task = state.get("task")
    _valid_tasks = {"generate_seed", "mutate", "review", "done", "diagnose"}
    if task and task not in _valid_tasks:
        _logger.node_error("supervisor", trace_id, f"STATE_VALIDATION: unknown task='{task}'")


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: deterministic routing based on AgentState fields.

    This is a Stage 1 implementation — no LLM calls.  All routing decisions
    are rule-based, reading the current state fields.

    The function returns only the fields that changed (LangGraph merge semantics).

    Routing table
    -------------
    - ``task == "done"`` OR ``iteration >= MAX_ITERATIONS``  → END
    - ``backtest_run_id`` set AND ``diagnosis_summary`` is None
                                                            → backtest_diagnostics
    - ``task == "generate_seed"``                           → strategy_researcher
    - ``diagnosis_summary`` set AND ``discard == True``     → END (archive path)
    - ``diagnosis_summary`` set AND ``discard == False``    → strategy_researcher (mutate)
    - ``task == "mutate"`` AND ``backtest_run_id`` set      → strategy_researcher
    - fallback                                              → strategy_researcher
    """
    t0 = time.monotonic()

    trace_id = state.get("trace_id", "")
    _validate_state_entry(state, trace_id)
    state_keys = list(state.keys())
    _logger.node_enter("supervisor", trace_id, state_keys)

    task: str = state.get("task", "generate_seed")
    iteration: int = state.get("iteration", 0)
    backtest_run_id: str | None = state.get("backtest_run_id")
    diagnosis_summary: str | None = state.get("diagnosis_summary")
    discard: bool | None = state.get("discard")
    marker_action: str | None = state.get("marker_action")

    # ── Hard stop conditions ────────────────────────────────────────────────
    if task == "done" or iteration >= _MAX_ITERATIONS:
        next_node = "END"

    # ── Feature discovery mode (priority 2 — before strategy research) ──────
    elif state.get("research_mode") == "discover_features":
        next_node = "feature_researcher"

    # ── AutoML evaluation mode (priority 3) ──────────────────────────────────
    elif state.get("research_mode") == "automl_evaluation":
        next_node = "model_researcher"

    # ── Backtest result arrived — needs diagnosis (checked before generate_seed) ──
    elif backtest_run_id is not None and diagnosis_summary is None:
        next_node = "backtest_diagnostics"

    # ── Seed generation ─────────────────────────────────────────────────────
    elif task == "generate_seed":
        next_node = "strategy_researcher"

    # ── Diagnosis recommends discard (existing — unchanged) ─────────────────
    elif diagnosis_summary is not None and discard is True:
        next_node = "END"

    # ── DIME marker routing (evaluated after discard check) ─────────────────
    # "lock": breakthrough detected — seed next generation from locked candidate
    elif marker_action == "lock":
        next_node = "strategy_researcher"

    # "explore": high surprise or high uncertainty — widen mutation search
    elif marker_action == "explore":
        next_node = "strategy_researcher"

    # "exploit": consistent improvement trend — refine current best
    elif marker_action == "exploit":
        next_node = "generation_comparator"

    # ── Existing comparison / mutation routing (unchanged) ───────────────────
    # Multiple generations exist — compare candidates before mutation dispatch
    elif (
        state.get("generation", 0) >= 1
        and backtest_run_id is not None
        and diagnosis_summary is not None
        and discard is not True
        and state.get("comparison_result") is None
    ):
        next_node = "generation_comparator"

    # ── Diagnosis recommends mutation ────────────────────────────────────────
    elif diagnosis_summary is not None and discard is False:
        next_node = "strategy_researcher"

    # ── Explicit mutate task with prior backtest ─────────────────────────────
    elif task == "mutate" and backtest_run_id is not None:
        next_node = "strategy_researcher"

    # ── Fallback ─────────────────────────────────────────────────────────────
    else:
        next_node = "strategy_researcher"

    duration_ms = int((time.monotonic() - t0) * 1000)
    _logger.node_exit("supervisor", trace_id, duration_ms, next_node)

    return {
        "next_node": next_node,
        "iteration": iteration + 1,
    }


def route_next(state: AgentState) -> str:
    """Conditional edge function: read ``state["next_node"]`` and return it.

    LangGraph calls this after every supervisor execution to decide which node
    to invoke next.  All edges from the supervisor use this function.

    Returns ``"END"`` to terminate the graph.  The graph wires ``"END"`` to
    ``langgraph.graph.END`` in the conditional-edge path map.
    """
    return state.get("next_node", "END")
