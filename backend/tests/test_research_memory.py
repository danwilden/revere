"""Tests for the Research Memory storage and writer."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.data.local_metadata import LocalMetadataRepository
from backend.lab.research_memory import (
    ResearchMemory,
    ResearchMemoryStore,
    _derive_outcome,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path: Path) -> LocalMetadataRepository:
    return LocalMetadataRepository(tmp_path)


@pytest.fixture
def store(repo: LocalMetadataRepository) -> ResearchMemoryStore:
    return ResearchMemoryStore(repo)


def _make_memory(store: ResearchMemoryStore, **kwargs) -> ResearchMemory:
    now = datetime.now(tz=timezone.utc).isoformat()
    defaults = dict(
        id=str(uuid.uuid4()),
        experiment_ids=[str(uuid.uuid4())],
        instrument="EUR_USD",
        timeframe="H4",
        theory="Test theory",
        results_reasoning="Test reasoning",
        learnings=["Learning 1", "Learning 2"],
        tags=["momentum", "eur_usd"],
        outcome="NEUTRAL",
        sharpe=None,
        total_trades=None,
        source="auto",
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    memory = ResearchMemory(**defaults)
    return store.save(memory)


# ---------------------------------------------------------------------------
# _derive_outcome
# ---------------------------------------------------------------------------

class TestDeriveOutcome:
    def test_both_none_is_neutral(self):
        assert _derive_outcome(None, None) == "NEUTRAL"

    def test_sharpe_none_is_neutral(self):
        assert _derive_outcome(None, 30) == "NEUTRAL"

    def test_trades_none_is_neutral(self):
        assert _derive_outcome(0.5, None) == "NEUTRAL"

    def test_positive(self):
        assert _derive_outcome(0.3, 20) == "POSITIVE"

    def test_positive_high_sharpe(self):
        assert _derive_outcome(1.5, 50) == "POSITIVE"

    def test_insufficient_trades_is_neutral(self):
        assert _derive_outcome(0.8, 10) == "NEUTRAL"

    def test_low_sharpe_is_negative(self):
        assert _derive_outcome(0.1, 30) == "NEGATIVE"

    def test_negative_sharpe_is_negative(self):
        assert _derive_outcome(-0.5, 30) == "NEGATIVE"

    def test_exactly_threshold_positive(self):
        assert _derive_outcome(0.3, 20) == "POSITIVE"

    def test_just_below_threshold_negative(self):
        assert _derive_outcome(0.29, 20) == "NEGATIVE"


# ---------------------------------------------------------------------------
# ResearchMemoryStore: save + get
# ---------------------------------------------------------------------------

class TestSaveAndGet:
    def test_save_and_get_roundtrip(self, store):
        mem = _make_memory(store)
        retrieved = store.get(mem.id)
        assert retrieved.id == mem.id
        assert retrieved.theory == "Test theory"
        assert retrieved.instrument == "EUR_USD"

    def test_get_missing_raises_key_error(self, store):
        with pytest.raises(KeyError):
            store.get("nonexistent-id")

    def test_save_returns_memory(self, store):
        now = datetime.now(tz=timezone.utc).isoformat()
        mem = ResearchMemory(
            id=str(uuid.uuid4()),
            experiment_ids=[],
            instrument="GBP_USD",
            timeframe="H1",
            theory="GBP theory",
            results_reasoning="reasons",
            learnings=[],
            tags=["gbp"],
            outcome="NEGATIVE",
            created_at=now,
            updated_at=now,
        )
        result = store.save(mem)
        assert result.id == mem.id


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------

class TestListAll:
    def test_empty_list(self, store):
        assert store.list_all() == []

    def test_returns_all_memories(self, store):
        _make_memory(store)
        _make_memory(store)
        assert len(store.list_all()) == 2

    def test_sorted_newest_first(self, store):
        import time
        m1 = _make_memory(store, created_at="2024-01-01T00:00:00+00:00")
        m2 = _make_memory(store, created_at="2024-06-01T00:00:00+00:00")
        all_mems = store.list_all()
        assert all_mems[0].id == m2.id
        assert all_mems[1].id == m1.id


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_by_instrument(self, store):
        _make_memory(store, instrument="EUR_USD")
        _make_memory(store, instrument="GBP_USD")
        results = store.search(instrument="EUR_USD")
        assert len(results) == 1
        assert results[0].instrument == "EUR_USD"

    def test_search_by_timeframe(self, store):
        _make_memory(store, timeframe="H1")
        _make_memory(store, timeframe="H4")
        results = store.search(timeframe="H1")
        assert len(results) == 1
        assert results[0].timeframe == "H1"

    def test_search_by_outcome(self, store):
        _make_memory(store, outcome="POSITIVE")
        _make_memory(store, outcome="NEGATIVE")
        results = store.search(outcome="POSITIVE")
        assert len(results) == 1
        assert results[0].outcome == "POSITIVE"

    def test_search_by_tags_any_match(self, store):
        _make_memory(store, tags=["momentum", "h4"])
        _make_memory(store, tags=["mean-reversion", "daily"])
        results = store.search(tags=["momentum"])
        assert len(results) == 1

    def test_search_no_filters_returns_all(self, store):
        _make_memory(store)
        _make_memory(store)
        assert len(store.search()) == 2

    def test_search_limit(self, store):
        for _ in range(5):
            _make_memory(store)
        results = store.search(limit=3)
        assert len(results) == 3

    def test_search_combined_filters(self, store):
        _make_memory(store, instrument="EUR_USD", outcome="POSITIVE")
        _make_memory(store, instrument="EUR_USD", outcome="NEGATIVE")
        _make_memory(store, instrument="GBP_USD", outcome="POSITIVE")
        results = store.search(instrument="EUR_USD", outcome="POSITIVE")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# get_context_for_run
# ---------------------------------------------------------------------------

class TestGetContextForRun:
    def test_exact_match_returned(self, store):
        _make_memory(store, instrument="EUR_USD", timeframe="H4")
        _make_memory(store, instrument="EUR_USD", timeframe="H1")
        results = store.get_context_for_run("EUR_USD", "H4")
        assert len(results) == 2
        assert results[0].timeframe == "H4"  # exact match first

    def test_exact_match_first_then_same_instrument(self, store):
        _make_memory(store, instrument="EUR_USD", timeframe="H4", theory="Exact")
        _make_memory(store, instrument="EUR_USD", timeframe="H1", theory="Backfill")
        results = store.get_context_for_run("EUR_USD", "H4", limit=5)
        assert results[0].theory == "Exact"
        assert results[1].theory == "Backfill"

    def test_fallback_to_same_instrument(self, store):
        _make_memory(store, instrument="EUR_USD", timeframe="H1")
        results = store.get_context_for_run("EUR_USD", "H4")
        assert len(results) == 1
        assert results[0].instrument == "EUR_USD"

    def test_limit_respected(self, store):
        for _ in range(10):
            _make_memory(store, instrument="EUR_USD", timeframe="H4")
        results = store.get_context_for_run("EUR_USD", "H4", limit=3)
        assert len(results) == 3

    def test_empty_when_no_match(self, store):
        _make_memory(store, instrument="GBP_USD", timeframe="H1")
        results = store.get_context_for_run("EUR_USD", "H4")
        assert results == []


# ---------------------------------------------------------------------------
# memory_writer
# ---------------------------------------------------------------------------

class TestMemoryWriter:
    @pytest.mark.asyncio
    async def test_extracts_fields_from_bedrock_response(self, store, repo):
        # Set up experiment in metadata repo
        exp_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        repo._upsert("experiments", {
            "id": exp_id,
            "status": "succeeded",
            "instrument": "EUR_USD",
            "timeframe": "H4",
            "hypothesis": "Test hypothesis",
            "sharpe": 0.45,
            "total_trades": 35,
            "win_rate": 0.55,
            "max_drawdown_pct": -8.0,
            "failure_taxonomy": None,
            "strategy_id": None,
            "created_at": now,
            "updated_at": now,
        })

        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.content = json.dumps({
            "theory": "EUR/USD shows momentum in H4",
            "results_reasoning": "High ADX led to wins",
            "learnings": ["Use ADX > 25", "Avoid Fridays"],
            "tags": ["momentum", "eur_usd", "adx"],
        })
        mock_adapter.converse = AsyncMock(return_value=mock_result)

        from backend.agents.memory_writer import write_memory_for_experiment
        result = await write_memory_for_experiment(exp_id, repo, store, mock_adapter)

        assert result is not None
        assert result.theory == "EUR/USD shows momentum in H4"
        assert result.learnings == ["Use ADX > 25", "Avoid Fridays"]
        assert result.tags == ["momentum", "eur_usd", "adx"]
        assert result.instrument == "EUR_USD"
        assert result.timeframe == "H4"

    @pytest.mark.asyncio
    async def test_positive_outcome_derived(self, store, repo):
        exp_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        repo._upsert("experiments", {
            "id": exp_id, "status": "succeeded", "instrument": "EUR_USD",
            "timeframe": "H4", "sharpe": 0.5, "total_trades": 25,
            "created_at": now, "updated_at": now,
        })
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.content = json.dumps({
            "theory": "t", "results_reasoning": "r",
            "learnings": ["l"], "tags": ["tag"],
        })
        mock_adapter.converse = AsyncMock(return_value=mock_result)

        from backend.agents.memory_writer import write_memory_for_experiment
        result = await write_memory_for_experiment(exp_id, repo, store, mock_adapter)
        assert result.outcome == "POSITIVE"

    @pytest.mark.asyncio
    async def test_negative_outcome_derived(self, store, repo):
        exp_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        repo._upsert("experiments", {
            "id": exp_id, "status": "succeeded", "instrument": "EUR_USD",
            "timeframe": "H4", "sharpe": -0.2, "total_trades": 30,
            "created_at": now, "updated_at": now,
        })
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.content = json.dumps({
            "theory": "t", "results_reasoning": "r",
            "learnings": [], "tags": [],
        })
        mock_adapter.converse = AsyncMock(return_value=mock_result)

        from backend.agents.memory_writer import write_memory_for_experiment
        result = await write_memory_for_experiment(exp_id, repo, store, mock_adapter)
        assert result.outcome == "NEGATIVE"

    @pytest.mark.asyncio
    async def test_neutral_outcome_when_trades_low(self, store, repo):
        exp_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        repo._upsert("experiments", {
            "id": exp_id, "status": "succeeded", "instrument": "EUR_USD",
            "timeframe": "H4", "sharpe": 1.0, "total_trades": 5,
            "created_at": now, "updated_at": now,
        })
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.content = json.dumps({
            "theory": "t", "results_reasoning": "r",
            "learnings": [], "tags": [],
        })
        mock_adapter.converse = AsyncMock(return_value=mock_result)

        from backend.agents.memory_writer import write_memory_for_experiment
        result = await write_memory_for_experiment(exp_id, repo, store, mock_adapter)
        assert result.outcome == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_returns_none_for_running_experiment(self, store, repo):
        exp_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        repo._upsert("experiments", {
            "id": exp_id, "status": "running", "instrument": "EUR_USD",
            "timeframe": "H4", "created_at": now, "updated_at": now,
        })
        mock_adapter = MagicMock()
        mock_adapter.converse = AsyncMock()

        from backend.agents.memory_writer import write_memory_for_experiment
        result = await write_memory_for_experiment(exp_id, repo, store, mock_adapter)
        assert result is None
        mock_adapter.converse.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_bedrock_error(self, store, repo):
        exp_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        repo._upsert("experiments", {
            "id": exp_id, "status": "succeeded", "instrument": "EUR_USD",
            "timeframe": "H4", "sharpe": 0.5, "total_trades": 25,
            "created_at": now, "updated_at": now,
        })
        mock_adapter = MagicMock()
        mock_adapter.converse = AsyncMock(side_effect=RuntimeError("Bedrock down"))

        from backend.agents.memory_writer import write_memory_for_experiment
        result = await write_memory_for_experiment(exp_id, repo, store, mock_adapter)
        assert result is None  # never raises


# ---------------------------------------------------------------------------
# Graph edge logic
# ---------------------------------------------------------------------------

class TestGraphEdges:
    def test_same_tags_edge_generated(self, store):
        _make_memory(store, tags=["momentum", "adx"], id=str(uuid.uuid4()))
        _make_memory(store, tags=["momentum", "rsi"], id=str(uuid.uuid4()))
        memories = store.list_all()
        assert len(memories) == 2

        # Simulate edge building
        tag_edges = []
        for i, m1 in enumerate(memories):
            for m2 in memories[i + 1:]:
                shared = list(set(m1.tags) & set(m2.tags))
                if shared:
                    tag_edges.append({
                        "source": f"mem_{m1.id}",
                        "target": f"mem_{m2.id}",
                        "type": "same_tags",
                        "shared_tags": shared,
                    })
        assert len(tag_edges) == 1
        assert "momentum" in tag_edges[0]["shared_tags"]

    def test_memory_of_edge_correct_keys(self):
        exp_id = str(uuid.uuid4())
        mem_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        mem = ResearchMemory(
            id=mem_id, experiment_ids=[exp_id], instrument="EUR_USD",
            timeframe="H4", theory="t", results_reasoning="r",
            learnings=[], tags=[], outcome="NEUTRAL",
            created_at=now, updated_at=now,
        )
        edge = {"source": f"mem_{mem.id}", "target": f"exp_{exp_id}", "type": "memory_of"}
        assert edge["source"] == f"mem_{mem_id}"
        assert edge["target"] == f"exp_{exp_id}"
        assert edge["type"] == "memory_of"
