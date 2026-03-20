"""Execute proposed action tool — the ONLY write path from the chat agent to the job system.

Security-critical: validates inputs rigorously before submitting via the
existing backtest job contract (POST /api/backtests/jobs).
"""
from __future__ import annotations

from datetime import datetime

from backend.agents.tools.chat_schemas import (
    ExecuteProposedActionInput,
    ExecuteProposedActionOutput,
)
from backend.agents.tools.client import MedallionClient, ToolCallError
from backend.schemas.enums import Timeframe

TOOL_NAME = "execute_proposed_action"

# Supported action types — extend this set as new write actions are added.
_SUPPORTED_ACTIONS = frozenset({"submit_backtest"})

# Minimal structural keys — at least one must be present in a rules DSL definition.
_REQUIRED_STRATEGY_KEYS = {"entry_long", "entry_short"}

# Fields that require a computed feature run. Any strategy referencing these
# fields needs feature_run_id resolved before the backtest is submitted.
_FEATURE_PIPELINE_FIELDS: frozenset[str] = frozenset({
    "log_ret_1", "log_ret_5", "log_ret_20", "rvol_20",
    "atr_14", "atr_pct_14", "rsi_14", "ema_slope_20", "ema_slope_50",
    "adx_14", "breakout_20", "session",
    "day_of_week", "hour_of_day", "is_friday",
    "minute_of_hour", "week_of_year", "month_of_year",
    "minute_of_hour_sin", "minute_of_hour_cos",
    "hour_of_day_sin", "hour_of_day_cos",
    "day_of_week_sin", "day_of_week_cos",
    "week_of_year_sin", "week_of_year_cos",
    "month_of_year_sin", "month_of_year_cos",
})


def _strategy_needs_features(node: object) -> bool:
    """Return True if any rule node references a computed feature field.

    Recursively walks dicts and lists, checking 'field' and 'field2' keys
    against _FEATURE_PIPELINE_FIELDS.
    """
    if isinstance(node, dict):
        if node.get("field") in _FEATURE_PIPELINE_FIELDS:
            return True
        if node.get("field2") in _FEATURE_PIPELINE_FIELDS:
            return True
        return any(_strategy_needs_features(v) for v in node.values())
    if isinstance(node, list):
        return any(_strategy_needs_features(child) for child in node)
    return False


async def _resolve_feature_run(
    instrument: str,
    timeframe: str,
    test_start: datetime,
    test_end: datetime,
    client: MedallionClient,
) -> str | None:
    """Call POST /api/features/runs/resolve and return feature_run_id.

    Returns None on any failure — the backtest submission will surface a
    clearer error than silently omitting features.
    """
    try:
        result = await client.post(
            "/api/features/runs/resolve",
            body={
                "instrument": instrument,
                "timeframe": timeframe,
                "start_date": test_start.strftime("%Y-%m-%d"),
                "end_date": test_end.strftime("%Y-%m-%d"),
            },
            tool_name=TOOL_NAME,
        )
        return result.get("feature_run_id")
    except ToolCallError:
        return None


async def execute_proposed_action(
    inp: ExecuteProposedActionInput,
    client: MedallionClient,
) -> ExecuteProposedActionOutput:
    """Validate and submit a proposed strategy action.

    Currently supports only ``action_type="submit_backtest"``.  All other
    action types are rejected with a ``ToolCallError``.

    The function goes through ``client.post()`` — it never calls job functions
    directly — preserving the same contract as the REST API.
    """
    # ------------------------------------------------------------------
    # 1. Validate action_type
    # ------------------------------------------------------------------
    if inp.action_type not in _SUPPORTED_ACTIONS:
        raise ToolCallError(
            tool_name=TOOL_NAME,
            status_code=400,
            detail=(
                f"Unsupported action_type '{inp.action_type}'. "
                f"Supported types: {sorted(_SUPPORTED_ACTIONS)}"
            ),
        )

    # ------------------------------------------------------------------
    # 2. Validate strategy_definition structure
    # ------------------------------------------------------------------
    if not inp.strategy_definition:
        raise ToolCallError(
            tool_name=TOOL_NAME,
            status_code=400,
            detail="strategy_definition must be a non-empty dict",
        )

    if not _REQUIRED_STRATEGY_KEYS & inp.strategy_definition.keys():
        raise ToolCallError(
            tool_name=TOOL_NAME,
            status_code=400,
            detail=(
                "strategy_definition must contain at least one of: "
                f"{sorted(_REQUIRED_STRATEGY_KEYS)}"
            ),
        )

    # ------------------------------------------------------------------
    # 3. Validate timeframe against the Timeframe enum
    # ------------------------------------------------------------------
    try:
        timeframe = Timeframe(inp.timeframe)
    except ValueError:
        valid = [t.value for t in Timeframe]
        raise ToolCallError(
            tool_name=TOOL_NAME,
            status_code=400,
            detail=f"Invalid timeframe '{inp.timeframe}'. Must be one of: {valid}",
        )

    # ------------------------------------------------------------------
    # 4. Parse and validate date strings
    # ------------------------------------------------------------------
    test_start = _parse_date(inp.test_start, "test_start")
    test_end = _parse_date(inp.test_end, "test_end")

    if test_end <= test_start:
        raise ToolCallError(
            tool_name=TOOL_NAME,
            status_code=400,
            detail="test_end must be after test_start",
        )

    # ------------------------------------------------------------------
    # 5. Pre-flight: verify data coverage; auto-trigger Dukascopy ingestion
    #    if the requested range is not available.
    # ------------------------------------------------------------------
    try:
        ranges_raw = await client.get(
            "/api/market-data/ranges",
            params={"instrument": inp.instrument},
            tool_name=TOOL_NAME,
        )
        ranges: list[dict] = ranges_raw.get("ranges", [])
        tf_upper = timeframe.value.upper()
        coverage: dict | None = None
        for entry in ranges:
            if (
                entry.get("instrument_id", "").upper() == inp.instrument.upper()
                and entry.get("timeframe", "").upper() == tf_upper
            ):
                coverage = entry
                break

        # Determine whether the requested window is fully covered.
        data_ok = False
        if coverage and coverage.get("has_data"):
            cov_start = (coverage.get("start") or "")[:10]
            cov_end = (coverage.get("end") or "")[:10]
            req_start = test_start.strftime("%Y-%m-%d")
            req_end = test_end.strftime("%Y-%m-%d")
            if cov_start and cov_end and cov_start <= req_start and cov_end >= req_end:
                data_ok = True

        if not data_ok:
            ingest_raw = await client.post(
                "/api/dukascopy/jobs",
                body={
                    "instruments": [inp.instrument],
                    "start_date": test_start.strftime("%Y-%m-%d"),
                    "end_date": test_end.strftime("%Y-%m-%d"),
                },
                tool_name=TOOL_NAME,
            )
            ingestion_job_id = ingest_raw.get("job_id", "unknown")

            # Resolve feature_run_id for pending backtest (best-effort; may be None if bars
            # don't exist yet — pending_backtest.py will re-resolve after ingestion completes)
            resolved_feature_run_id = inp.feature_run_id
            if resolved_feature_run_id is None and _strategy_needs_features(inp.strategy_definition):
                resolved_feature_run_id = await _resolve_feature_run(
                    instrument=inp.instrument,
                    timeframe=inp.timeframe,
                    test_start=test_start,
                    test_end=test_end,
                    client=client,
                )

            # Register backtest to run automatically when ingestion completes
            pending_body = {
                "inline_strategy": inp.strategy_definition,
                "instrument": inp.instrument,
                "timeframe": inp.timeframe,
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "spread_pips": inp.spread_pips,
                "slippage_pips": inp.slippage_pips,
                "commission_per_unit": inp.commission_per_unit,
                "pip_size": inp.pip_size,
                "feature_run_id": resolved_feature_run_id,
                "model_id": inp.model_id,
                "session_id": inp.session_id,
            }
            await client.post(
                f"/api/dukascopy/jobs/{ingestion_job_id}/pending-backtest",
                body=pending_body,
                tool_name=TOOL_NAME,
            )

            return ExecuteProposedActionOutput(
                job_id=ingestion_job_id,
                backtest_run_id=None,
                status="data_ingestion_started",
                message=(
                    f"Ingestion started (Job ID: {ingestion_job_id}). "
                    f"The backtest will run automatically when data is ready; "
                    f"check the Backtests view or ask me for status."
                ),
            )
    except ToolCallError:
        # If the pre-flight check itself fails (e.g. API unreachable), fall through
        # and let the backtest submission surface the real error naturally.
        pass

    # Resolve feature_run_id if strategy references computed feature fields
    resolved_feature_run_id = inp.feature_run_id
    if resolved_feature_run_id is None and _strategy_needs_features(inp.strategy_definition):
        resolved_feature_run_id = await _resolve_feature_run(
            instrument=inp.instrument,
            timeframe=inp.timeframe,
            test_start=test_start,
            test_end=test_end,
            client=client,
        )

    # ------------------------------------------------------------------
    # 6. Build request body and submit via client.post()
    # ------------------------------------------------------------------
    body = {
        "inline_strategy": inp.strategy_definition,
        "instrument": inp.instrument,
        "timeframe": timeframe.value,
        "test_start": test_start.isoformat(),
        "test_end": test_end.isoformat(),
        "spread_pips": inp.spread_pips,
        "slippage_pips": inp.slippage_pips,
        "commission_per_unit": inp.commission_per_unit,
        "pip_size": inp.pip_size,
    }

    if resolved_feature_run_id:
        body["feature_run_id"] = resolved_feature_run_id
    if inp.model_id:
        body["model_id"] = inp.model_id
    if inp.session_id:
        body["session_id"] = inp.session_id

    raw = await client.post(
        "/api/backtests/jobs",
        body=body,
        tool_name=TOOL_NAME,
    )

    # ------------------------------------------------------------------
    # 7. Build and return output
    # ------------------------------------------------------------------
    job_id = raw.get("job_id", "")
    status = raw.get("status", "queued")

    return ExecuteProposedActionOutput(
        job_id=job_id,
        backtest_run_id=None,  # not available until job completes
        status=status,
        message=f"Backtest job queued. Job ID: {job_id}",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str, field_name: str) -> datetime:
    """Parse an ISO date or datetime string, raising ToolCallError on failure."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Also try fromisoformat as a fallback (handles timezone suffixes in 3.11+)
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        pass

    raise ToolCallError(
        tool_name=TOOL_NAME,
        status_code=400,
        detail=f"Cannot parse {field_name} '{value}' as a date. Use ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).",
    )
