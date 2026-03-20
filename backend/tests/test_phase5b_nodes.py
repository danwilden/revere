"""Tests for Phase 5B node implementations — strategy_researcher, backtest_diagnostics,
generation_comparator.

All tests are fully deterministic with mocked Bedrock and httpx — no real AWS calls.

Patching strategy:
- BedrockAdapter.converse is patched directly on the class so asyncio.run() inside
  the synchronous node wrappers picks it up correctly.
- boto3.client is patched on the providers.bedrock module to prevent credential
  resolution at import time.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.providers.bedrock import ConverseResult
from backend.agents.state import DEFAULT_STATE


# ---------------------------------------------------------------------------
# Helpers — build canonical mock ConverseResults
# ---------------------------------------------------------------------------

def _end_turn_result(content: str) -> ConverseResult:
    return ConverseResult(
        content=content,
        tool_use=None,
        input_tokens=100,
        output_tokens=50,
        stop_reason="end_turn",
    )


def _tool_use_result(tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> ConverseResult:
    return ConverseResult(
        content="",
        tool_use={"name": tool_name, "input": tool_input, "toolUseId": tool_use_id},
        input_tokens=100,
        output_tokens=50,
        stop_reason="tool_use",
    )


def _make_candidate_json(
    candidate_id: str | None = None,
    strategy_id: str | None = None,
    backtest_run_id: str | None = None,
    generation: int = 0,
) -> str:
    """Build a valid StrategyCandidate JSON string."""
    cid = candidate_id or str(uuid.uuid4())
    sid = strategy_id or str(uuid.uuid4())
    rid = backtest_run_id or str(uuid.uuid4())
    data = {
        "candidate_id": cid,
        "hypothesis": "RSI oversold in trending regime suggests mean-reversion entry.",
        "strategy_id": sid,
        "strategy_definition": {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        },
        "backtest_run_id": rid,
        "metrics": {"net_pnl": 150.0, "sharpe_ratio": 0.72},
        "trade_count": 25,
        "sharpe": 0.72,
        "max_drawdown_pct": -8.5,
        "win_rate": 0.52,
        "generation": generation,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    return json.dumps(data)


def _make_diagnostic_json(
    failure_taxonomy: str = "poor_sharpe",
    discard: bool = False,
    confidence: float = 0.8,
) -> str:
    data = {
        "failure_taxonomy": failure_taxonomy,
        "root_cause": "Strategy has too few trades to establish statistical edge.",
        "recommended_mutations": [
            "Relax RSI threshold from 30 to 40",
            "Reduce stop_atr_multiplier from 2.0 to 1.5",
        ],
        "confidence": confidence,
        "discard": discard,
    }
    return json.dumps(data)


def _make_comparison_json(
    winner_id: str,
    winner_strategy_id: str,
    score_delta: float = 0.15,
    recommendation: str = "archive",
) -> str:
    data = {
        "winner_id": winner_id,
        "winner_strategy_id": winner_strategy_id,
        "rationale": "Candidate A has higher Sharpe and lower drawdown.",
        "score_delta": score_delta,
        "recommendation": recommendation,
        "scores": {
            winner_id: 0.65,
            "cand-b-002": 0.50,
        },
    }
    return json.dumps(data)


def _make_throttling_error() -> Exception:
    """Build a botocore ClientError that looks like a ThrottlingException."""
    import botocore.exceptions
    error_response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}
    return botocore.exceptions.ClientError(error_response, "Converse")


# ---------------------------------------------------------------------------
# strategy_researcher_node tests
# ---------------------------------------------------------------------------

class TestStrategyResearcherNode:

    def _state(self, **overrides: Any):
        state = DEFAULT_STATE("test-researcher-session")
        state.update(overrides)
        return state

    @patch("backend.agents.providers.bedrock.boto3")
    def test_parses_valid_candidate(self, mock_boto3: MagicMock):
        """Mock end_turn response with valid StrategyCandidate JSON — candidates list populated."""
        from backend.agents.strategy_researcher import strategy_researcher_node

        cid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        candidate_json = _make_candidate_json(candidate_id=cid, strategy_id=sid,
                                               backtest_run_id=rid)

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result(candidate_json),
        ):
            state = self._state(task="generate_seed")
            result = strategy_researcher_node(state)

        assert result["next_node"] == "supervisor"
        assert "strategy_candidates" in result
        assert len(result["strategy_candidates"]) == 1
        cand = result["strategy_candidates"][0]
        assert cand["candidate_id"] == cid
        assert cand["strategy_id"] == sid
        assert cand["backtest_run_id"] == rid
        assert cand["hypothesis"] != ""
        # Should NOT set task="done" on success
        assert result.get("task") != "done"

    @patch("backend.agents.providers.bedrock.boto3")
    def test_malformed_json_sets_error_and_task_done(self, mock_boto3: MagicMock):
        """Non-JSON end_turn content → errors set and task='done'."""
        from backend.agents.strategy_researcher import strategy_researcher_node

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result("This is not valid JSON at all."),
        ):
            state = self._state(task="generate_seed")
            result = strategy_researcher_node(state)

        assert result.get("task") == "done"
        assert any("failed to parse StrategyCandidate" in e for e in result["errors"])

    @patch("backend.agents.providers.bedrock.boto3")
    def test_throttle_retry_succeeds_on_second_attempt(self, mock_boto3: MagicMock):
        """ThrottlingException on first call, success on second — result has no throttle error."""
        from backend.agents.strategy_researcher import strategy_researcher_node

        candidate_json = _make_candidate_json()
        call_count = {"n": 0}

        async def _converse_side_effect(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _make_throttling_error()
            return _end_turn_result(candidate_json)

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            side_effect=_converse_side_effect,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            state = self._state(task="generate_seed")
            result = strategy_researcher_node(state)

        assert call_count["n"] == 2
        assert result["next_node"] == "supervisor"
        assert result.get("task") != "done"
        assert "strategy_candidates" in result
        assert len(result["strategy_candidates"]) == 1

    @patch("backend.agents.providers.bedrock.boto3")
    def test_throttle_max_retries_exhausted_sets_error(self, mock_boto3: MagicMock):
        """Three consecutive ThrottlingExceptions → errors set and task='done'."""
        from backend.agents.strategy_researcher import strategy_researcher_node

        async def _always_throttle(**kwargs):
            raise _make_throttling_error()

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            side_effect=_always_throttle,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            state = self._state(task="generate_seed")
            result = strategy_researcher_node(state)

        assert result.get("task") == "done"
        assert result["errors"]
        # Either the exhausted-retries message or a Bedrock error is present
        error_text = " ".join(result["errors"])
        assert "ThrottlingException" in error_text or "Bedrock" in error_text

    @patch("backend.agents.providers.bedrock.boto3")
    def test_tool_dispatch_poll_job(self, mock_boto3: MagicMock):
        """Converse returns tool_use for poll_job, then end_turn — poll_job is called."""
        from backend.agents.strategy_researcher import strategy_researcher_node

        tool_use_id = "tu-001"
        candidate_json = _make_candidate_json()

        # Sequence: first call → tool_use(poll_job), second call → end_turn
        poll_tool_result = _tool_use_result(
            "poll_job",
            {"job_id": "job-abc-123"},
            tool_use_id,
        )

        call_seq = {"n": 0}

        async def _converse_seq(**kwargs):
            call_seq["n"] += 1
            if call_seq["n"] == 1:
                return poll_tool_result
            return _end_turn_result(candidate_json)

        # Mock the httpx call that poll_job would make internally
        mock_http_response = MagicMock()
        mock_http_response.is_success = True
        mock_http_response.json.return_value = {
            "id": "job-abc-123",
            "job_type": "backtest",
            "status": "succeeded",
            "progress_pct": 100.0,
            "stage_label": "done",
            "requested_by": "system",
            "created_at": "2024-01-15T10:00:00",
            "params_json": {},
            "result_ref": "run-xyz-456",
        }

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            side_effect=_converse_seq,
        ), patch("httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_ctx.get = AsyncMock(return_value=mock_http_response)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

            state = self._state(task="generate_seed")
            result = strategy_researcher_node(state)

        # poll_job should have been called — second converse should have happened
        assert call_seq["n"] == 2
        assert result["next_node"] == "supervisor"


# ---------------------------------------------------------------------------
# backtest_diagnostics_node tests
# ---------------------------------------------------------------------------

class TestBacktestDiagnosticsNode:

    def _state(self, **overrides: Any):
        state = DEFAULT_STATE("test-diagnostics-session")
        state.update(overrides)
        return state

    @patch("backend.agents.providers.bedrock.boto3")
    def test_zero_trades_prepends_zero_trade_context(self, mock_boto3: MagicMock):
        """When total_trades==0, ZERO_TRADE_CONTEXT must appear in the user message."""
        from backend.agents.backtest_diagnostics import backtest_diagnostics_node

        captured_messages: list[Any] = []

        async def _capture_converse(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return _end_turn_result(_make_diagnostic_json(failure_taxonomy="zero_trades"))

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            side_effect=_capture_converse,
        ):
            state = self._state(
                backtest_metrics={"total_trades": 0, "net_pnl": 0.0},
                strategy_definition={"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}},
            )
            result = backtest_diagnostics_node(state)

        assert captured_messages, "converse was not called"
        first_message_content = captured_messages[0]["content"][0]["text"]
        assert "ZERO_TRADE_CONTEXT" in first_message_content

    @patch("backend.agents.providers.bedrock.boto3")
    def test_parses_diagnostic_summary_correctly(self, mock_boto3: MagicMock):
        """Valid DiagnosticSummary JSON → structured diagnostic_summary dict in result."""
        from backend.agents.backtest_diagnostics import backtest_diagnostics_node

        diag_json = _make_diagnostic_json(failure_taxonomy="poor_sharpe", discard=False)

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result(diag_json),
        ):
            state = self._state(
                backtest_metrics={"total_trades": 15, "sharpe_ratio": 0.1},
                backtest_trades=[
                    {"pnl": -5.0, "holding_period": 3},
                    {"pnl": 10.0, "holding_period": 5},
                ],
                strategy_definition={"entry_long": {"field": "adx_14", "op": "gt", "value": 25}},
            )
            result = backtest_diagnostics_node(state)

        assert result["next_node"] == "supervisor"
        assert result["diagnostic_summary"] is not None
        assert result["diagnostic_summary"]["failure_taxonomy"] == "poor_sharpe"
        assert result["diagnostic_summary"]["discard"] is False
        assert isinstance(result["recommended_mutations"], list)
        assert result["discard"] is False
        # diagnosis_summary str for supervisor routing compat
        assert isinstance(result["diagnosis_summary"], str)
        assert result["diagnosis_summary"] != ""

    @patch("backend.agents.providers.bedrock.boto3")
    def test_parse_failure_falls_back_to_no_edge(self, mock_boto3: MagicMock):
        """Two consecutive bad responses → hardcoded NO_EDGE fallback used."""
        from backend.agents.backtest_diagnostics import backtest_diagnostics_node

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result("I cannot produce valid JSON right now."),
        ):
            state = self._state(
                backtest_metrics={"total_trades": 5},
                strategy_definition={},
            )
            result = backtest_diagnostics_node(state)

        assert result["next_node"] == "supervisor"
        assert result["diagnostic_summary"] is not None
        assert result["diagnostic_summary"]["failure_taxonomy"] == "no_edge"
        assert result["diagnostic_summary"]["confidence"] == 0.0
        assert result["discard"] is False

    @patch("backend.agents.providers.bedrock.boto3")
    def test_discard_true_written_when_llm_says_discard(self, mock_boto3: MagicMock):
        """LLM responds with discard=true → state.discard is set to True."""
        from backend.agents.backtest_diagnostics import backtest_diagnostics_node

        diag_json = _make_diagnostic_json(
            failure_taxonomy="no_edge",
            discard=True,
            confidence=0.9,
        )

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result(diag_json),
        ):
            state = self._state(
                generation=5,
                backtest_metrics={"total_trades": 0},
                strategy_definition={},
            )
            result = backtest_diagnostics_node(state)

        assert result["discard"] is True
        assert result["diagnostic_summary"]["discard"] is True


# ---------------------------------------------------------------------------
# generation_comparator_node tests
# ---------------------------------------------------------------------------

class TestGenerationComparatorNode:

    def _state(self, **overrides: Any):
        state = DEFAULT_STATE("test-comparator-session")
        state.update(overrides)
        return state

    def _candidate(
        self,
        candidate_id: str | None = None,
        strategy_id: str | None = None,
        sharpe: float = 0.5,
        generation: int = 0,
    ) -> dict[str, Any]:
        return {
            "candidate_id": candidate_id or str(uuid.uuid4()),
            "hypothesis": "Test hypothesis",
            "strategy_id": strategy_id or str(uuid.uuid4()),
            "strategy_definition": {},
            "backtest_run_id": str(uuid.uuid4()),
            "metrics": {"sharpe_ratio": sharpe},
            "trade_count": 30,
            "sharpe": sharpe,
            "max_drawdown_pct": -10.0,
            "win_rate": 0.55,
            "generation": generation,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    @patch("backend.agents.providers.bedrock.boto3")
    def test_empty_candidates_returns_discard_without_llm_call(self, mock_boto3: MagicMock):
        """strategy_candidates=[] → returns discard + task='done', converse never called."""
        from backend.agents.generation_comparator import generation_comparator_node

        converse_mock = AsyncMock()
        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            converse_mock,
        ):
            state = self._state(strategy_candidates=[])
            result = generation_comparator_node(state)

        converse_mock.assert_not_called()
        assert result["comparison_recommendation"] == "discard"
        assert result.get("task") == "done"
        assert any("no candidates" in e for e in result["errors"])

    @patch("backend.agents.providers.bedrock.boto3")
    def test_two_candidates_sets_winner_and_comparison_result(self, mock_boto3: MagicMock):
        """Two candidates → ComparisonResult parsed, selected_candidate_id set."""
        from backend.agents.generation_comparator import generation_comparator_node

        cand_a_id = "cand-a-001"
        cand_a_strat = str(uuid.uuid4())
        cand_b_id = "cand-b-002"
        candidates = [
            self._candidate(candidate_id=cand_a_id, strategy_id=cand_a_strat,
                            sharpe=0.8, generation=0),
            self._candidate(candidate_id=cand_b_id, sharpe=0.4, generation=1),
        ]

        comparison_json = _make_comparison_json(
            winner_id=cand_a_id,
            winner_strategy_id=cand_a_strat,
            score_delta=0.20,
            recommendation="archive",
        )

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result(comparison_json),
        ):
            state = self._state(strategy_candidates=candidates)
            result = generation_comparator_node(state)

        assert result["next_node"] == "supervisor"
        assert result["selected_candidate_id"] == cand_a_id
        assert result["strategy_id"] == cand_a_strat
        assert result["comparison_recommendation"] == "archive"
        assert result["comparison_result"] is not None
        assert result["comparison_result"]["winner_id"] == cand_a_id
        assert result["comparison_result"]["score_delta"] == pytest.approx(0.20)
        # errors should be empty
        assert result.get("errors") == []

    @patch("backend.agents.providers.bedrock.boto3")
    def test_tie_handling_still_returns_valid_result(self, mock_boto3: MagicMock):
        """score_delta < 0.05 (tie) → result is still valid, no error raised."""
        from backend.agents.generation_comparator import generation_comparator_node

        cand_a_id = "cand-a-tie"
        cand_a_strat = str(uuid.uuid4())
        candidates = [
            self._candidate(candidate_id=cand_a_id, strategy_id=cand_a_strat,
                            sharpe=0.42, generation=0),
            self._candidate(candidate_id="cand-b-tie", sharpe=0.41, generation=1),
        ]

        # score_delta of 0.03 is below the 0.05 tie threshold
        comparison_json = _make_comparison_json(
            winner_id=cand_a_id,
            winner_strategy_id=cand_a_strat,
            score_delta=0.03,
            recommendation="archive",
        )

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result(comparison_json),
        ):
            state = self._state(strategy_candidates=candidates)
            result = generation_comparator_node(state)

        # Tie should not cause error or task=done
        assert result["next_node"] == "supervisor"
        assert result.get("task") != "done"
        assert result["selected_candidate_id"] == cand_a_id
        assert result["comparison_result"]["score_delta"] == pytest.approx(0.03)

    @patch("backend.agents.providers.bedrock.boto3")
    def test_single_candidate_still_calls_llm(self, mock_boto3: MagicMock):
        """Single candidate is valid input — LLM is called, result is set."""
        from backend.agents.generation_comparator import generation_comparator_node

        cand_id = "cand-single-001"
        cand_strat = str(uuid.uuid4())
        candidates = [
            self._candidate(candidate_id=cand_id, strategy_id=cand_strat, sharpe=0.6),
        ]

        comparison_json = json.dumps({
            "winner_id": cand_id,
            "winner_strategy_id": cand_strat,
            "rationale": "Only one candidate. It wins by default.",
            "score_delta": None,
            "recommendation": "archive",
            "scores": {cand_id: 0.6},
        })

        converse_mock = AsyncMock(return_value=_end_turn_result(comparison_json))
        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            converse_mock,
        ):
            state = self._state(strategy_candidates=candidates)
            result = generation_comparator_node(state)

        converse_mock.assert_called_once()
        assert result["selected_candidate_id"] == cand_id

    @patch("backend.agents.providers.bedrock.boto3")
    def test_parse_failure_retries_then_returns_error(self, mock_boto3: MagicMock):
        """Two consecutive bad responses → error set and task='done'."""
        from backend.agents.generation_comparator import generation_comparator_node

        candidates = [self._candidate()]

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            new_callable=AsyncMock,
            return_value=_end_turn_result("No JSON here at all, sorry."),
        ):
            state = self._state(strategy_candidates=candidates)
            result = generation_comparator_node(state)

        assert result.get("task") == "done"
        assert result["comparison_recommendation"] == "discard"
        assert result["errors"]

    @patch("backend.agents.providers.bedrock.boto3")
    def test_throttle_in_comparator_sets_error(self, mock_boto3: MagicMock):
        """Bedrock ThrottlingException exhausted → error set and task='done'."""
        from backend.agents.generation_comparator import generation_comparator_node

        candidates = [self._candidate(), self._candidate()]

        async def _always_throttle(**kwargs):
            raise _make_throttling_error()

        with patch(
            "backend.agents.providers.bedrock.BedrockAdapter.converse",
            side_effect=_always_throttle,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            state = self._state(strategy_candidates=candidates)
            result = generation_comparator_node(state)

        assert result.get("task") == "done"
        error_text = " ".join(result["errors"])
        assert "ThrottlingException" in error_text or "Bedrock" in error_text


# ---------------------------------------------------------------------------
# Supervisor routing additions — Phase 5B generation_comparator branch
# ---------------------------------------------------------------------------

class TestSupervisorPhase5BRouting:
    """Tests for the new generation_comparator routing branch in supervisor_node."""

    def _state(self, **overrides: Any):
        state = DEFAULT_STATE("test-supervisor-5b")
        state.update(overrides)
        return state

    def test_routes_to_generation_comparator_when_conditions_met(self):
        from backend.agents.supervisor import supervisor_node

        state = self._state(
            task="mutate",
            generation=1,
            backtest_run_id="run-gen1-abc",
            diagnosis_summary="ATR stop is too tight.",
            discard=False,
            comparison_result=None,
        )
        result = supervisor_node(state)
        assert result["next_node"] == "generation_comparator"

    def test_does_not_route_comparator_when_comparison_result_already_set(self):
        from backend.agents.supervisor import supervisor_node

        state = self._state(
            task="mutate",
            generation=1,
            backtest_run_id="run-gen1-abc",
            diagnosis_summary="Some diagnosis",
            discard=False,
            comparison_result={"winner_id": "cand-x"},  # already set
        )
        result = supervisor_node(state)
        # Should NOT go to generation_comparator since result is already set
        assert result["next_node"] != "generation_comparator"

    def test_does_not_route_comparator_when_generation_zero(self):
        from backend.agents.supervisor import supervisor_node

        state = self._state(
            task="diagnose",
            generation=0,
            backtest_run_id="run-gen0-abc",
            diagnosis_summary="Some diagnosis",
            discard=False,
            comparison_result=None,
        )
        result = supervisor_node(state)
        # generation < 1 → mutation path, not comparator
        assert result["next_node"] != "generation_comparator"

    def test_does_not_route_comparator_when_discard_true(self):
        from backend.agents.supervisor import supervisor_node

        state = self._state(
            task="mutate",
            generation=2,
            backtest_run_id="run-gen2-abc",
            diagnosis_summary="No recoverable path.",
            discard=True,
            comparison_result=None,
        )
        result = supervisor_node(state)
        # discard=True → END, not generation_comparator
        assert result["next_node"] == "END"


# ---------------------------------------------------------------------------
# Schema model tests — Phase 5B new models
# ---------------------------------------------------------------------------

class TestPhase5BSchemas:

    def test_strategy_candidate_valid_construction(self):
        from backend.agents.tools.schemas import StrategyCandidate
        cand = StrategyCandidate.model_validate({
            "candidate_id": str(uuid.uuid4()),
            "hypothesis": "Test hypothesis",
            "strategy_definition": {"entry_long": {}},
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        })
        assert cand.trade_count == 0
        assert cand.sharpe is None
        assert cand.error is None

    def test_failure_taxonomy_enum_values(self):
        from backend.agents.tools.schemas import FailureTaxonomy
        assert FailureTaxonomy.ZERO_TRADES == "zero_trades"
        assert FailureTaxonomy.NO_EDGE == "no_edge"
        assert FailureTaxonomy.POSITIVE == "positive"

    def test_diagnostic_summary_valid(self):
        from backend.agents.tools.schemas import DiagnosticSummary, FailureTaxonomy
        summary = DiagnosticSummary.model_validate({
            "failure_taxonomy": "poor_sharpe",
            "root_cause": "Sharpe is below threshold.",
            "recommended_mutations": ["Widen stop"],
            "confidence": 0.75,
            "discard": False,
        })
        assert summary.failure_taxonomy == FailureTaxonomy.POOR_SHARPE
        assert summary.discard is False

    def test_diagnostic_summary_confidence_bounds(self):
        import pydantic
        from backend.agents.tools.schemas import DiagnosticSummary
        with pytest.raises(pydantic.ValidationError):
            DiagnosticSummary.model_validate({
                "failure_taxonomy": "no_edge",
                "root_cause": "x",
                "recommended_mutations": [],
                "confidence": 1.5,  # out of range
                "discard": False,
            })

    def test_comparison_result_valid(self):
        from backend.agents.tools.schemas import ComparisonResult
        result = ComparisonResult.model_validate({
            "winner_id": "cand-001",
            "winner_strategy_id": "strat-001",
            "rationale": "Candidate A wins.",
            "score_delta": 0.12,
            "recommendation": "continue",
            "scores": {"cand-001": 0.72, "cand-002": 0.60},
        })
        assert result.recommendation == "continue"
        assert result.scores["cand-001"] == pytest.approx(0.72)

    def test_get_hmm_model_schemas(self):
        from backend.agents.tools.schemas import GetHmmModelInput, GetHmmModelOutput, HmmModelStateStats
        inp = GetHmmModelInput(model_id="model-abc-123")
        assert inp.model_id == "model-abc-123"

        out = GetHmmModelOutput.model_validate({
            "id": "model-abc-123",
            "instrument_id": "EUR_USD",
            "timeframe": "H4",
            "num_states": 7,
            "label_map": {"0": "TREND_BULL_LOW_VOL"},
            "state_stats": [
                {"state_id": 0, "label": "TREND_BULL_LOW_VOL", "frequency_pct": 14.2}
            ],
            "created_at": "2024-01-01T00:00:00+00:00",
        })
        assert out.num_states == 7
        assert out.state_stats[0].label == "TREND_BULL_LOW_VOL"

    def test_agent_state_has_phase5b_fields(self):
        from backend.agents.state import DEFAULT_STATE
        state = DEFAULT_STATE("test-phase5b-state")
        assert "regime_context" in state
        assert "strategy_candidates" in state
        assert "selected_candidate_id" in state
        assert "diagnostic_summary" in state
        assert "comparison_result" in state
        assert state["regime_context"] is None
        assert state["strategy_candidates"] is None
        assert state["diagnostic_summary"] is None
