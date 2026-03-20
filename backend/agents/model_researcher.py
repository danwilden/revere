"""ModelResearcher LangGraph node — evaluates SageMaker Autopilot candidates via Bedrock LLM.

Single-turn evaluation node: loads AutoMLJobRecord, sends candidate metrics to
Bedrock Converse, parses ModelEvaluation, applies hard AUC-ROC gate (>= 0.55),
updates the record, and clears research_mode.

Pattern mirrors feature_researcher.py: async implementation wrapped by a sync
LangGraph-compatible function via asyncio.run().
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from backend.agents.providers.bedrock import BedrockAdapter
from backend.agents.providers.logging import AgentLogger
from backend.agents.state import AgentState
from backend.automl.records import AutoMLJobRecord, ModelEvaluation
from backend.automl.sagemaker_runner import AUC_ROC_ACCEPTANCE_THRESHOLD

logger = logging.getLogger(__name__)

NODE_NAME = "model_researcher"

SYSTEM_PROMPT = (
    "You are a quantitative model evaluation analyst. "
    "Evaluate the SageMaker Autopilot candidate metrics for a Forex price direction model. "
    "Return a JSON object with exactly these keys: "
    '"candidate_id" (string), "accept" (boolean), "rationale" (string), "auc_roc" (float). '
    "Accept only if AUC-ROC >= 0.55 and the model shows genuine predictive signal."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _extract_json(text: str) -> dict[str, Any]:
    """Extract first JSON object from LLM response text (handles markdown fences)."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start: i + 1])
    raise ValueError("Malformed JSON in LLM response")


def _load_automl_record(automl_job_id: str) -> dict | None:
    """Load an AutoMLJobRecord dict from the metadata repo."""
    from backend.deps import get_metadata_repo
    repo = get_metadata_repo()
    # LocalMetadataRepository exposes _get for arbitrary stores
    if hasattr(repo, "_get"):
        return repo._get("automl_jobs", automl_job_id)
    return None


def _save_automl_record(automl_job_id: str, record_dict: dict) -> None:
    """Persist an updated AutoMLJobRecord dict."""
    from backend.deps import get_metadata_repo
    repo = get_metadata_repo()
    record_dict["updated_at"] = _utcnow_iso()
    if hasattr(repo, "_upsert"):
        # Ensure "id" key is set so _upsert can index by it
        record_dict.setdefault("id", automl_job_id)
        repo._upsert("automl_jobs", record_dict)


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _run_model_researcher(state: AgentState) -> dict[str, Any]:
    _logger = AgentLogger(session_id=state.get("session_id", ""))
    trace_id = state.get("trace_id", "")
    prior_errors: list[str] = list(state.get("errors") or [])
    t0 = time.monotonic()
    _logger.node_enter(NODE_NAME, trace_id, list(state.keys()))

    automl_job_id: str | None = state.get("automl_job_id")
    if not automl_job_id:
        _logger.node_exit(NODE_NAME, trace_id, int((time.monotonic() - t0) * 1000), "supervisor")
        return {
            "research_mode": None,
            "model_evaluation": None,
            "errors": prior_errors + ["model_researcher: automl_job_id missing from state"],
        }

    # Load record
    record_dict = _load_automl_record(automl_job_id)
    if record_dict is None:
        _logger.node_exit(NODE_NAME, trace_id, int((time.monotonic() - t0) * 1000), "supervisor")
        return {
            "research_mode": None,
            "model_evaluation": None,
            "errors": prior_errors + [
                f"model_researcher: AutoMLJobRecord not found for id={automl_job_id}"
            ],
        }

    # Build Bedrock message
    candidate_summary = {
        "automl_job_id": automl_job_id,
        "target_type": record_dict.get("target_type"),
        "status": record_dict.get("status"),
        "best_candidate_id": record_dict.get("best_candidate_id"),
        "best_auc_roc": record_dict.get("best_auc_roc"),
        "failure_reason": record_dict.get("failure_reason"),
    }
    user_text = (
        "Evaluate this SageMaker AutoML job result:\n"
        + json.dumps(candidate_summary, indent=2)
        + "\n\nReturn your evaluation as a JSON object."
    )
    messages = [{"role": "user", "content": [{"text": user_text}]}]

    adapter = BedrockAdapter()
    evaluation: ModelEvaluation | None = None

    try:
        result = await adapter.converse(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            tools=None,
            max_tokens=1024,
            temperature=0.2,
        )
        raw = _extract_json(result.content)

        # Hard gate: override accept if AUC-ROC below threshold
        auc_roc_val: float | None = None
        try:
            auc_roc_val = float(raw.get("auc_roc", 0.0))
        except (TypeError, ValueError):
            auc_roc_val = None

        accept = bool(raw.get("accept", False))
        if auc_roc_val is None or auc_roc_val < AUC_ROC_ACCEPTANCE_THRESHOLD:
            accept = False

        evaluation = ModelEvaluation(
            candidate_id=str(raw.get("candidate_id", automl_job_id)),
            accept=accept,
            rationale=str(raw.get("rationale", "")),
            auc_roc=auc_roc_val if auc_roc_val is not None else 0.0,
        )

    except Exception as exc:
        logger.error("model_researcher: LLM call or parse failed: %s", exc)
        evaluation = ModelEvaluation(
            candidate_id=automl_job_id,
            accept=False,
            rationale=f"Evaluation failed: {exc}",
            auc_roc=0.0,
        )
        prior_errors.append(f"model_researcher: {exc}")

    eval_dict = evaluation.model_dump(mode="json")

    # Update and persist record
    try:
        record_dict["evaluation"] = eval_dict
        if evaluation.accept:
            record_dict["best_candidate_id"] = evaluation.candidate_id
            record_dict["best_auc_roc"] = evaluation.auc_roc
        _save_automl_record(automl_job_id, record_dict)
    except Exception as exc:
        logger.warning("model_researcher: could not persist record update: %s", exc)
        prior_errors.append(f"model_researcher: persist failed: {exc}")

    duration_ms = int((time.monotonic() - t0) * 1000)
    _logger.node_exit(NODE_NAME, trace_id, duration_ms, "supervisor")

    return {
        "research_mode": None,   # always cleared
        "model_evaluation": eval_dict,
        "errors": prior_errors,
    }


# ---------------------------------------------------------------------------
# Public LangGraph node (sync wrapper)
# ---------------------------------------------------------------------------


def model_researcher_node(state: AgentState) -> dict[str, Any]:
    """LangGraph node: Bedrock-powered AutoML candidate evaluation.

    Runs async implementation synchronously via asyncio.run() to satisfy
    LangGraph's synchronous node interface.

    Always clears ``research_mode`` to ``None`` regardless of outcome.
    """
    return asyncio.run(_run_model_researcher(state))
