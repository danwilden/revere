"""Read-only tool executors for the Medallion chat agent.

Six tools that fetch context from the backend API before the chat agent
generates a response. All tools are async and use MedallionClient for HTTP.

Exports
-------
CHAT_READ_TOOLS : list[dict]
    Bedrock-compatible tool spec dicts for the six read tools.
dispatch_chat_read_tool(tool_name, tool_input, client) -> dict
    Route a tool call to the correct function and return the result as a dict.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.agents.tools.chat_schemas import (
    CheckDataAvailabilityInput,
    CheckDataAvailabilityOutput,
    ExperimentSummary,
    GetBacktestResultInput,
    GetBacktestResultOutput,
    GetExperimentInput,
    GetExperimentOutput,
    GetStrategyDefinitionInput,
    GetStrategyDefinitionOutput,
    ListRecentExperimentsInput,
    ListRecentExperimentsOutput,
    MemorySummary,
    SearchExperimentsInput,
    SearchExperimentsOutput,
    SearchMemoriesInput,
    SearchMemoriesOutput,
)
from backend.agents.tools.client import MedallionClient, ToolCallError

logger = logging.getLogger(__name__)

_TOOL_NAME_GET_EXPERIMENT = "get_experiment"
_TOOL_NAME_GET_BACKTEST = "get_backtest_result"
_TOOL_NAME_GET_STRATEGY = "get_strategy_definition"
_TOOL_NAME_LIST_EXPERIMENTS = "list_recent_experiments"
_TOOL_NAME_SEARCH_EXPERIMENTS = "search_experiments"
_TOOL_NAME_CHECK_DATA = "check_data_availability"

_MAX_HYPOTHESIS_LEN = 100
_MAX_TRADES_IN_RESULT = 50
_SEARCH_LIMIT = 200
_SEARCH_MAX_RESULTS = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate_hypothesis(text: str | None) -> str:
    """Truncate hypothesis to 100 characters, adding ellipsis if needed."""
    if text is None:
        return ""
    if len(text) <= _MAX_HYPOTHESIS_LEN:
        return text
    return text[: _MAX_HYPOTHESIS_LEN - 3] + "..."


def _experiment_to_summary(raw: dict[str, Any]) -> ExperimentSummary:
    """Convert a raw experiment dict (from either API store) to a summary."""
    return ExperimentSummary(
        id=raw.get("id", ""),
        hypothesis=_truncate_hypothesis(raw.get("hypothesis") or raw.get("description", "")),
        status=raw.get("status", ""),
        instrument=raw.get("instrument", ""),
        timeframe=raw.get("timeframe", ""),
        created_at=str(raw.get("created_at", "")),
    )


# ---------------------------------------------------------------------------
# Tool 1: get_experiment
# ---------------------------------------------------------------------------

async def get_experiment(
    inp: GetExperimentInput,
    client: MedallionClient,
) -> GetExperimentOutput:
    """Fetch a full experiment record by ID including iterations.

    Calls GET /api/experiments/{experiment_id}.

    Parameters
    ----------
    inp:
        Input containing the experiment_id to fetch.
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    GetExperimentOutput
        Full experiment record and iteration history.

    Raises
    ------
    ToolCallError
        If the experiment is not found (404) or the API returns an error.
    """
    raw = await client.get(
        f"/api/experiments/{inp.experiment_id}",
        tool_name=_TOOL_NAME_GET_EXPERIMENT,
    )
    # API returns ExperimentDetailResponse: {experiment: ..., iterations: [...]}
    experiment = raw.get("experiment", raw)
    iterations = raw.get("iterations", [])
    return GetExperimentOutput(experiment=experiment, iterations=iterations)


# ---------------------------------------------------------------------------
# Tool 2: get_backtest_result
# ---------------------------------------------------------------------------

async def get_backtest_result(
    inp: GetBacktestResultInput,
    client: MedallionClient,
) -> GetBacktestResultOutput:
    """Fetch a backtest run record with metrics and trade summary.

    Makes two API calls:
    - GET /api/backtests/runs/{run_id} for run + metrics
    - GET /api/backtests/runs/{run_id}/trades for the trade log

    Trades are truncated to the first 50 to avoid oversized payloads.

    Parameters
    ----------
    inp:
        Input containing the backtest_run_id to fetch.
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    GetBacktestResultOutput
        Combined run data, metrics, per-regime metrics, and trade summary.

    Raises
    ------
    ToolCallError
        If the backtest run is not found (404) or the API returns an error.
    """
    run_id = inp.backtest_run_id

    # Fetch run summary + metrics
    run_data = await client.get(
        f"/api/backtests/runs/{run_id}",
        tool_name=_TOOL_NAME_GET_BACKTEST,
    )

    run = run_data.get("run", {})
    all_metrics = run_data.get("metrics", [])

    # Split metrics into overall and per-regime
    overall_metrics: list[dict[str, Any]] = []
    per_regime_metrics: list[dict[str, Any]] = []
    for m in all_metrics:
        segment = m.get("segment_type", "overall")
        if segment == "regime":
            per_regime_metrics.append(m)
        else:
            overall_metrics.append(m)

    # Fetch trades (may fail independently — e.g. run still in progress)
    trades: list[dict[str, Any]] = []
    trade_count = 0
    try:
        trades_data = await client.get(
            f"/api/backtests/runs/{run_id}/trades",
            tool_name=_TOOL_NAME_GET_BACKTEST,
        )
        all_trades = trades_data.get("trades", [])
        trade_count = trades_data.get("count", len(all_trades))
        trades = all_trades[:_MAX_TRADES_IN_RESULT]
    except ToolCallError:
        # Trades not yet available — return empty list
        logger.debug("Trades not available for run %s", run_id)

    return GetBacktestResultOutput(
        run=run,
        metrics=overall_metrics,
        per_regime_metrics=per_regime_metrics,
        trades=trades,
        trade_count=trade_count,
    )


# ---------------------------------------------------------------------------
# Tool 3: get_strategy_definition
# ---------------------------------------------------------------------------

async def get_strategy_definition(
    inp: GetStrategyDefinitionInput,
    client: MedallionClient,
) -> GetStrategyDefinitionOutput:
    """Fetch a strategy definition by ID.

    Calls GET /api/strategies/{strategy_id}.

    Parameters
    ----------
    inp:
        Input containing the strategy_id to fetch.
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    GetStrategyDefinitionOutput
        Strategy id, name, type, definition_json, and tags.

    Raises
    ------
    ToolCallError
        If the strategy is not found (404) or the API returns an error.
    """
    raw = await client.get(
        f"/api/strategies/{inp.strategy_id}",
        tool_name=_TOOL_NAME_GET_STRATEGY,
    )
    return GetStrategyDefinitionOutput(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        strategy_type=raw.get("strategy_type", ""),
        definition_json=raw.get("definition_json", {}),
        tags=raw.get("tags", []),
    )


# ---------------------------------------------------------------------------
# Tool 4: list_recent_experiments
# ---------------------------------------------------------------------------

async def list_recent_experiments(
    inp: ListRecentExperimentsInput,
    client: MedallionClient,
) -> ListRecentExperimentsOutput:
    """List recent experiments, optionally filtered by instrument.

    Calls GET /api/experiments with a limit parameter. If instrument is
    provided, results are filtered client-side since the API does not
    support an instrument query parameter.

    Never raises -- returns an empty list on API errors.

    Parameters
    ----------
    inp:
        Input with n (max results) and optional instrument filter.
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    ListRecentExperimentsOutput
        List of experiment summaries with truncated hypotheses.
    """
    try:
        # Fetch more than needed if filtering by instrument
        fetch_limit = inp.n if inp.instrument is None else min(inp.n * 3, 100)
        raw = await client.get(
            "/api/experiments",
            params={"limit": fetch_limit},
            tool_name=_TOOL_NAME_LIST_EXPERIMENTS,
        )
    except ToolCallError as exc:
        logger.warning("list_recent_experiments failed: %s", exc)
        return ListRecentExperimentsOutput(experiments=[])

    experiments = raw.get("experiments", [])

    # Client-side instrument filter
    if inp.instrument is not None:
        experiments = [
            e for e in experiments
            if e.get("instrument", "").upper() == inp.instrument.upper()
        ]

    # Truncate to requested count and build summaries
    experiments = experiments[: inp.n]
    summaries = [_experiment_to_summary(e) for e in experiments]

    return ListRecentExperimentsOutput(experiments=summaries)


# ---------------------------------------------------------------------------
# Tool 5: search_experiments
# ---------------------------------------------------------------------------

async def search_experiments(
    inp: SearchExperimentsInput,
    client: MedallionClient,
) -> SearchExperimentsOutput:
    """Search experiments by substring matching across hypothesis, tags, and instrument.

    Fetches up to 200 experiments from the API and performs in-memory
    substring search. Never raises -- returns an empty list on no matches
    or API errors.

    Parameters
    ----------
    inp:
        Input with the query string to search for.
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    SearchExperimentsOutput
        Top 10 matching experiment summaries.
    """
    try:
        raw = await client.get(
            "/api/experiments",
            params={"limit": _SEARCH_LIMIT},
            tool_name=_TOOL_NAME_SEARCH_EXPERIMENTS,
        )
    except ToolCallError as exc:
        logger.warning("search_experiments failed to fetch: %s", exc)
        return SearchExperimentsOutput(experiments=[], query=inp.query)

    experiments = raw.get("experiments", [])
    query_lower = inp.query.lower()

    matches: list[dict[str, Any]] = []
    for exp in experiments:
        searchable = " ".join([
            (exp.get("hypothesis") or exp.get("description") or ""),
            json.dumps(exp.get("tags", [])),
            exp.get("instrument", ""),
            exp.get("name", ""),
        ]).lower()

        if query_lower in searchable:
            matches.append(exp)
            if len(matches) >= _SEARCH_MAX_RESULTS:
                break

    summaries = [_experiment_to_summary(m) for m in matches]
    return SearchExperimentsOutput(experiments=summaries, query=inp.query)


# ---------------------------------------------------------------------------
# Tool 6: check_data_availability
# ---------------------------------------------------------------------------

def _exec_inspect_capability(inp: dict, client) -> dict:
    field_name = inp["field_name"]
    from backend.strategies.capabilities import inspect_capability
    record = inspect_capability(field_name)
    return {
        "name": record.name,
        "taxonomy": record.taxonomy.value,
        "description": record.description,
        "available": record.available,
        "resolution_hint": record.resolution_hint,
        "requires_feature_run": record.requires_feature_run,
    }


async def check_data_availability(
    inp: CheckDataAvailabilityInput,
    client: MedallionClient,
) -> CheckDataAvailabilityOutput:
    """Check whether market data exists for a specific instrument, timeframe, and date range.

    Calls GET /api/market-data/ranges?instrument={instrument} and inspects
    the coverage record matching the requested timeframe.

    Never raises — returns needs_ingestion=True on API errors so the caller
    can always proceed to the ingestion path safely.

    Parameters
    ----------
    inp:
        Input with instrument, timeframe, start_date, end_date.
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    CheckDataAvailabilityOutput
        Coverage summary including whether ingestion is needed.
    """
    try:
        raw = await client.get(
            "/api/market-data/ranges",
            params={"instrument": inp.instrument},
            tool_name=_TOOL_NAME_CHECK_DATA,
        )
    except ToolCallError as exc:
        logger.warning("check_data_availability API call failed: %s", exc)
        return CheckDataAvailabilityOutput(
            has_data=False,
            coverage_start=None,
            coverage_end=None,
            needs_ingestion=True,
            message=(
                f"Could not check data availability for {inp.instrument} "
                f"(API error: {exc.detail}). Ingestion will be needed."
            ),
        )

    ranges: list[dict[str, Any]] = raw.get("ranges", [])

    # Find the entry matching instrument + timeframe
    tf_upper = inp.timeframe.upper()
    match: dict[str, Any] | None = None
    for entry in ranges:
        if (
            entry.get("instrument_id", "").upper() == inp.instrument.upper()
            and entry.get("timeframe", "").upper() == tf_upper
        ):
            match = entry
            break

    # No record at all — data definitely missing
    if match is None or not match.get("has_data"):
        return CheckDataAvailabilityOutput(
            has_data=False,
            coverage_start=None,
            coverage_end=None,
            needs_ingestion=True,
            message=(
                f"No {inp.timeframe} data found for {inp.instrument}. "
                f"Ingestion is required before running a backtest."
            ),
        )

    coverage_start = match.get("start")  # ISO string or None
    coverage_end = match.get("end")      # ISO string or None

    # Compare coverage boundaries against the requested range.
    # We only need the date portion (first 10 chars of ISO string) for the
    # comparison so we avoid a full datetime parse.
    requested_start = inp.start_date[:10]
    requested_end = inp.end_date[:10]

    # Treat missing boundaries as no coverage
    if coverage_start is None or coverage_end is None:
        return CheckDataAvailabilityOutput(
            has_data=False,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            needs_ingestion=True,
            message=(
                f"{inp.instrument} {inp.timeframe} data exists but coverage "
                f"boundaries are unknown. Ingestion is recommended."
            ),
        )

    cov_start_date = coverage_start[:10]
    cov_end_date = coverage_end[:10]

    fully_covered = (cov_start_date <= requested_start) and (cov_end_date >= requested_end)

    if fully_covered:
        message = (
            f"{inp.instrument} {inp.timeframe} data is available from "
            f"{cov_start_date} to {cov_end_date}, which fully covers the "
            f"requested range {requested_start} to {requested_end}."
        )
    else:
        message = (
            f"{inp.instrument} {inp.timeframe} data only covers "
            f"{cov_start_date} to {cov_end_date}, which does not fully cover "
            f"the requested range {requested_start} to {requested_end}. "
            f"Ingestion is required."
        )

    return CheckDataAvailabilityOutput(
        has_data=True,
        coverage_start=cov_start_date,
        coverage_end=cov_end_date,
        needs_ingestion=not fully_covered,
        message=message,
    )


# ---------------------------------------------------------------------------
# Tool 8: search_memories
# ---------------------------------------------------------------------------

_TOOL_NAME_SEARCH_MEMORIES = "search_memories"


async def search_memories(
    inp: SearchMemoriesInput,
    client: MedallionClient,
) -> SearchMemoriesOutput:
    """Search research memories by instrument, timeframe, tags, and outcome."""
    params: dict = {}
    if inp.instrument is not None:
        params["instrument"] = inp.instrument
    if inp.timeframe is not None:
        params["timeframe"] = inp.timeframe
    if inp.tags is not None:
        params["tags"] = ",".join(inp.tags)
    if inp.outcome is not None:
        params["outcome"] = inp.outcome
    params["limit"] = inp.limit

    try:
        raw = await client.get(
            "/api/memories",
            params=params,
            tool_name=_TOOL_NAME_SEARCH_MEMORIES,
        )
    except ToolCallError as exc:
        logger.warning("search_memories failed: %s", exc)
        return SearchMemoriesOutput(memories=[], count=0)

    memories_raw = raw if isinstance(raw, list) else raw.get("memories", raw) if isinstance(raw, dict) else []
    summaries = []
    for m in (memories_raw if isinstance(memories_raw, list) else []):
        summaries.append(MemorySummary(
            id=m.get("id", ""),
            instrument=m.get("instrument", ""),
            timeframe=m.get("timeframe", ""),
            outcome=m.get("outcome", "NEUTRAL"),
            theory=m.get("theory", "")[:200],
            learnings=m.get("learnings", []),
            tags=m.get("tags", []),
            sharpe=m.get("sharpe"),
            total_trades=m.get("total_trades"),
            created_at=str(m.get("created_at", "")),
        ))

    return SearchMemoriesOutput(memories=summaries, count=len(summaries))


# ---------------------------------------------------------------------------
# Bedrock tool specs
# ---------------------------------------------------------------------------

CHAT_READ_TOOLS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "get_experiment",
            "description": (
                "Fetch a full experiment record by ID including hypothesis, "
                "strategy, backtest results, and status."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "experiment_id": {
                            "type": "string",
                            "description": "The experiment ID to fetch.",
                        },
                    },
                    "required": ["experiment_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_backtest_result",
            "description": (
                "Fetch a backtest run record with performance metrics, "
                "per-regime metrics, and a summary of trades (first 50)."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "backtest_run_id": {
                            "type": "string",
                            "description": "The backtest run ID to fetch.",
                        },
                    },
                    "required": ["backtest_run_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_strategy_definition",
            "description": (
                "Fetch a strategy definition by ID, returning the name, "
                "type, rules DSL definition_json, and tags."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "strategy_id": {
                            "type": "string",
                            "description": "The strategy ID to fetch.",
                        },
                    },
                    "required": ["strategy_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_recent_experiments",
            "description": (
                "List recent experiments sorted by creation date (newest first). "
                "Optionally filter by instrument. Returns compact summaries."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "n": {
                            "type": "integer",
                            "description": "Maximum number of experiments to return (default 10).",
                        },
                        "instrument": {
                            "type": "string",
                            "description": "Optional instrument filter, e.g. 'EUR_USD'.",
                        },
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_experiments",
            "description": (
                "Search experiments by keyword. Matches against hypothesis, "
                "tags, instrument, and name. Returns up to 10 matching summaries."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query string (case-insensitive substring match).",
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "check_data_availability",
            "description": (
                "Check whether market data exists for a specific instrument, timeframe, "
                "and date range. Always call this before proposing a backtest to verify "
                "data coverage. Returns has_data, coverage dates, needs_ingestion flag, "
                "and a human-readable message."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "instrument": {
                            "type": "string",
                            "description": "Instrument ID, e.g. EUR_USD.",
                        },
                        "timeframe": {
                            "type": "string",
                            "description": "Timeframe: M1, H1, H4, or D.",
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Start date in YYYY-MM-DD format.",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in YYYY-MM-DD format.",
                        },
                    },
                    "required": ["instrument", "timeframe", "start_date", "end_date"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "inspect_capability",
            "description": (
                "Classify a named field or capability and determine whether it is available "
                "in the current strategy context. Returns the taxonomy (MARKET_FEATURE, "
                "STATE_MARKER, NATIVE_PRIMITIVE, SIGNAL_FIELD, or UNKNOWN), a description, "
                "availability status, requires_feature_run flag, and a resolution hint. "
                "If requires_feature_run=true, the field is not available without a feature run. "
                "Call this before saying a feature or capability is unsupported."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "field_name": {
                            "type": "string",
                            "description": "The field or capability name to inspect (e.g. 'days_in_trade', 'day_of_week', 'exit_before_weekend')",
                        }
                    },
                    "required": ["field_name"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_native_fields",
            "description": (
                "Return all fields unconditionally available in the backtest bar context "
                "(OHLCV, volume, trade lifecycle markers). Use this to see which fields are "
                "safe to use in strategy rules without a feature_run_id."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_memories",
            "description": (
                "Search research memories to find what strategies and hypotheses have been "
                "tried before for a given instrument or theme. Returns theory, learnings, "
                "outcome (POSITIVE/NEGATIVE/NEUTRAL), and sharpe for each memory. "
                "Use this to avoid repeating failed approaches and build on successful ones."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "instrument": {
                            "type": "string",
                            "description": "Filter by instrument, e.g. EUR_USD.",
                        },
                        "timeframe": {
                            "type": "string",
                            "description": "Filter by timeframe: M1, H1, H4, D.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by tags (any match).",
                        },
                        "outcome": {
                            "type": "string",
                            "enum": ["POSITIVE", "NEGATIVE", "NEUTRAL"],
                            "description": "Filter by outcome.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 10).",
                        },
                    },
                    "required": [],
                }
            },
        }
    },
]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, Any] = {
    "get_experiment": (get_experiment, GetExperimentInput),
    "get_backtest_result": (get_backtest_result, GetBacktestResultInput),
    "get_strategy_definition": (get_strategy_definition, GetStrategyDefinitionInput),
    "list_recent_experiments": (list_recent_experiments, ListRecentExperimentsInput),
    "search_experiments": (search_experiments, SearchExperimentsInput),
    "check_data_availability": (check_data_availability, CheckDataAvailabilityInput),
    "search_memories": (search_memories, SearchMemoriesInput),
}


async def dispatch_chat_read_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    client: MedallionClient,
) -> dict[str, Any]:
    """Dispatch a chat read tool call to the correct function.

    Parameters
    ----------
    tool_name:
        One of the read tool names.
    tool_input:
        Raw dict of tool input parameters (from Bedrock tool_use block).
    client:
        MedallionClient for HTTP requests.

    Returns
    -------
    dict
        The tool result as a JSON-safe dict (model_dump with mode="json").

    Raises
    ------
    ToolCallError
        If the tool name is not recognized or the underlying tool raises.
    """
    if tool_name == "inspect_capability":
        return _exec_inspect_capability(tool_input, client)

    if tool_name == "list_native_fields":
        from backend.strategies.capabilities import list_native_fields
        return {"native_fields": list_native_fields()}

    entry = _TOOL_DISPATCH.get(tool_name)
    if entry is None:
        raise ToolCallError(
            tool_name=tool_name,
            status_code=400,
            detail=f"Unknown chat read tool: '{tool_name}'",
        )

    func, input_cls = entry
    inp = input_cls.model_validate(tool_input)
    result = await func(inp, client)
    return result.model_dump(mode="json")
