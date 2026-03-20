"""DopamineSignal — prediction-error / surprise signal.

Fires when the observed sharpe deviates significantly from the running
expectation (measured via Welford online mean/variance).  High dopamine
suggests surprising results (good or bad) and widens mutation search.
"""
from __future__ import annotations

import math
from collections import deque

DOPAMINE_WINDOW: int = 10
DOPAMINE_SCALE: float = 0.4


class DopamineSignal:
    """Tracks sharpe prediction error across generations.

    Uses a Welford online algorithm for numerically stable mean and variance
    so the signal never needs to store the full history for recomputation.
    """

    def __init__(self) -> None:
        self._history: deque[float] = deque(maxlen=DOPAMINE_WINDOW)
        self._mean: float = 0.0
        self._M2: float = 0.0      # sum of squared deviations (Welford)
        self._count: int = 0
        self._current_score: float = 0.5

    def update(self, sharpe: float | None) -> float:
        """Update the signal with the latest sharpe and return the new score."""
        if sharpe is None:
            self._current_score = 0.5
            return self._current_score

        # Welford online update for mean and M2
        self._count += 1
        delta = sharpe - self._mean
        self._mean += delta / self._count
        delta2 = sharpe - self._mean
        self._M2 += delta * delta2
        self._history.append(sharpe)

        if self._count < 2:
            # Not enough history for a z-score
            self._current_score = 0.5
            return self._current_score

        variance = self._M2 / (self._count - 1)
        std = math.sqrt(variance) if variance > 0.0 else 0.0

        if std == 0.0:
            z_score = 0.0
        else:
            z_score = (sharpe - self._mean) / std

        # sigmoid(|z| - 1) clamped to [0, 1]
        raw = 1.0 / (1.0 + math.exp(-(abs(z_score) - 1.0)))
        self._current_score = max(0.0, min(1.0, raw))
        return self._current_score

    def get_exploration_bonus(self) -> float:
        """Return current score scaled by DOPAMINE_SCALE."""
        return self._current_score * DOPAMINE_SCALE
