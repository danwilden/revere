"""Research memory — persistent learnings from research runs.

Each memory record captures the theory behind an experiment, why results
came out as they did, actionable learnings, and thematic tags.
Stored in the 'research_memories' metadata store.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel


class ResearchMemory(BaseModel):
    id: str
    experiment_ids: list[str]
    instrument: str
    timeframe: str
    theory: str
    results_reasoning: str
    learnings: list[str]
    tags: list[str]
    outcome: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
    sharpe: float | None = None
    total_trades: int | None = None
    source: Literal["auto", "manual"] = "auto"
    created_at: str
    updated_at: str


def _derive_outcome(sharpe: float | None, total_trades: int | None) -> str:
    """POSITIVE if sharpe >= 0.3 AND total_trades >= 20, NEUTRAL if missing/insufficient, else NEGATIVE."""
    if sharpe is None or total_trades is None:
        return "NEUTRAL"
    if sharpe >= 0.3 and total_trades >= 20:
        return "POSITIVE"
    if total_trades < 20:
        return "NEUTRAL"
    return "NEGATIVE"


class ResearchMemoryStore:
    """Persist and query research memories via LocalMetadataRepository."""

    _STORE = "research_memories"

    def __init__(self, metadata_repo: Any) -> None:
        self._repo = metadata_repo

    def save(self, memory: ResearchMemory) -> ResearchMemory:
        """Upsert a memory record and return it."""
        self._repo._upsert(self._STORE, memory.model_dump(mode="json"))
        return memory

    def get(self, memory_id: str) -> ResearchMemory:
        """Return the memory record for the given ID. Raises KeyError if missing."""
        raw = self._repo._get(self._STORE, memory_id)
        if raw is None:
            raise KeyError(f"Research memory '{memory_id}' not found")
        return ResearchMemory.model_validate(raw)

    def list_all(self) -> list[ResearchMemory]:
        """Return all memory records sorted by created_at descending."""
        records = self._repo._list(self._STORE)
        records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return [ResearchMemory.model_validate(r) for r in records]

    def search(
        self,
        instrument: str | None = None,
        timeframe: str | None = None,
        tags: list[str] | None = None,
        outcome: str | None = None,
        limit: int = 20,
    ) -> list[ResearchMemory]:
        """Filter memories by instrument, timeframe, tags (any match), and outcome."""
        records = self._repo._list(self._STORE)
        if instrument is not None:
            records = [r for r in records if r.get("instrument") == instrument]
        if timeframe is not None:
            records = [r for r in records if r.get("timeframe") == timeframe]
        if outcome is not None:
            records = [r for r in records if r.get("outcome") == outcome]
        if tags is not None and tags:
            tags_set = set(t.lower() for t in tags)
            records = [
                r for r in records
                if any(t.lower() in tags_set for t in r.get("tags", []))
            ]
        records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return [ResearchMemory.model_validate(r) for r in records[:limit]]

    def get_context_for_run(
        self, instrument: str, timeframe: str, limit: int = 5
    ) -> list[ResearchMemory]:
        """Return memories most relevant to this instrument+timeframe run.

        Exact instrument+timeframe matches first, then same-instrument backfill.
        """
        all_records = self._repo._list(self._STORE)
        all_records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)

        exact: list[dict] = []
        same_instrument: list[dict] = []
        for r in all_records:
            if r.get("instrument") == instrument and r.get("timeframe") == timeframe:
                exact.append(r)
            elif r.get("instrument") == instrument:
                same_instrument.append(r)

        combined = exact + same_instrument
        combined = combined[:limit]
        return [ResearchMemory.model_validate(r) for r in combined]
