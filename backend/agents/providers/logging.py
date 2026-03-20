"""Structured JSON event logger for agent node transitions and LLM/tool calls."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from loguru import logger as _loguru_logger

# Route all loguru output to stderr as raw JSON lines.
# Remove the default handler so we control the exact format.
_loguru_logger.remove()
_loguru_logger.add(
    sys.stderr,
    format="{message}",
    level="DEBUG",
    serialize=False,
)


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON-serialised event dict to stderr via loguru."""
    event.setdefault("timestamp_utc", datetime.now(tz=timezone.utc).isoformat())
    _loguru_logger.info(json.dumps(event, default=str))


class AgentLogger:
    """Structured event logger for LangGraph agent node transitions.

    All methods emit JSON-serialisable dicts to stderr.  In Phase 7 production
    these are captured by CloudWatch Logs via the container stderr pipe.

    Parameters
    ----------
    session_id:
        UUID for the research session — correlates all events in one run.
    """

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id

    # ------------------------------------------------------------------
    # Node lifecycle events
    # ------------------------------------------------------------------

    def node_enter(
        self,
        node: str,
        trace_id: str,
        state_keys: list[str],
    ) -> None:
        """Emit when a LangGraph node begins execution.

        Parameters
        ----------
        node:
            Node name (e.g. ``"supervisor"``, ``"strategy_researcher"``).
        trace_id:
            Per-turn UUID for log correlation.
        state_keys:
            List of AgentState keys present at entry (for debugging).
        """
        _emit(
            {
                "event": "node_enter",
                "node": node,
                "trace_id": trace_id,
                "session_id": self.session_id,
                "state_keys": state_keys,
            }
        )

    def node_exit(
        self,
        node: str,
        trace_id: str,
        duration_ms: int,
        next_node: str,
    ) -> None:
        """Emit when a LangGraph node completes successfully.

        Parameters
        ----------
        node:
            Node name.
        trace_id:
            Per-turn UUID for log correlation.
        duration_ms:
            Wall-clock milliseconds spent in this node.
        next_node:
            Routing decision written to ``state["next_node"]``.
        """
        _emit(
            {
                "event": "node_exit",
                "node": node,
                "trace_id": trace_id,
                "session_id": self.session_id,
                "duration_ms": duration_ms,
                "next_node": next_node,
            }
        )

    def node_error(
        self,
        node: str,
        trace_id: str,
        error: str,
    ) -> None:
        """Emit when a LangGraph node raises an unhandled exception.

        Parameters
        ----------
        node:
            Node name.
        trace_id:
            Per-turn UUID for log correlation.
        error:
            Exception message or traceback string.
        """
        _emit(
            {
                "event": "node_error",
                "node": node,
                "trace_id": trace_id,
                "session_id": self.session_id,
                "error": error,
            }
        )

    # ------------------------------------------------------------------
    # LLM call events
    # ------------------------------------------------------------------

    def llm_call(
        self,
        model: str,
        trace_id: str,
        node: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        tool_use: bool = False,
        tool_name: str | None = None,
    ) -> None:
        """Emit after every Bedrock Converse API call.

        Parameters
        ----------
        model:
            Bedrock model ID string.
        trace_id:
            Per-turn UUID.
        node:
            Which agent node triggered the call.
        input_tokens:
            Prompt token count from Bedrock usage metadata.
        output_tokens:
            Completion token count.
        latency_ms:
            Round-trip latency in milliseconds.
        tool_use:
            Whether the model requested a tool call.
        tool_name:
            Name of the tool requested (or ``None``).
        """
        _emit(
            {
                "event": "llm_call",
                "model": model,
                "trace_id": trace_id,
                "session_id": self.session_id,
                "node": node,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "tool_use": tool_use,
                "tool_name": tool_name,
            }
        )

    # ------------------------------------------------------------------
    # Tool call events
    # ------------------------------------------------------------------

    def tool_call(
        self,
        tool: str,
        trace_id: str,
        node: str,
        input: dict[str, Any],
        output_summary: str,
        latency_ms: int,
        success: bool,
    ) -> None:
        """Emit after every deterministic tool executor call.

        Parameters
        ----------
        tool:
            Tool name (e.g. ``"submit_backtest"``).
        trace_id:
            Per-turn UUID.
        node:
            Which agent node triggered the call.
        input:
            Tool input dict (logged for debugging).
        output_summary:
            Short human-readable description of the output (not the full payload).
        latency_ms:
            Round-trip latency in milliseconds.
        success:
            False if the tool raised a ``ToolCallError``.
        """
        _emit(
            {
                "event": "tool_call",
                "tool": tool,
                "trace_id": trace_id,
                "session_id": self.session_id,
                "node": node,
                "input": input,
                "output_summary": output_summary,
                "latency_ms": latency_ms,
                "success": success,
            }
        )

    # ------------------------------------------------------------------
    # State transition events
    # ------------------------------------------------------------------

    def state_update(
        self,
        trace_id: str,
        field: str,
        new_value_summary: str,
    ) -> None:
        """Emit when a node writes an important field back to AgentState.

        Parameters
        ----------
        trace_id:
            Per-turn UUID.
        field:
            AgentState key that changed (e.g. ``"strategy_id"``).
        new_value_summary:
            Short human-readable description of the new value.
        """
        _emit(
            {
                "event": "state_update",
                "trace_id": trace_id,
                "session_id": self.session_id,
                "field": field,
                "new_value_summary": new_value_summary,
            }
        )
