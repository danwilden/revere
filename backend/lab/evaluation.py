"""Experiment scoring and comparison for Phase 5B research evaluation.

Gate constants define the minimum acceptable thresholds for an experiment
to be considered viable.  Scores are composites over normalised sub-metrics
and are independent from whether a gate is passed.
"""
from __future__ import annotations

from pydantic import BaseModel

from backend.lab.experiment_registry import ExperimentRecord

# ---------------------------------------------------------------------------
# Gate constants
# ---------------------------------------------------------------------------

MIN_TRADE_COUNT: int = 20
MIN_SHARPE: float = 0.1
MAX_DRAWDOWN_PCT: float = -30.0
MIN_WIN_RATE: float = 0.35


# ---------------------------------------------------------------------------
# Score model
# ---------------------------------------------------------------------------

class ExperimentScore(BaseModel):
    experiment_id: str
    composite_score: float
    sharpe_score: float
    drawdown_score: float
    activity_score: float
    win_rate_score: float
    sharpe: float | None
    max_drawdown_pct: float | None
    total_trades: int | None
    win_rate: float | None
    passed_minimum_gates: bool
    gate_failures: list[str]


# ---------------------------------------------------------------------------
# Scoring algorithm
# ---------------------------------------------------------------------------

def score_experiment(record: ExperimentRecord) -> ExperimentScore:
    """Compute a composite score and minimum-gate assessment for one experiment.

    Sub-metric formulas (from architecture spec Section 6.2):
      sharpe_score   = min(max(sharpe / 2.0, 0.0), 1.0)
      drawdown_score = max(0.0, 1.0 - abs(max_drawdown_pct) / 50.0)
      activity_score = min(total_trades / 50.0, 1.0)
      win_rate_score = min(max((win_rate - 0.35) / 0.30, 0.0), 1.0)
      composite      = 0.40*sharpe + 0.30*drawdown + 0.15*activity + 0.15*win_rate

    A None metric is treated as 0.0 for scoring purposes.
    Gate failures are accumulated independently from scores.
    """
    sharpe = record.sharpe
    max_dd = record.max_drawdown_pct
    trades = record.total_trades
    win_rate = record.win_rate

    # Sub-scores (None → 0.0)
    sharpe_score = min(max(sharpe / 2.0, 0.0), 1.0) if sharpe is not None else 0.0
    drawdown_score = (
        max(0.0, 1.0 - abs(max_dd) / 50.0) if max_dd is not None else 0.0
    )
    activity_score = min(trades / 50.0, 1.0) if trades is not None else 0.0
    win_rate_score = (
        min(max((win_rate - 0.35) / 0.30, 0.0), 1.0) if win_rate is not None else 0.0
    )

    composite = (
        0.40 * sharpe_score
        + 0.30 * drawdown_score
        + 0.15 * activity_score
        + 0.15 * win_rate_score
    )

    # Gate checks
    gate_failures: list[str] = []
    if trades is None or trades < MIN_TRADE_COUNT:
        gate_failures.append(
            f"total_trades {trades} < MIN_TRADE_COUNT {MIN_TRADE_COUNT}"
        )
    if sharpe is None or sharpe < MIN_SHARPE:
        gate_failures.append(f"sharpe {sharpe} < MIN_SHARPE {MIN_SHARPE}")
    if max_dd is None or max_dd < MAX_DRAWDOWN_PCT:
        gate_failures.append(
            f"max_drawdown_pct {max_dd} < MAX_DRAWDOWN_PCT {MAX_DRAWDOWN_PCT}"
        )
    if win_rate is None or win_rate < MIN_WIN_RATE:
        gate_failures.append(f"win_rate {win_rate} < MIN_WIN_RATE {MIN_WIN_RATE}")

    return ExperimentScore(
        experiment_id=record.id,
        composite_score=composite,
        sharpe_score=sharpe_score,
        drawdown_score=drawdown_score,
        activity_score=activity_score,
        win_rate_score=win_rate_score,
        sharpe=sharpe,
        max_drawdown_pct=max_dd,
        total_trades=trades,
        win_rate=win_rate,
        passed_minimum_gates=len(gate_failures) == 0,
        gate_failures=gate_failures,
    )


def compare_experiments(records: list[ExperimentRecord]) -> list[ExperimentScore]:
    """Score every record and return sorted descending by composite_score."""
    scores = [score_experiment(r) for r in records]
    scores.sort(key=lambda s: s.composite_score, reverse=True)
    return scores
