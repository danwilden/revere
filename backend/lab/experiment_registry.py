"""Experiment registry — persistence layer for Phase 5B research experiments.

Each experiment record is stored as a JSON file in the metadata repository
under the "experiments" store name, keyed by experiment UUID.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ARCHIVED = "archived"
    VALIDATED = "validated"


class ExperimentRecord(BaseModel):
    id: str
    session_id: str
    parent_id: str | None = None
    generation: int = 0
    status: ExperimentStatus = ExperimentStatus.PENDING
    instrument: str
    timeframe: str
    test_start: str
    test_end: str
    model_id: str | None = None
    feature_run_id: str | None = None
    task: str = "generate_seed"
    requested_by: str = "system"
    hypothesis: str | None = None
    strategy_id: str | None = None
    backtest_run_id: str | None = None
    score: float | None = None
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    win_rate: float | None = None
    total_trades: int | None = None
    failure_taxonomy: str | None = None
    comparison_recommendation: str | None = None
    error_message: str | None = None
    discard_reason: str | None = None
    created_at: str
    updated_at: str
    final_state_snapshot: dict[str, Any] | None = None


class ExperimentRegistry:
    """Persist and query experiment records via LocalMetadataRepository.

    Records are stored in the "experiments" store — a flat dict of
    experiment_id -> record dict, using the same _upsert/_get/_list/_update
    pattern as all other entity types in LocalMetadataRepository.
    """

    _STORE = "experiments"

    def __init__(self, metadata_repo: Any) -> None:
        self._repo = metadata_repo

    def create(
        self,
        session_id: str,
        instrument: str,
        timeframe: str,
        test_start: str,
        test_end: str,
        task: str = "generate_seed",
        requested_by: str = "system",
        model_id: str | None = None,
        feature_run_id: str | None = None,
        parent_id: str | None = None,
        generation: int = 0,
    ) -> ExperimentRecord:
        """Create and persist a new experiment record."""
        now = datetime.now(tz=timezone.utc).isoformat()
        record = ExperimentRecord(
            id=str(uuid.uuid4()),
            session_id=session_id,
            parent_id=parent_id,
            generation=generation,
            status=ExperimentStatus.PENDING,
            instrument=instrument,
            timeframe=timeframe,
            test_start=test_start,
            test_end=test_end,
            model_id=model_id,
            feature_run_id=feature_run_id,
            task=task,
            requested_by=requested_by,
            created_at=now,
            updated_at=now,
        )
        self._repo._upsert(self._STORE, record.model_dump(mode="json"))
        return record

    def get(self, experiment_id: str) -> ExperimentRecord:
        """Return the experiment record for the given ID.

        Raises KeyError if not found.
        """
        raw = self._repo._get(self._STORE, experiment_id)
        if raw is None:
            raise KeyError(f"Experiment '{experiment_id}' not found")
        return ExperimentRecord.model_validate(raw)

    def update_status(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        *,
        hypothesis: str | None = None,
        strategy_id: str | None = None,
        backtest_run_id: str | None = None,
        score: float | None = None,
        sharpe: float | None = None,
        max_drawdown_pct: float | None = None,
        win_rate: float | None = None,
        total_trades: int | None = None,
        failure_taxonomy: str | None = None,
        comparison_recommendation: str | None = None,
        error_message: str | None = None,
        final_state_snapshot: dict[str, Any] | None = None,
    ) -> ExperimentRecord:
        """Update the status and any supplied non-None fields on an experiment.

        Only kwargs that are not None are written — existing values are
        preserved for kwargs that are left as None.
        """
        existing = self.get(experiment_id)
        updates: dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if hypothesis is not None:
            updates["hypothesis"] = hypothesis
        if strategy_id is not None:
            updates["strategy_id"] = strategy_id
        if backtest_run_id is not None:
            updates["backtest_run_id"] = backtest_run_id
        if score is not None:
            updates["score"] = score
        if sharpe is not None:
            updates["sharpe"] = sharpe
        if max_drawdown_pct is not None:
            updates["max_drawdown_pct"] = max_drawdown_pct
        if win_rate is not None:
            updates["win_rate"] = win_rate
        if total_trades is not None:
            updates["total_trades"] = total_trades
        if failure_taxonomy is not None:
            updates["failure_taxonomy"] = failure_taxonomy
        if comparison_recommendation is not None:
            updates["comparison_recommendation"] = comparison_recommendation
        if error_message is not None:
            updates["error_message"] = error_message
        if final_state_snapshot is not None:
            updates["final_state_snapshot"] = final_state_snapshot

        self._repo._update(self._STORE, experiment_id, updates)
        return self.get(experiment_id)

    def list_recent(
        self,
        limit: int = 20,
        instrument: str | None = None,
        status: ExperimentStatus | None = None,
    ) -> list[ExperimentRecord]:
        """Return experiments sorted by created_at descending.

        Optionally filter by instrument and/or status.
        """
        records = self._repo._list(self._STORE)
        if instrument is not None:
            records = [r for r in records if r.get("instrument") == instrument]
        if status is not None:
            records = [r for r in records if r.get("status") == status.value]
        records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return [ExperimentRecord.model_validate(r) for r in records[:limit]]

    def get_lineage(self, experiment_id: str) -> list[ExperimentRecord]:
        """Walk the parent_id chain and return records in generation order.

        Returns [root, child1, child2, ...] so index 0 is always the seed.
        Raises KeyError if the starting experiment does not exist.
        """
        # Build the chain from the requested ID upward to the root
        chain: list[ExperimentRecord] = []
        current_id: str | None = experiment_id
        seen: set[str] = set()

        while current_id is not None:
            if current_id in seen:
                # Guard against circular parent references
                break
            seen.add(current_id)
            record = self.get(current_id)  # raises KeyError for the first call
            chain.append(record)
            current_id = record.parent_id

        # chain is [requested, ..., root] — reverse for generation order
        chain.reverse()
        return chain
