"""Unit tests for feature_researcher_node and supervisor routing for Phase 5C.

All tests are deterministic — Bedrock, DuckDB, and sandbox are fully mocked.
No real network calls or subprocess spawning.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.agents.feature_researcher import (
    MAX_FEATURES_PER_SESSION,
    NODE_NAME,
    _build_user_message,
    _collect_session_results,
    feature_researcher_node,
)
from backend.agents.state import DEFAULT_STATE
from backend.agents.supervisor import supervisor_node
from backend.agents.tools.feature import (
    _FEATURE_EVAL_CACHE,
    _FEATURE_SERIES_CACHE,
    _FEATURE_SPEC_CACHE,
    propose_feature,
    register_feature,
)
from backend.agents.tools.schemas import (
    ALLOWED_FAMILIES,
    FeatureEvalResult,
    FeatureSpec,
    ProposeFeatureInput,
    RegisterFeatureInput,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> dict[str, Any]:
    state = dict(DEFAULT_STATE("test-session-001"))
    state.update(overrides)
    return state


def _make_converse_result(stop_reason: str = "end_turn", content: str = "", tool_use: dict | None = None):
    from backend.agents.providers.bedrock import ConverseResult
    return ConverseResult(
        stop_reason=stop_reason,
        content=content,
        tool_use=tool_use,
        input_tokens=10,
        output_tokens=20,
    )


def _make_tool_use_result(tool_name: str, inp: dict, tool_use_id: str = "tu-001"):
    return _make_converse_result(
        stop_reason="tool_use",
        content="",
        tool_use={"name": tool_name, "input": inp, "toolUseId": tool_use_id},
    )


# ---------------------------------------------------------------------------
# Tests: propose_feature tool
# ---------------------------------------------------------------------------

class TestProposeFeature:
    @pytest.mark.asyncio
    async def test_valid_spec_returns_success(self):
        inp = ProposeFeatureInput(spec={
            "name": "ema_ratio_10_50",
            "family": "momentum",
            "formula_description": "EMA(10) / EMA(50) ratio",
            "lookback_bars": 50,
            "dependency_columns": ["close"],
            "transformation": "rolling_ratio",
            "expected_intuition": "Captures trend direction",
            "leakage_risk": "none",
            "code": "result = df['close'].ewm(span=10).mean() / df['close'].ewm(span=50).mean() - 1",
        })
        client = MagicMock()
        out = await propose_feature(inp, client)
        assert out.valid is True
        assert out.errors == []
        assert out.spec is not None
        assert out.spec["name"] == "ema_ratio_10_50"
        # Cached
        assert "ema_ratio_10_50" in _FEATURE_SPEC_CACHE

    @pytest.mark.asyncio
    async def test_invalid_family_rejected(self):
        inp = ProposeFeatureInput(spec={
            "name": "bad_family_feat",
            "family": "fundamental_analysis",  # NOT in ALLOWED_FAMILIES
            "formula_description": "...",
            "lookback_bars": 10,
            "dependency_columns": ["close"],
            "transformation": "ratio",
            "expected_intuition": "...",
            "leakage_risk": "none",
            "code": "result = df['close']",
        })
        client = MagicMock()
        out = await propose_feature(inp, client)
        assert out.valid is False
        assert any("family" in e for e in out.errors)
        assert out.spec is None

    @pytest.mark.asyncio
    async def test_missing_required_field_rejected(self):
        inp = ProposeFeatureInput(spec={"name": "incomplete_spec"})
        client = MagicMock()
        out = await propose_feature(inp, client)
        assert out.valid is False
        assert len(out.errors) > 0

    @pytest.mark.asyncio
    async def test_all_allowed_families_pass(self):
        client = MagicMock()
        for family in ALLOWED_FAMILIES:
            inp = ProposeFeatureInput(spec={
                "name": f"test_{family}_feat",
                "family": family,
                "formula_description": "test",
                "lookback_bars": 10,
                "dependency_columns": ["close"],
                "transformation": "test",
                "expected_intuition": "test",
                "leakage_risk": "none",
                "code": "result = df['close']",
            })
            out = await propose_feature(inp, client)
            assert out.valid is True, f"Family '{family}' should be valid but was rejected"


# ---------------------------------------------------------------------------
# Tests: feature_researcher_node (mocked Bedrock)
# ---------------------------------------------------------------------------

class TestFeatureResearcherNode:

    def _make_end_turn_sequence(self):
        """Returns a sequence of converse() results: tool_use x4 then end_turn."""
        propose_result = _make_tool_use_result("propose_feature", {
            "spec": {
                "name": "test_vol_ratio",
                "family": "volatility",
                "formula_description": "Short/long vol ratio",
                "lookback_bars": 40,
                "dependency_columns": ["close"],
                "transformation": "rolling_ratio",
                "expected_intuition": "High ratio = vol expansion",
                "leakage_risk": "none",
                "code": "result = df['close'].rolling(10).std() / df['close'].rolling(40).std()",
            }
        }, "tu-001")
        compute_result = _make_tool_use_result("compute_feature", {
            "feature_name": "test_vol_ratio",
            "code": "result = df['close'].rolling(10).std() / df['close'].rolling(40).std()",
            "instrument": "EUR_USD",
            "timeframe": "H4",
            "start": "2024-01-01",
            "end": "2024-06-01",
        }, "tu-002")
        evaluate_result = _make_tool_use_result("evaluate_feature", {
            "feature_name": "test_vol_ratio",
            "instrument": "EUR_USD",
            "timeframe": "H4",
            "start": "2024-01-01",
            "end": "2024-06-01",
            "model_id": "model-123",
        }, "tu-003")
        register_result = _make_tool_use_result("register_feature", {
            "feature_name": "test_vol_ratio",
        }, "tu-004")
        end_turn = _make_converse_result("end_turn", '{"features_proposed": 1, "features_registered": 1, "results": []}')
        return [propose_result, compute_result, evaluate_result, register_result, end_turn]

    @patch("backend.agents.feature_researcher.BedrockAdapter")
    @patch("backend.agents.feature_researcher.MedallionClient")
    @patch("backend.deps.get_market_repo")
    @patch("backend.deps.get_feature_library")
    def test_full_lifecycle_writes_feature_eval_results(
        self, mock_get_lib, mock_get_repo, mock_client_cls, mock_adapter_cls
    ):
        """Full lifecycle: state in → LLM calls mocked → FeatureEvalResult in state."""
        # Setup mocked series and regime labels
        mock_repo = MagicMock()
        mock_repo.get_bars_agg.return_value = [
            {"instrument_id": "EUR_USD", "timeframe": "H4",
             "timestamp_utc": f"2024-01-{d:02d}T00:00:00", "open": 1.1, "high": 1.11,
             "low": 1.09, "close": 1.10 + d * 0.001, "volume": 100.0,
             "source": "test", "derivation_version": "1"}
            for d in range(1, 51)
        ]
        mock_repo.get_regime_labels.return_value = [
            {"model_id": "model-123", "instrument_id": "EUR_USD", "timeframe": "H4",
             "timestamp_utc": f"2024-01-{d:02d}T00:00:00",
             "state_id": d % 2, "regime_label": "TREND_BULL_LOW_VOL" if d % 2 == 0 else "TREND_BEAR_LOW_VOL",
             "state_probabilities_json": "{}"}
            for d in range(1, 51)
        ]
        mock_get_repo.return_value = mock_repo

        # Mock FeatureLibrary
        mock_library = MagicMock()
        mock_library.list_all.return_value = []
        mock_library.get.return_value = None
        mock_get_lib.return_value = mock_library

        # Mock BedrockAdapter
        mock_adapter = MagicMock()
        mock_adapter._model_id = "test-model"
        sequence = self._make_end_turn_sequence()
        mock_adapter.converse = AsyncMock(side_effect=sequence)
        mock_adapter_cls.return_value = mock_adapter

        # Mock extract_tool_use to return proper tuples
        with patch("backend.agents.feature_researcher.BedrockAdapter.extract_tool_use") as mock_extract:
            mock_extract.side_effect = [
                ("propose_feature", sequence[0].tool_use["input"]),
                ("compute_feature", sequence[1].tool_use["input"]),
                ("evaluate_feature", sequence[2].tool_use["input"]),
                ("register_feature", sequence[3].tool_use["input"]),
                None,
            ]
            # Inject a real FeatureEvalResult into cache to simulate evaluate_feature running
            test_eval = FeatureEvalResult(
                feature_name="test_vol_ratio",
                f_statistic=3.5,
                regime_breakdown={"TREND_BULL_LOW_VOL": 1.2, "TREND_BEAR_LOW_VOL": 0.8},
                leakage_risk="none",
                registered=True,
            )
            _FEATURE_EVAL_CACHE["test_vol_ratio"] = test_eval

            state = _make_state(
                research_mode="discover_features",
                instrument="EUR_USD",
                timeframe="H4",
                test_start="2024-01-01",
                test_end="2024-06-01",
                model_id="model-123",
            )

            result = feature_researcher_node(state)

        assert "feature_eval_results" in result
        assert result["research_mode"] is None  # trigger cleared
        assert result["task"] == "done"
        assert result["next_node"] == "supervisor"

    @patch("backend.agents.feature_researcher.BedrockAdapter")
    @patch("backend.agents.feature_researcher.MedallionClient")
    def test_invalid_family_rejected_before_compute(self, mock_client_cls, mock_adapter_cls):
        """Invalid family in propose spec → errors returned to LLM, compute not called."""
        bad_propose = _make_tool_use_result("propose_feature", {
            "spec": {
                "name": "illegal_feature",
                "family": "NOT_ALLOWED",
                "formula_description": "...",
                "lookback_bars": 10,
                "dependency_columns": ["close"],
                "transformation": "ratio",
                "expected_intuition": "...",
                "leakage_risk": "none",
                "code": "result = df['close']",
            }
        }, "tu-bad")
        end_turn = _make_converse_result("end_turn", '{"features_proposed": 0, "features_registered": 0, "results": []}')

        mock_adapter = MagicMock()
        mock_adapter._model_id = "test-model"
        mock_adapter.converse = AsyncMock(side_effect=[bad_propose, end_turn])
        mock_adapter_cls.return_value = mock_adapter

        compute_called = []

        with patch("backend.agents.feature_researcher.BedrockAdapter.extract_tool_use") as mock_extract:
            mock_extract.side_effect = [
                ("propose_feature", bad_propose.tool_use["input"]),
                None,
            ]
            with patch("backend.deps.get_feature_library") as mock_lib:
                mock_lib.return_value.list_all.return_value = []
                with patch("backend.deps.get_market_repo") as mock_repo:
                    # If compute is called, record it
                    mock_repo.return_value.get_bars_agg.side_effect = lambda *a, **kw: compute_called.append(True) or []

                    state = _make_state(research_mode="discover_features")
                    feature_researcher_node(state)

        # compute should NOT have been called for an invalid family
        assert not compute_called

    @patch("backend.agents.feature_researcher.BedrockAdapter")
    @patch("backend.agents.feature_researcher.MedallionClient")
    def test_research_mode_cleared_in_return(self, mock_client_cls, mock_adapter_cls):
        """research_mode must be None in return dict regardless of success/failure."""
        end_turn = _make_converse_result("end_turn", '{"features_proposed": 0, "features_registered": 0, "results": []}')
        mock_adapter = MagicMock()
        mock_adapter._model_id = "test-model"
        mock_adapter.converse = AsyncMock(return_value=end_turn)
        mock_adapter_cls.return_value = mock_adapter

        with patch("backend.deps.get_feature_library") as mock_lib:
            mock_lib.return_value.list_all.return_value = []
            state = _make_state(research_mode="discover_features")
            result = feature_researcher_node(state)

        assert result["research_mode"] is None

    @patch("backend.agents.feature_researcher.BedrockAdapter")
    @patch("backend.agents.feature_researcher.MedallionClient")
    def test_throttling_exception_sets_error(self, mock_client_cls, mock_adapter_cls):
        """ThrottlingException after all retries sets state.errors and task=done."""
        import botocore.exceptions
        throttle = botocore.exceptions.ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "throttled"}},
            "Converse",
        )
        mock_adapter = MagicMock()
        mock_adapter._model_id = "test-model"
        # Always throttle
        mock_adapter.converse = AsyncMock(side_effect=throttle)
        mock_adapter_cls.return_value = mock_adapter

        with patch("backend.agents.feature_researcher.asyncio.sleep", new_callable=AsyncMock):
            with patch("backend.deps.get_feature_library") as mock_lib:
                mock_lib.return_value.list_all.return_value = []
                state = _make_state(research_mode="discover_features")
                result = feature_researcher_node(state)

        assert result["task"] == "done"
        assert any("ThrottlingException" in e for e in result.get("errors", []))
        assert result["research_mode"] is None

    def test_collect_session_results_only_includes_cached(self):
        """_collect_session_results returns only features that have cached eval results."""
        eval_a = FeatureEvalResult(
            feature_name="feat_a",
            f_statistic=2.5,
            regime_breakdown={},
            leakage_risk="none",
            registered=True,
        )
        _FEATURE_EVAL_CACHE["feat_a"] = eval_a
        # feat_b not in cache

        results = _collect_session_results(["feat_a", "feat_b", "feat_c"])
        assert len(results) == 1
        assert results[0]["feature_name"] == "feat_a"

        # Cleanup
        _FEATURE_EVAL_CACHE.pop("feat_a", None)


# ---------------------------------------------------------------------------
# Tests: supervisor routing for research_mode
# ---------------------------------------------------------------------------

class TestSupervisorPhase5CRouting:

    def test_research_mode_routes_to_feature_researcher(self):
        """research_mode='discover_features' routes to feature_researcher."""
        state = _make_state(research_mode="discover_features", task="generate_seed")
        result = supervisor_node(state)
        assert result["next_node"] == "feature_researcher"

    def test_research_mode_takes_priority_over_generate_seed(self):
        """research_mode routing fires before task='generate_seed' routing."""
        state = _make_state(research_mode="discover_features", task="generate_seed")
        result = supervisor_node(state)
        # Must route to feature_researcher, not strategy_researcher
        assert result["next_node"] == "feature_researcher"

    def test_none_research_mode_does_not_route_to_feature_researcher(self):
        """research_mode=None routes normally (not to feature_researcher)."""
        state = _make_state(research_mode=None, task="generate_seed")
        result = supervisor_node(state)
        assert result["next_node"] == "strategy_researcher"

    def test_task_done_overrides_research_mode(self):
        """task='done' terminates regardless of research_mode."""
        state = _make_state(research_mode="discover_features", task="done")
        result = supervisor_node(state)
        assert result["next_node"] == "END"

    def test_max_iterations_overrides_research_mode(self):
        """iteration >= 10 terminates regardless of research_mode."""
        state = _make_state(research_mode="discover_features", task="generate_seed", iteration=10)
        result = supervisor_node(state)
        assert result["next_node"] == "END"

    def test_iteration_incremented_on_feature_discovery_route(self):
        """iteration is incremented when routing to feature_researcher."""
        state = _make_state(research_mode="discover_features", iteration=2)
        result = supervisor_node(state)
        assert result["iteration"] == 3


# ---------------------------------------------------------------------------
# Tests: build_user_message
# ---------------------------------------------------------------------------

class TestBuildUserMessage:

    @patch("backend.deps.get_feature_library")
    def test_message_contains_required_fields(self, mock_lib):
        mock_lib.return_value.list_all.return_value = []
        state = _make_state(
            instrument="GBP_USD",
            timeframe="H1",
            test_start="2024-01-01",
            test_end="2024-03-01",
            model_id="model-abc",
        )
        msg = _build_user_message(state)
        assert "GBP_USD" in msg
        assert "H1" in msg
        assert "2024-01-01" in msg
        assert "model-abc" in msg
        assert "MAX_FEATURES" in msg


# ---------------------------------------------------------------------------
# Tests: register_feature blocks leakage=high unconditionally
# ---------------------------------------------------------------------------

class TestRegisterFeatureLeakageBlock:

    @pytest.mark.asyncio
    async def test_leakage_high_blocked_regardless_of_f_statistic(self):
        """leakage_risk='high' must be blocked even with F=10.0."""
        spec = FeatureSpec(
            name="future_ret_feat",
            family="momentum",
            formula_description="Future return (BAD)",
            lookback_bars=1,
            dependency_columns=["close"],
            transformation="shift",
            expected_intuition="...",
            leakage_risk="high",
            code="result = df['close'].shift(-1) / df['close'] - 1",
        )
        eval_result = FeatureEvalResult(
            feature_name="future_ret_feat",
            f_statistic=10.0,  # very high but should still be blocked
            regime_breakdown={},
            leakage_risk="high",
            registered=False,
        )
        _FEATURE_SPEC_CACHE["future_ret_feat"] = spec
        _FEATURE_EVAL_CACHE["future_ret_feat"] = eval_result

        client = MagicMock()
        with patch("backend.deps.get_feature_library") as mock_lib:
            mock_library = MagicMock()
            mock_library.get.return_value = None
            mock_lib.return_value = mock_library

            out = await register_feature(RegisterFeatureInput(feature_name="future_ret_feat"), client)

        assert out.registered is False
        assert out.reason == "leakage_blocked"

        # Cleanup
        _FEATURE_SPEC_CACHE.pop("future_ret_feat", None)
        _FEATURE_EVAL_CACHE.pop("future_ret_feat", None)
