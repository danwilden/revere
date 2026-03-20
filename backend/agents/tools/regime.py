"""Regime context tool executor for the agent research layer.

Retrieves HMM regime context for a given symbol and timeframe via the
Medallion backend API. Used by strategy_researcher_node to populate the
regime_context field in AgentState before building the LLM user message.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.agents.tools.client import MedallionClient, ToolCallError
from backend.agents.tools.schemas import GetRegimeContextInput, RegimeContext

logger = logging.getLogger(__name__)


async def get_regime_context(
    inp: GetRegimeContextInput,
    client: MedallionClient,
) -> RegimeContext:
    """Load HMM regime context for a given model and symbol.

    Calls GET /api/models/hmm/{model_id} to get model metadata including
    label_map and state_stats. Falls back gracefully if model not found.

    Parameters
    ----------
    inp:
        Input containing model_id, instrument, and timeframe.
    client:
        MedallionClient for HTTP requests to the backend API.

    Returns
    -------
    RegimeContext
        Populated context, or a context with the ``error`` field set if
        the model could not be loaded.
    """
    try:
        raw = await client.get(
            f"/api/models/hmm/{inp.model_id}",
            tool_name="get_regime_context",
        )
    except ToolCallError as exc:
        logger.warning(
            "Failed to load HMM model %s: %s", inp.model_id, exc
        )
        return RegimeContext(
            model_id=inp.model_id,
            instrument=inp.instrument,
            timeframe=inp.timeframe,
            num_states=0,
            label_map={},
            state_stats=[],
            error=f"HTTP {exc.status_code}: {exc.detail}",
        )

    # Extract fields from the API response (ModelRecordResponse shape).
    label_map: dict[str, str] = raw.get("label_map_json", {})

    # num_states: prefer parameters_json, fall back to label_map length
    parameters_json = raw.get("parameters_json", {})
    if isinstance(parameters_json, str):
        try:
            parameters_json = json.loads(parameters_json)
        except (json.JSONDecodeError, TypeError):
            parameters_json = {}
    num_states: int = parameters_json.get("num_states", len(label_map))

    # state_stats: stored as state_stats_json (JSON string) on the model
    # record after training completes. Not part of ModelRecordResponse
    # schema, so may be absent from the API response.
    state_stats_raw = raw.get("state_stats_json")
    state_stats: list[dict[str, Any]] = []
    if state_stats_raw is not None:
        if isinstance(state_stats_raw, str):
            try:
                state_stats = json.loads(state_stats_raw)
            except (json.JSONDecodeError, TypeError):
                state_stats = []
        elif isinstance(state_stats_raw, list):
            state_stats = state_stats_raw

    # Derive current_regime_label from the most frequent state in state_stats
    current_regime_label: str | None = None
    regime_probabilities: dict[str, float] = {}
    if state_stats and label_map:
        # Build regime_probabilities from frequency_pct in state_stats
        for ss in state_stats:
            sid = str(ss.get("state_id", ""))
            label = label_map.get(sid)
            freq = ss.get("frequency_pct")
            if label and freq is not None:
                regime_probabilities[label] = round(float(freq) / 100.0, 4)

        # Current regime = highest frequency state (best guess without live data)
        best = max(state_stats, key=lambda s: s.get("frequency_pct", 0.0))
        best_sid = str(best.get("state_id", ""))
        current_regime_label = label_map.get(best_sid)

    return RegimeContext(
        model_id=inp.model_id,
        instrument=inp.instrument,
        timeframe=inp.timeframe,
        num_states=num_states,
        label_map=label_map,
        state_stats=state_stats,
        current_regime_label=current_regime_label,
        regime_probabilities=regime_probabilities,
    )


async def load_regime_context_from_state(
    state: dict[str, Any],
    client: MedallionClient,
) -> dict[str, Any] | None:
    """Return regime_context dict suitable for writing to AgentState.

    Checks whether ``regime_context`` is already populated in *state*.
    If so, returns that value without making any API calls. If not, and
    ``model_id`` is present, calls :func:`get_regime_context` to load
    fresh context from the backend.

    Parameters
    ----------
    state:
        Current AgentState dict (or any dict with the expected keys).
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    dict or None
        A dict representation of :class:`RegimeContext` suitable for
        merging into AgentState, or ``None`` if no model_id is available.
    """
    # Already populated — return as-is
    existing = state.get("regime_context")
    if existing is not None:
        return existing

    # No model to load from
    model_id = state.get("model_id")
    if not model_id:
        return None

    inp = GetRegimeContextInput(
        model_id=model_id,
        instrument=state.get("instrument", ""),
        timeframe=state.get("timeframe", ""),
    )

    context = await get_regime_context(inp, client)
    return context.model_dump(mode="json")
