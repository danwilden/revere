"""LangGraph StateGraph definition for the Phase 5 supervisor research loop."""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from backend.agents.backtest_diagnostics import backtest_diagnostics_node
from backend.agents.feature_researcher import feature_researcher_node
from backend.agents.generation_comparator import generation_comparator_node
from backend.agents.mark import build_mark_node
from backend.agents.model_researcher import model_researcher_node
from backend.agents.state import AgentState
from backend.agents.strategy_researcher import strategy_researcher_node
from backend.agents.supervisor import route_next, supervisor_node


def build_graph():
    """Build and compile the Phase 5 supervisor research graph.

    Topology
    --------
    - Entry point: ``supervisor``
    - Conditional edges from ``supervisor`` use :func:`route_next` which reads
      ``state["next_node"]`` written by :func:`~backend.agents.supervisor.supervisor_node`.
    - ``backtest_diagnostics`` routes to ``mark`` (DIME marker layer) before
      returning to ``supervisor`` so marker_action is available for routing.
    - All other worker nodes return unconditionally to ``supervisor``.
    - ``"END"`` in the path map resolves to :data:`langgraph.graph.END`.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph graph ready for ``.invoke()`` or ``.stream()``.
    """
    graph: StateGraph = StateGraph(AgentState)

    # ── Register nodes ──────────────────────────────────────────────────────
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("strategy_researcher", strategy_researcher_node)
    graph.add_node("backtest_diagnostics", backtest_diagnostics_node)
    graph.add_node("generation_comparator", generation_comparator_node)
    graph.add_node("feature_researcher", feature_researcher_node)
    graph.add_node("model_researcher", model_researcher_node)

    # mark_node is a closure capturing a shared MarkerSystem instance
    graph.add_node("mark", build_mark_node())

    # ── Entry point ─────────────────────────────────────────────────────────
    graph.set_entry_point("supervisor")

    # ── Conditional edges from supervisor ───────────────────────────────────
    # ``route_next`` reads ``state["next_node"]``; the path map translates the
    # string value to either a node name or the END sentinel.
    graph.add_conditional_edges(
        "supervisor",
        route_next,
        {
            "strategy_researcher": "strategy_researcher",
            "backtest_diagnostics": "backtest_diagnostics",
            "generation_comparator": "generation_comparator",
            "feature_researcher": "feature_researcher",
            "model_researcher": "model_researcher",
            "END": END,
        },
    )

    # ── Worker node edges ────────────────────────────────────────────────────
    graph.add_edge("strategy_researcher", "supervisor")
    # backtest_diagnostics → mark (DIME marker layer) → supervisor
    graph.add_edge("backtest_diagnostics", "mark")
    graph.add_edge("mark", "supervisor")
    graph.add_edge("generation_comparator", "supervisor")
    graph.add_edge("feature_researcher", "supervisor")
    graph.add_edge("model_researcher", "supervisor")

    return graph.compile()
