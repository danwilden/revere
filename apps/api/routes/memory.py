"""Memory routes — research knowledge graph persistence and retrieval.

GET  /api/memories                   list with optional filters
GET  /api/memories/graph             graph nodes + edges + stats
GET  /api/memories/{memory_id}       single memory
POST /api/memories                   manual memory creation
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.deps import get_experiment_registry, get_memory_store
from backend.lab.research_memory import ResearchMemory, ResearchMemoryStore, _derive_outcome
from backend.lab.experiment_registry import ExperimentRegistry

router = APIRouter(tags=["memory"])


class CreateMemoryRequest(BaseModel):
    experiment_ids: list[str] = []
    instrument: str
    timeframe: str
    theory: str
    results_reasoning: str
    learnings: list[str]
    tags: list[str]
    sharpe: float | None = None
    total_trades: int | None = None


@router.get("", response_model=list[ResearchMemory])
async def list_memories(
    instrument: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    tags: str | None = Query(default=None, description="Comma-separated tags"),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    store: ResearchMemoryStore = Depends(get_memory_store),
) -> list[ResearchMemory]:
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    return store.search(instrument=instrument, timeframe=timeframe, tags=tag_list, outcome=outcome, limit=limit)


@router.get("/graph")
async def get_memory_graph(
    store: ResearchMemoryStore = Depends(get_memory_store),
    registry: ExperimentRegistry = Depends(get_experiment_registry),
) -> dict[str, Any]:
    """Build a graph of memory nodes, experiment nodes, and relationship edges."""
    memories = store.list_all()
    experiments = registry.list_recent(limit=200)

    nodes: list[dict] = []
    edges: list[dict] = []

    # Memory nodes
    for mem in memories:
        nodes.append({
            "id": f"mem_{mem.id}",
            "type": "memory",
            "label": f"{mem.instrument} {mem.timeframe} {mem.outcome}",
            "instrument": mem.instrument,
            "timeframe": mem.timeframe,
            "outcome": mem.outcome,
            "sharpe": mem.sharpe,
            "total_trades": mem.total_trades,
            "tags": mem.tags,
            "theory": mem.theory[:120],
            "created_at": mem.created_at,
        })

    # Experiment nodes
    for exp in experiments:
        nodes.append({
            "id": f"exp_{exp.id}",
            "type": "experiment",
            "label": f"GEN {exp.generation} {exp.status.value.upper()}",
            "instrument": exp.instrument,
            "timeframe": exp.timeframe,
            "status": exp.status.value,
            "sharpe": exp.sharpe,
            "generation": exp.generation,
            "created_at": exp.created_at,
        })

    # memory_of edges
    for mem in memories:
        for exp_id in mem.experiment_ids:
            edges.append({
                "source": f"mem_{mem.id}",
                "target": f"exp_{exp_id}",
                "type": "memory_of",
            })

    # same_tags edges (capped at 50, sorted by shared-tag-count desc)
    tag_edges: list[dict] = []
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
    tag_edges.sort(key=lambda e: len(e["shared_tags"]), reverse=True)
    edges.extend(tag_edges[:50])

    # lineage edges (parent_id)
    for exp in experiments:
        if exp.parent_id is not None:
            edges.append({
                "source": f"exp_{exp.parent_id}",
                "target": f"exp_{exp.id}",
                "type": "lineage",
            })

    # same_session edges
    session_map: dict[str, list[str]] = defaultdict(list)
    for exp in experiments:
        session_map[exp.session_id].append(exp.id)
    for session_id, exp_ids in session_map.items():
        if len(exp_ids) > 1:
            for j in range(len(exp_ids) - 1):
                edges.append({
                    "source": f"exp_{exp_ids[j]}",
                    "target": f"exp_{exp_ids[j + 1]}",
                    "type": "same_session",
                })

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_memories": len(memories),
            "total_experiments": len(experiments),
            "total_edges": len(edges),
        },
    }


@router.get("/{memory_id}", response_model=ResearchMemory)
async def get_memory(
    memory_id: str,
    store: ResearchMemoryStore = Depends(get_memory_store),
) -> ResearchMemory:
    try:
        return store.get(memory_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")


@router.post("", response_model=ResearchMemory, status_code=201)
async def create_memory(
    body: CreateMemoryRequest,
    store: ResearchMemoryStore = Depends(get_memory_store),
) -> ResearchMemory:
    now = datetime.now(tz=timezone.utc).isoformat()
    memory = ResearchMemory(
        id=str(uuid.uuid4()),
        experiment_ids=body.experiment_ids,
        instrument=body.instrument,
        timeframe=body.timeframe,
        theory=body.theory,
        results_reasoning=body.results_reasoning,
        learnings=body.learnings,
        tags=body.tags,
        outcome=_derive_outcome(body.sharpe, body.total_trades),
        sharpe=body.sharpe,
        total_trades=body.total_trades,
        source="manual",
        created_at=now,
        updated_at=now,
    )
    return store.save(memory)
