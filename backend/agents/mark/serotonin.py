"""SerotoninSignal — improving-trend signal.

Rises when sharpe improves consistently across recent generations.
High serotonin indicates the current mutation direction is working,
so the agent should exploit rather than explore.
"""
from __future__ import annotations

import math
from collections import deque

SEROTONIN_WINDOW: int = 5
SEROTONIN_SCALE: float = 10.0
SEROTONIN_EXPLOIT_THR: float = 0.6


class SerotoninSignal:
    """Tracks consistent improvement in sharpe via linear regression slope."""

    def __init__(self) -> None:
        self._history: deque[float] = deque(maxlen=SEROTONIN_WINDOW)
        self._current_score: float = 0.0

    def update(self, sharpe: float | None) -> float:
        """Update the signal with the latest sharpe and return the new score."""
        if sharpe is None:
            return self._current_score

        self._history.append(sharpe)

        if len(self._history) < 2:
            self._current_score = 0.0
            return self._current_score

        values = list(self._history)
        n = len(values)
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        slope = numerator / denominator if denominator > 0.0 else 0.0

        # tanh(slope * scale) clamped to [0, 1]
        raw = math.tanh(slope * SEROTONIN_SCALE)
        self._current_score = max(0.0, min(1.0, raw))
        return self._current_score

    def should_exploit(self) -> bool:
        """Return True if the current score exceeds the exploit threshold."""
        return self._current_score > SEROTONIN_EXPLOIT_THR
