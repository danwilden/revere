"""AWS Bedrock Converse API adapter for LLM-driven agent nodes."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import boto3

from backend.config import settings


@dataclass
class ConverseResult:
    """Parsed response from the Bedrock Converse API.

    Attributes
    ----------
    content:
        The text content of the assistant message (empty string if tool-use only).
    tool_use:
        Raw tool use block dict if the model requested a tool call, else ``None``.
        Shape: ``{"toolUseId": str, "name": str, "input": dict}``.
    input_tokens:
        Number of tokens consumed by the prompt.
    output_tokens:
        Number of tokens in the completion.
    stop_reason:
        Bedrock stop_reason string, e.g. ``"end_turn"`` or ``"tool_use"``.
    """

    content: str
    tool_use: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    stop_reason: str


class BedrockAdapter:
    """Thin async wrapper around ``boto3.client("bedrock-runtime")`` Converse API.

    The boto3 client is instantiated lazily on first use to avoid credential
    resolution at import time (safe for tests that mock boto3).

    Parameters
    ----------
    model_id:
        Bedrock model identifier. Defaults to ``settings.bedrock_model_id``.
    region:
        AWS region. Defaults to ``settings.bedrock_region``.
    """

    def __init__(
        self,
        model_id: str | None = None,
        region: str | None = None,
    ) -> None:
        self._model_id = model_id or settings.bedrock_model_id
        self._region = region or settings.bedrock_region
        self._client: Any = None  # lazy

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def converse(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ConverseResult:
        """Call the Bedrock Converse API (non-streaming).

        Parameters
        ----------
        messages:
            List of Converse-format message dicts
            (``{"role": "user"/"assistant", "content": [...]}``.
        system_prompt:
            Optional system instruction text.
        tools:
            Optional list of Bedrock tool spec dicts
            (``{"toolSpec": {"name": ..., "description": ..., "inputSchema": ...}}``).
        max_tokens:
            Upper token limit for the completion.
        temperature:
            Sampling temperature (0.0 = deterministic).

        Returns
        -------
        ConverseResult
            Parsed response including token counts and optional tool-use block.
        """
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]
        if tools:
            kwargs["toolConfig"] = {"tools": tools}

        t0 = time.monotonic()
        # Run synchronous boto3 call in thread pool to avoid blocking the event loop.
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.converse(**kwargs)
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        result = self._parse_converse_response(response)

        # Structured observability log.
        import sys
        log_event = {
            "event": "llm_call",
            "model": self._model_id,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": latency_ms,
            "stop_reason": result.stop_reason,
            "tool_use": result.tool_use is not None,
            "tool_name": result.tool_use.get("name") if result.tool_use else None,
        }
        print(json.dumps(log_event), file=sys.stderr)

        return result

    async def converse_stream(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Call the Bedrock ConverseStream API and yield text chunks.

        Parameters
        ----------
        messages:
            Converse-format message list.
        system_prompt:
            Optional system instruction text.
        tools:
            Optional Bedrock tool spec list.

        Yields
        ------
        str
            Each text delta as it arrives from the stream.
        """
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]
        if tools:
            kwargs["toolConfig"] = {"tools": tools}

        # boto3 stream must be consumed synchronously — wrap in async generator.
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.converse_stream(**kwargs)
        )
        stream = response.get("stream", [])
        for event in stream:
            delta = (
                event.get("contentBlockDelta", {})
                .get("delta", {})
                .get("text", "")
            )
            if delta:
                yield delta

    @staticmethod
    def extract_tool_use(result: ConverseResult) -> tuple[str, dict[str, Any]] | None:
        """Extract ``(tool_name, tool_input)`` from a ``ConverseResult``.

        Returns ``None`` if the model did not request a tool call.

        Parameters
        ----------
        result:
            A ``ConverseResult`` returned by :meth:`converse`.

        Returns
        -------
        tuple[str, dict] or None
            ``(tool_name, tool_input)`` when ``stop_reason == "tool_use"``,
            otherwise ``None``.
        """
        if result.tool_use is None:
            return None
        name = result.tool_use.get("name", "")
        tool_input = result.tool_use.get("input", {})
        if not name:
            return None
        return name, tool_input

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return (or lazily create) the boto3 bedrock-runtime client."""
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
            )
        return self._client

    @staticmethod
    def _parse_converse_response(response: dict[str, Any]) -> ConverseResult:
        """Convert a raw Bedrock Converse response dict to ``ConverseResult``."""
        usage = response.get("usage", {})
        input_tokens: int = usage.get("inputTokens", 0)
        output_tokens: int = usage.get("outputTokens", 0)
        stop_reason: str = response.get("stopReason", "")

        output_msg = response.get("output", {}).get("message", {})
        content_blocks: list[dict[str, Any]] = output_msg.get("content", [])

        text_parts: list[str] = []
        tool_use_block: dict[str, Any] | None = None

        for block in content_blocks:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tool_use_block = block["toolUse"]

        return ConverseResult(
            content=" ".join(text_parts),
            tool_use=tool_use_block,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
        )
