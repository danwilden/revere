"""MarkerSystem — orchestrates all four neuromodulator signals.

This module is the single source of truth for all signal weights and thresholds.
It is instantiated once at graph build time (closure pattern) and called as a
LangGraph node after backtest_diagnostics and before the supervisor routes.

No Bedrock calls, no I/O, no DB access.  Pure computation on AgentState.
"""
from __future__ import annotations

from typing import Any

from backend.agents.mark.amygdala import AmygdalaSignal
from backend.agents.mark.dopamine import DopamineSignal
from backend.agents.mark.noradrenaline import NoradrenalineSignal
from backend.agents.mark.serotonin import SerotoninSignal

# ── Signal weights (must sum to 1.0) ────────────────────────────────────────
W_DOPAMINE: float = 0.30
W_SEROTONIN: float = 0.20
W_NORA: float = 0.25
W_AMYGDALA: float = 0.25

# ── Per-signal constants (canonical definitions — individual modules re-use
#    these via their own module-level assignments) ────────────────────────────
DOPAMINE_SCALE: float = 0.4
DOPAMINE_WINDOW: int = 10

SEROTONIN_SCALE: float = 10.0
SEROTONIN_WINDOW: int = 5
SEROTONIN_EXPLOIT_THR: float = 0.6

NORA_SCALE: float = 3.0
NORA_WINDOW: int = 8
NORA_PIVOT_THR: float = 0.65
CONSECUTIVE_FAIL_SPIKE: int = 3

AMYGDALA_THRESHOLD_PCT: float = 0.05
AMYGDALA_DECAY: float = 0.85


class MarkerSystem:
    """Orchestrates dopamine, serotonin, noradrenaline, and amygdala signals.

    Instantiate once at graph build time so signal state persists across
    LangGraph node invocations within a single research session.

    Call evaluate(state) as the mark_node to get the dict to merge into AgentState:
      {
          "marker_scores": {"dopamine": float, "serotonin": float,
                            "noradrenaline": float, "amygdala": float},
          "marker_action": "lock" | "explore" | "exploit" | "continue",
          "composite_score": float,
      }
    """

    def __init__(self) -> None:
        self._dopamine = DopamineSignal()
        self._serotonin = SerotoninSignal()
        self._noradrenaline = NoradrenalineSignal()
        self._amygdala = AmygdalaSignal()

    def evaluate(self, state: Any) -> dict[str, Any]:
        """Read AgentState, update all signals, emit marker_action and scores."""
        candidates: list[dict[str, Any]] = state.get("strategy_candidates") or []
        diagnostic_summary: dict[str, Any] = state.get("diagnostic_summary") or {}
        failure_taxonomy: str | None = diagnostic_summary.get("failure_taxonomy")

        sharpe: float | None = None
        candidate_id: str = ""
        if candidates:
            last = candidates[-1]
            raw_sharpe = last.get("sharpe")
            if raw_sharpe is not None:
                try:
                    sharpe = float(raw_sharpe)
                except (TypeError, ValueError):
                    sharpe = None
            candidate_id = str(last.get("candidate_id", ""))

        # Update all four signals
        dopamine_score = self._dopamine.update(sharpe)
        serotonin_score = self._serotonin.update(sharpe)
        nora_score = self._noradrenaline.update(sharpe, failure_taxonomy)
        amygdala_score = self._amygdala.update(sharpe, candidate_id)

        # Weighted composite
        composite = (
            W_DOPAMINE * dopamine_score
            + W_SEROTONIN * serotonin_score
            + W_NORA * nora_score
            + W_AMYGDALA * amygdala_score
        )

        # Marker action — first match wins (priority order per spec)
        if amygdala_score >= 0.9:
            marker_action = "lock"
        elif dopamine_score > 0.7 or nora_score > 0.65:
            marker_action = "explore"
        elif serotonin_score > 0.6:
            marker_action = "exploit"
        else:
            marker_action = "continue"

        return {
            "marker_scores": {
                "dopamine": dopamine_score,
                "serotonin": serotonin_score,
                "noradrenaline": nora_score,
                "amygdala": amygdala_score,
            },
            "marker_action": marker_action,
            "composite_score": composite,
        }
