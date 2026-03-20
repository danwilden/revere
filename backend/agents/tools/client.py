"""Async httpx client for calling the Medallion backend API from agent tools."""
from __future__ import annotations

import json
from typing import Any

import httpx

from backend.config import settings


class ToolCallError(Exception):
    """Raised when the backend API returns a non-2xx response.

    Attributes
    ----------
    tool_name:
        The name of the agent tool that triggered the request.
    status_code:
        HTTP status code returned by the backend.
    detail:
        Error detail text extracted from the response body.
    """

    def __init__(self, tool_name: str, status_code: int, detail: str) -> None:
        self.tool_name = tool_name
        self.status_code = status_code
        self.detail = detail
        super().__init__(
            f"[{tool_name}] HTTP {status_code}: {detail}"
        )


class MedallionClient:
    """Lightweight async HTTP client wrapping the Medallion backend API.

    Parameters
    ----------
    base_url:
        Root URL for the backend. Defaults to ``settings.api_base_url``
        (``http://localhost:8000``). Override for testing or cross-env calls.
    timeout:
        Request timeout in seconds (default 30).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = (base_url or settings.api_base_url).rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        tool_name: str = "unknown",
    ) -> Any:
        """Send a GET request and return the parsed JSON body.

        Parameters
        ----------
        path:
            URL path relative to ``base_url`` (must start with ``/``).
        params:
            Optional query-string parameters.
        tool_name:
            Caller tool name for error attribution.

        Raises
        ------
        ToolCallError
            On any non-2xx HTTP response.
        """
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, params=params)
        return self._parse(response, tool_name)

    async def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        tool_name: str = "unknown",
    ) -> Any:
        """Send a POST request with a JSON body and return the parsed JSON body.

        Parameters
        ----------
        path:
            URL path relative to ``base_url`` (must start with ``/``).
        body:
            Optional request body, serialized as JSON.
        tool_name:
            Caller tool name for error attribution.

        Raises
        ------
        ToolCallError
            On any non-2xx HTTP response.
        """
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=body)
        return self._parse(response, tool_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(response: httpx.Response, tool_name: str) -> Any:
        """Parse response or raise ``ToolCallError`` on non-2xx."""
        if response.is_success:
            try:
                return response.json()
            except json.JSONDecodeError:
                # Some endpoints return empty bodies on success (e.g. 201 No Content).
                return None

        # Extract human-readable detail from the error body.
        try:
            payload = response.json()
            detail = payload.get("detail", response.text)
        except (json.JSONDecodeError, AttributeError):
            detail = response.text

        raise ToolCallError(tool_name, response.status_code, str(detail))
