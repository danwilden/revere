"""Tests for the regime context tool executor and schemas.

All tests are deterministic — HTTP calls are mocked via AsyncMock.
"""
from __future__ import annotations

import json

import pytest

from backend.agents.tools.client import MedallionClient, ToolCallError
from backend.agents.tools.schemas import (
    GetRegimeContextInput,
    RegimeContext,
    RegimeSnapshot,
)
from backend.agents.tools.regime import get_regime_context, load_regime_context_from_state


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_model_response(
    *,
    model_id: str = "hmm-model-001",
    instrument_id: str = "EUR_USD",
    timeframe: str = "H4",
    num_states: int = 7,
    include_state_stats: bool = True,
) -> dict:
    """Build a fake ModelRecordResponse-shaped dict as the API would return."""
    label_map = {
        "0": "TREND_BULL_LOW_VOL",
        "1": "TREND_BULL_HIGH_VOL",
        "2": "TREND_BEAR_LOW_VOL",
        "3": "TREND_BEAR_HIGH_VOL",
        "4": "RANGE_MEAN_REVERT",
        "5": "CHOPPY_SIGNAL",
        "6": "CHOPPY_NOISE",
    }
    state_stats = [
        {"state_id": 0, "label": "TREND_BULL_LOW_VOL", "mean_return": 0.0003,
         "mean_adx": 28.5, "mean_volatility": 0.0012, "frequency_pct": 18.0},
        {"state_id": 1, "label": "TREND_BULL_HIGH_VOL", "mean_return": 0.0005,
         "mean_adx": 35.0, "mean_volatility": 0.0025, "frequency_pct": 10.0},
        {"state_id": 2, "label": "TREND_BEAR_LOW_VOL", "mean_return": -0.0002,
         "mean_adx": 26.0, "mean_volatility": 0.0010, "frequency_pct": 15.0},
        {"state_id": 3, "label": "TREND_BEAR_HIGH_VOL", "mean_return": -0.0006,
         "mean_adx": 32.0, "mean_volatility": 0.0030, "frequency_pct": 8.0},
        {"state_id": 4, "label": "RANGE_MEAN_REVERT", "mean_return": 0.0000,
         "mean_adx": 15.0, "mean_volatility": 0.0008, "frequency_pct": 25.0},
        {"state_id": 5, "label": "CHOPPY_SIGNAL", "mean_return": 0.0001,
         "mean_adx": 18.0, "mean_volatility": 0.0015, "frequency_pct": 14.0},
        {"state_id": 6, "label": "CHOPPY_NOISE", "mean_return": -0.0001,
         "mean_adx": 12.0, "mean_volatility": 0.0020, "frequency_pct": 10.0},
    ]

    resp: dict = {
        "id": model_id,
        "model_type": "hmm",
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "training_start": "2024-01-01T00:00:00",
        "training_end": "2024-06-01T00:00:00",
        "parameters_json": {
            "num_states": num_states,
            "feature_set_name": "default_v1",
            "feature_run_id": "feat-run-001",
        },
        "artifact_ref": "models/hmm/hmm-model-001.joblib",
        "label_map_json": label_map,
        "created_at": "2024-06-01T12:00:00",
        "status": "SUCCEEDED",
    }

    if include_state_stats:
        resp["state_stats_json"] = json.dumps(state_stats)

    return resp


def _make_client_mock(return_value=None, side_effect=None):
    """Create a MedallionClient mock with a controlled get() response."""
    from unittest.mock import AsyncMock

    client = MedallionClient.__new__(MedallionClient)
    client.get = AsyncMock(return_value=return_value, side_effect=side_effect)
    client.post = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Test 1: get_regime_context — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_regime_context_success():
    """When the API returns valid model data, RegimeContext is fully populated."""
    raw = _make_model_response()
    client = _make_client_mock(return_value=raw)

    inp = GetRegimeContextInput(
        model_id="hmm-model-001",
        instrument="EUR_USD",
        timeframe="H4",
    )

    ctx = await get_regime_context(inp, client)

    assert isinstance(ctx, RegimeContext)
    assert ctx.model_id == "hmm-model-001"
    assert ctx.instrument == "EUR_USD"
    assert ctx.timeframe == "H4"
    assert ctx.num_states == 7
    assert ctx.error is None

    # label_map populated
    assert ctx.label_map["0"] == "TREND_BULL_LOW_VOL"
    assert len(ctx.label_map) == 7

    # state_stats populated
    assert len(ctx.state_stats) == 7
    assert ctx.state_stats[0]["state_id"] == 0

    # regime_probabilities computed from frequency_pct
    assert "RANGE_MEAN_REVERT" in ctx.regime_probabilities
    assert ctx.regime_probabilities["RANGE_MEAN_REVERT"] == pytest.approx(0.25, abs=0.01)

    # current_regime_label = highest frequency state (RANGE_MEAN_REVERT at 25%)
    assert ctx.current_regime_label == "RANGE_MEAN_REVERT"

    # Client was called with correct path
    client.get.assert_called_once_with(
        "/api/models/hmm/hmm-model-001",
        tool_name="get_regime_context",
    )


# ---------------------------------------------------------------------------
# Test 2: get_regime_context — model not found (404)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_regime_context_model_not_found():
    """When the API returns 404, RegimeContext has error field set — no exception raised."""
    client = _make_client_mock(
        side_effect=ToolCallError("get_regime_context", 404, "Model not found"),
    )

    inp = GetRegimeContextInput(
        model_id="nonexistent",
        instrument="EUR_USD",
        timeframe="H4",
    )

    ctx = await get_regime_context(inp, client)

    assert isinstance(ctx, RegimeContext)
    assert ctx.error is not None
    assert "404" in ctx.error
    assert ctx.num_states == 0
    assert ctx.label_map == {}
    assert ctx.state_stats == []


# ---------------------------------------------------------------------------
# Test 3: load_regime_context_from_state — already set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_regime_context_from_state_already_set():
    """When state already has regime_context, returns it without calling client."""
    existing_ctx = {"model_id": "abc", "instrument": "EUR_USD", "timeframe": "H4"}
    state = {
        "regime_context": existing_ctx,
        "model_id": "abc",
        "instrument": "EUR_USD",
        "timeframe": "H4",
    }

    client = _make_client_mock()

    result = await load_regime_context_from_state(state, client)

    assert result == existing_ctx
    client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: load_regime_context_from_state — no model_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_regime_context_from_state_no_model_id():
    """When state has no model_id, returns None."""
    state = {
        "regime_context": None,
        "model_id": None,
        "instrument": "EUR_USD",
        "timeframe": "H4",
    }

    client = _make_client_mock()

    result = await load_regime_context_from_state(state, client)

    assert result is None
    client.get.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: load_regime_context_from_state — loads fresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_regime_context_from_state_loads_fresh():
    """When model_id is set and regime_context is None, calls get_regime_context."""
    raw = _make_model_response(model_id="hmm-999")
    client = _make_client_mock(return_value=raw)

    state = {
        "regime_context": None,
        "model_id": "hmm-999",
        "instrument": "GBP_USD",
        "timeframe": "D",
    }

    result = await load_regime_context_from_state(state, client)

    assert result is not None
    assert isinstance(result, dict)
    assert result["model_id"] == "hmm-999"
    assert result["instrument"] == "GBP_USD"
    assert result["timeframe"] == "D"
    assert result["num_states"] == 7
    assert result["error"] is None

    client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: RegimeContext schema validation
# ---------------------------------------------------------------------------

def test_regime_context_schema_validation():
    """RegimeContext model accepts valid data and serializes correctly."""
    ctx = RegimeContext(
        model_id="m-001",
        instrument="EUR_USD",
        timeframe="H4",
        num_states=7,
        label_map={"0": "TREND_BULL_LOW_VOL", "1": "RANGE_MEAN_REVERT"},
        state_stats=[{"state_id": 0, "label": "TREND_BULL_LOW_VOL", "frequency_pct": 50.0}],
        current_regime_label="TREND_BULL_LOW_VOL",
        regime_probabilities={"TREND_BULL_LOW_VOL": 0.5},
        signal_bank_snapshot={"hmm_regime": 0.5},
    )

    assert ctx.model_id == "m-001"
    assert ctx.num_states == 7
    assert ctx.error is None

    # Round-trip through model_dump
    d = ctx.model_dump(mode="json")
    assert d["model_id"] == "m-001"
    assert d["label_map"]["0"] == "TREND_BULL_LOW_VOL"
    assert d["regime_probabilities"]["TREND_BULL_LOW_VOL"] == 0.5

    # Reconstruct from dict
    ctx2 = RegimeContext.model_validate(d)
    assert ctx2.model_id == ctx.model_id
    assert ctx2.num_states == ctx.num_states


# ---------------------------------------------------------------------------
# Test 7: RegimeSnapshot schema validation
# ---------------------------------------------------------------------------

def test_regime_snapshot_schema_validation():
    """RegimeSnapshot model accepts valid data and serializes correctly."""
    snap = RegimeSnapshot(
        timestamp="2024-06-01T12:00:00Z",
        state_id=3,
        label="TREND_BEAR_HIGH_VOL",
        probability=0.87,
    )

    assert snap.timestamp == "2024-06-01T12:00:00Z"
    assert snap.state_id == 3
    assert snap.label == "TREND_BEAR_HIGH_VOL"
    assert snap.probability == 0.87

    # Optional fields default to None
    snap_minimal = RegimeSnapshot(
        timestamp="2024-01-01T00:00:00Z",
        state_id=0,
    )
    assert snap_minimal.label is None
    assert snap_minimal.probability is None

    # Round-trip
    d = snap.model_dump(mode="json")
    snap_rt = RegimeSnapshot.model_validate(d)
    assert snap_rt.state_id == snap.state_id
    assert snap_rt.probability == snap.probability
