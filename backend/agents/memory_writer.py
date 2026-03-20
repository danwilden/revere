"""Memory writer — extracts and persists research learnings after a run completes.

This module provides a single async function that fires after a research
graph run completes. It calls Bedrock once with a structured extraction
prompt to distil the experiment into a ResearchMemory record.

Best-effort: the entire function is wrapped in try/except so it never
blocks or fails a research run.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.agents.providers.bedrock import BedrockAdapter
from backend.lab.research_memory import ResearchMemory, ResearchMemoryStore, _derive_outcome

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM_PROMPT = """You are a research memory extractor for a Forex trading strategy research system.
Given an experiment record, extract structured learnings in strict JSON format.

Return ONLY a JSON object with these exact keys:
{
  "theory": "<1-3 sentences: what hypothesis drove this experiment>",
  "results_reasoning": "<1-3 sentences: why the results came out this way>",
  "learnings": ["<actionable bullet 1>", "<actionable bullet 2>", ...],
  "tags": ["<theme1>", "<theme2>", ...]
}

Rules:
- theory: concise hypothesis statement
- results_reasoning: causal explanation of the outcome
- learnings: 2-5 actionable insights a future researcher should know
- tags: 2-6 short thematic labels (e.g. "momentum", "mean-reversion", "eur_usd", "low-trades", "high-volatility")
- Output ONLY valid JSON, no markdown, no preamble
"""


async def write_memory_for_experiment(
    experiment_id: str,
    metadata_repo: Any,
    memory_store: ResearchMemoryStore,
    bedrock_adapter: BedrockAdapter,
) -> ResearchMemory | None:
    """Extract and persist a ResearchMemory for the given experiment.

    Returns the saved memory on success, None on any failure.
    Never raises.
    """
    try:
        # Load experiment
        raw = metadata_repo._get("experiments", experiment_id)
        if raw is None:
            logger.warning("memory_writer: experiment %s not found", experiment_id)
            return None

        # Only extract for terminal experiments
        status = raw.get("status", "")
        if status not in ("succeeded", "archived", "failed"):
            logger.debug("memory_writer: skipping experiment %s with status %s", experiment_id, status)
            return None

        # Build extraction prompt
        strategy_id = raw.get("strategy_id")
        strategy_def: dict = {}
        if strategy_id:
            strat_raw = metadata_repo._get("strategies", strategy_id)
            if strat_raw:
                strategy_def = strat_raw.get("definition_json", {})

        prompt_data = {
            "experiment_id": experiment_id,
            "instrument": raw.get("instrument"),
            "timeframe": raw.get("timeframe"),
            "status": status,
            "hypothesis": raw.get("hypothesis"),
            "failure_taxonomy": raw.get("failure_taxonomy"),
            "sharpe": raw.get("sharpe"),
            "total_trades": raw.get("total_trades"),
            "win_rate": raw.get("win_rate"),
            "max_drawdown_pct": raw.get("max_drawdown_pct"),
            "strategy_definition": strategy_def,
        }

        user_message = (
            "Extract research memory from this experiment:\n\n"
            + json.dumps(prompt_data, indent=2, default=str)
        )

        result = await bedrock_adapter.converse(
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            tools=None,
            max_tokens=1024,
            temperature=0.1,
        )

        # Parse JSON from response
        extracted = json.loads(result.content)

        sharpe = raw.get("sharpe")
        total_trades = raw.get("total_trades")
        if isinstance(sharpe, (int, float)):
            sharpe = float(sharpe)
        else:
            sharpe = None
        if isinstance(total_trades, int):
            total_trades = int(total_trades)
        else:
            total_trades = None

        outcome = _derive_outcome(sharpe, total_trades)

        now = datetime.now(tz=timezone.utc).isoformat()
        memory = ResearchMemory(
            id=str(uuid.uuid4()),
            experiment_ids=[experiment_id],
            instrument=raw.get("instrument", ""),
            timeframe=raw.get("timeframe", ""),
            theory=extracted.get("theory", ""),
            results_reasoning=extracted.get("results_reasoning", ""),
            learnings=extracted.get("learnings", []),
            tags=extracted.get("tags", []),
            outcome=outcome,
            sharpe=sharpe,
            total_trades=total_trades,
            source="auto",
            created_at=now,
            updated_at=now,
        )

        saved = memory_store.save(memory)
        logger.info("memory_writer: saved memory %s for experiment %s", saved.id, experiment_id)
        return saved

    except Exception as exc:
        logger.warning("memory_writer: failed for experiment %s: %s", experiment_id, exc)
        return None
