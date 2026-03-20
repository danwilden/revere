"""DIME marker layer — neuromodulator-inspired signal system for the research loop.

Public API
----------
MarkerSystem
    Orchestrates all four signals.  Instantiate once at graph build time.
build_mark_node()
    Factory that returns a closure-captured mark_node suitable for LangGraph.
"""
from __future__ import annotations

from typing import Any

from backend.agents.mark.marker_system import MarkerSystem


def build_mark_node():
    """Create a mark_node function with a shared MarkerSystem instance.

    The MarkerSystem is captured by closure so its internal signal state
    (rolling history, best-known sharpe, locked candidates) persists across
    LangGraph node invocations within the same process lifetime.

    Returns
    -------
    Callable[[AgentState], dict]
        A synchronous LangGraph node function.
    """
    marker_system = MarkerSystem()

    def mark_node(state: Any) -> dict[str, Any]:
        return marker_system.evaluate(state)

    return mark_node


__all__ = ["MarkerSystem", "build_mark_node"]
