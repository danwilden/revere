"""Tests for backend/agents/model_researcher.py.

All Bedrock adapter.converse() calls are mocked — no live AWS.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.model_researcher import (
    _extract_json,
    _run_model_researcher,
    model_researcher_node,
)
from backend.agents.state import DEFAULT_STATE
from backend.automl.sagemaker_runner import AUC_ROC_ACCEPTANCE_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> dict:
    state = DEFAULT_STATE("test-session")
    state.update(kwargs)
    return state


def _make_converse_result(content: str):
    """Build a minimal ConverseResult-like object for mocking."""
    result = MagicMock()
    result.content = content
    result.stop_reason = "end_turn"
    result.tool_use = None
    result.input_tokens = 100
    result.output_tokens = 50
    return result


def _good_eval_json(candidate_id: str = "cand-1", accept: bool = True, auc: float = 0.72) -> str:
    return json.dumps({
        "candidate_id": candidate_id,
        "accept": accept,
        "rationale": "Strong AUC-ROC with consistent signal.",
        "auc_roc": auc,
    })


def _make_automl_record(job_id: str = "job-abc", status: str = "completed") -> dict:
    return {
        "id": job_id,
        "job_name": job_id,
        "target_column": "direction_label",
        "target_type": "direction",
        "train_s3_uri": "s3://bucket/train.csv",
        "output_s3_prefix": "s3://bucket/out/",
        "status": status,
        "best_auc_roc": 0.72,
        "best_candidate_id": None,
        "evaluation": None,
    }


# ---------------------------------------------------------------------------
# Test 1: ModelEvaluation parsed correctly from LLM JSON response
# ---------------------------------------------------------------------------


def test_evaluation_parsed_from_llm_response():
    """Verify ModelEvaluation fields match LLM JSON output when AUC passes gate."""
    import asyncio

    record = _make_automl_record("job-parse")
    state = _make_state(automl_job_id="job-parse")

    with (
        patch("backend.agents.model_researcher._load_automl_record", return_value=record),
        patch("backend.agents.model_researcher._save_automl_record"),
        patch("backend.agents.providers.bedrock.BedrockAdapter.converse", new_callable=AsyncMock)
        as mock_converse,
    ):
        mock_converse.return_value = _make_converse_result(
            _good_eval_json("cand-1", accept=True, auc=0.72)
        )
        result = asyncio.run(_run_model_researcher(state))

    assert result["model_evaluation"] is not None
    assert result["model_evaluation"]["candidate_id"] == "cand-1"
    assert result["model_evaluation"]["accept"] is True
    assert result["model_evaluation"]["auc_roc"] == pytest.approx(0.72)
    assert result["model_evaluation"]["rationale"] != ""


# ---------------------------------------------------------------------------
# Test 2: accept=False enforced when AUC < threshold regardless of LLM text
# ---------------------------------------------------------------------------


def test_accept_false_when_auc_below_threshold():
    """LLM says accept=True but AUC < 0.55 → accept forced to False."""
    import asyncio

    low_auc = AUC_ROC_ACCEPTANCE_THRESHOLD - 0.10
    record = _make_automl_record("job-lowroc")
    state = _make_state(automl_job_id="job-lowroc")

    with (
        patch("backend.agents.model_researcher._load_automl_record", return_value=record),
        patch("backend.agents.model_researcher._save_automl_record"),
        patch("backend.agents.providers.bedrock.BedrockAdapter.converse", new_callable=AsyncMock)
        as mock_converse,
    ):
        mock_converse.return_value = _make_converse_result(
            _good_eval_json("cand-2", accept=True, auc=low_auc)
        )
        result = asyncio.run(_run_model_researcher(state))

    assert result["model_evaluation"]["accept"] is False
    assert result["model_evaluation"]["auc_roc"] == pytest.approx(low_auc)


# ---------------------------------------------------------------------------
# Test 3: research_mode cleared to None when candidate accepted
# ---------------------------------------------------------------------------


def test_research_mode_cleared_on_accept():
    """research_mode must be None in returned state when evaluation accepts."""
    import asyncio

    record = _make_automl_record("job-accept")
    state = _make_state(automl_job_id="job-accept", research_mode="automl_evaluation")

    with (
        patch("backend.agents.model_researcher._load_automl_record", return_value=record),
        patch("backend.agents.model_researcher._save_automl_record"),
        patch("backend.agents.providers.bedrock.BedrockAdapter.converse", new_callable=AsyncMock)
        as mock_converse,
    ):
        mock_converse.return_value = _make_converse_result(
            _good_eval_json("cand-3", accept=True, auc=0.68)
        )
        result = asyncio.run(_run_model_researcher(state))

    assert result["research_mode"] is None


# ---------------------------------------------------------------------------
# Test 4: research_mode cleared to None when candidate rejected
# ---------------------------------------------------------------------------


def test_research_mode_cleared_on_reject():
    """research_mode must be None even when evaluation rejects."""
    import asyncio

    record = _make_automl_record("job-reject")
    state = _make_state(automl_job_id="job-reject", research_mode="automl_evaluation")

    with (
        patch("backend.agents.model_researcher._load_automl_record", return_value=record),
        patch("backend.agents.model_researcher._save_automl_record"),
        patch("backend.agents.providers.bedrock.BedrockAdapter.converse", new_callable=AsyncMock)
        as mock_converse,
    ):
        mock_converse.return_value = _make_converse_result(
            _good_eval_json("cand-4", accept=False, auc=0.48)
        )
        result = asyncio.run(_run_model_researcher(state))

    assert result["research_mode"] is None
    assert result["model_evaluation"]["accept"] is False


# ---------------------------------------------------------------------------
# Test 5: research_mode cleared to None on exception during converse
# ---------------------------------------------------------------------------


def test_research_mode_cleared_on_error():
    """Exception during adapter.converse → research_mode still cleared to None."""
    import asyncio

    record = _make_automl_record("job-error")
    state = _make_state(automl_job_id="job-error", research_mode="automl_evaluation")

    with (
        patch("backend.agents.model_researcher._load_automl_record", return_value=record),
        patch("backend.agents.model_researcher._save_automl_record"),
        patch("backend.agents.providers.bedrock.BedrockAdapter.converse", new_callable=AsyncMock)
        as mock_converse,
    ):
        mock_converse.side_effect = RuntimeError("Bedrock timeout")
        result = asyncio.run(_run_model_researcher(state))

    assert result["research_mode"] is None
    # Should still have a model_evaluation dict (with accept=False fallback)
    assert result["model_evaluation"] is not None
    assert result["model_evaluation"]["accept"] is False


# ---------------------------------------------------------------------------
# Test 6: missing automl_job_id returns error without crash
# ---------------------------------------------------------------------------


def test_missing_automl_job_id_returns_error():
    """Node should handle missing automl_job_id gracefully."""
    import asyncio

    state = _make_state(automl_job_id=None, research_mode="automl_evaluation")
    result = asyncio.run(_run_model_researcher(state))

    assert result["research_mode"] is None
    assert result["model_evaluation"] is None
    assert any("automl_job_id" in e for e in result.get("errors", []))


# ---------------------------------------------------------------------------
# Test 7: supervisor routes to model_researcher on automl_evaluation
# ---------------------------------------------------------------------------


def test_supervisor_routes_to_model_researcher():
    """supervisor_node sets next_node=model_researcher when research_mode=automl_evaluation."""
    from backend.agents.supervisor import supervisor_node

    state = DEFAULT_STATE("sess-supervisor")
    state["research_mode"] = "automl_evaluation"

    result = supervisor_node(state)
    assert result["next_node"] == "model_researcher"


# ---------------------------------------------------------------------------
# Test 8: _extract_json handles plain JSON
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    text = '{"candidate_id": "x", "accept": true, "rationale": "ok", "auc_roc": 0.6}'
    parsed = _extract_json(text)
    assert parsed["accept"] is True
    assert parsed["auc_roc"] == 0.6


# ---------------------------------------------------------------------------
# Test 9: _extract_json handles markdown fences
# ---------------------------------------------------------------------------


def test_extract_json_markdown_fence():
    text = '```json\n{"candidate_id": "y", "accept": false, "rationale": "low", "auc_roc": 0.4}\n```'
    parsed = _extract_json(text)
    assert parsed["accept"] is False


# ---------------------------------------------------------------------------
# Test 10: graph includes model_researcher node
# ---------------------------------------------------------------------------


def test_graph_includes_model_researcher():
    """build_graph() should compile without errors and include model_researcher."""
    from backend.agents.graph import build_graph

    graph = build_graph()
    assert graph is not None
    # The graph nodes dict should contain model_researcher
    assert "model_researcher" in graph.nodes
