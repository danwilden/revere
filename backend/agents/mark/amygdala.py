"""AmygdalaSignal — breakthrough detection and lock signal.

Fires when the current sharpe beats the best-known sharpe by more than
AMYGDALA_THRESHOLD_PCT.  A breakthrough locks the candidate so downstream
nodes can seed the next generation from it.
"""
from __future__ import annotations

AMYGDALA_THRESHOLD_PCT: float = 0.05
AMYGDALA_DECAY: float = 0.85


class AmygdalaSignal:
    """Tracks best-known sharpe and detects significant improvements."""

    def __init__(self) -> None:
        self._best_known_sharpe: float | None = None
        self._locked_candidates: set[str] = set()
        self._current_score: float = 0.0

    def update(self, sharpe: float | None, candidate_id: str) -> float:
        """Update the signal with the latest sharpe and candidate ID.

        If sharpe improves on best_known by > AMYGDALA_THRESHOLD_PCT, score=1.0
        and the candidate is locked.  Otherwise score decays by AMYGDALA_DECAY.
        """
        if sharpe is None:
            self._current_score *= AMYGDALA_DECAY
            return self._current_score

        if self._best_known_sharpe is None:
            # First observation — record baseline, no breakthrough yet
            self._best_known_sharpe = sharpe
            self._current_score *= AMYGDALA_DECAY
            return self._current_score

        threshold = self._best_known_sharpe * (1.0 + AMYGDALA_THRESHOLD_PCT)
        if sharpe > threshold:
            self._current_score = 1.0
            self._best_known_sharpe = sharpe
            self._locked_candidates.add(candidate_id)
        else:
            self._current_score *= AMYGDALA_DECAY

        return self._current_score

    def is_breakthrough(self, candidate_id: str) -> bool:
        """Return True if candidate_id is in the locked breakthrough set."""
        return candidate_id in self._locked_candidates
