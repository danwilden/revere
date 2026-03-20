"""NoradrenalineSignal — uncertainty / volatility signal.

Spikes when metric variance is high or the same failure repeats across
generations.  High noradrenaline triggers a pivot to structurally different
mutations.
"""
from __future__ import annotations

import math
from collections import deque

NORA_WINDOW: int = 8
NORA_SCALE: float = 3.0
NORA_PIVOT_THR: float = 0.65
CONSECUTIVE_FAIL_SPIKE: int = 3


class NoradrenalineSignal:
    """Tracks coefficient of variation in sharpe and repeated failure taxonomy."""

    def __init__(self) -> None:
        self._history: deque[float] = deque(maxlen=NORA_WINDOW)
        self._taxonomy_history: list[str | None] = []
        self._current_score: float = 0.0

    def update(self, sharpe: float | None, failure_taxonomy: str | None) -> float:
        """Update the signal with latest sharpe + failure taxonomy, return new score."""
        if sharpe is not None:
            self._history.append(sharpe)
        self._taxonomy_history.append(failure_taxonomy)

        score = self._compute_cv_score()

        # Override if the same non-positive failure repeats too many times
        if self._consecutive_failures() >= CONSECUTIVE_FAIL_SPIKE:
            score = 0.9

        self._current_score = score
        return self._current_score

    def _compute_cv_score(self) -> float:
        """Coefficient of variation mapped through tanh to [0, 1]."""
        if len(self._history) < 2:
            return 0.0
        values = list(self._history)
        mean = sum(values) / len(values)
        if mean == 0.0:
            return 0.0
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        cv = std / abs(mean)
        return max(0.0, min(1.0, math.tanh(cv * NORA_SCALE)))

    def _consecutive_failures(self) -> int:
        """Count how many times the most recent taxonomy appears consecutively."""
        if not self._taxonomy_history:
            return 0
        last = self._taxonomy_history[-1]
        if last is None or last == "positive":
            return 0
        count = 0
        for t in reversed(self._taxonomy_history):
            if t == last:
                count += 1
            else:
                break
        return count

    def should_pivot(self) -> bool:
        """Return True if the current score exceeds the pivot threshold."""
        return self._current_score > NORA_PIVOT_THR
