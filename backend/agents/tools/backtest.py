"""Backtest tool executors — submit, poll, and retrieve backtest artifacts."""
from __future__ import annotations

from backend.agents.tools.client import MedallionClient
from backend.agents.tools.schemas import (
    GetBacktestRunInput,
    GetBacktestRunOutput,
    GetBacktestTradesInput,
    GetBacktestTradesOutput,
    GetEquityCurveInput,
    GetEquityCurveOutput,
    GetHmmModelInput,
    GetHmmModelOutput,
    ListBacktestRunsInput,
    ListBacktestRunsOutput,
    PollJobInput,
    PollJobOutput,
    SubmitBacktestInput,
    SubmitBacktestOutput,
)


async def submit_backtest(
    inp: SubmitBacktestInput,
    client: MedallionClient,
) -> SubmitBacktestOutput:
    """Launch a backtest job.  Returns ``job_id`` and initial ``status``.

    Maps to: POST /api/backtests/jobs
    """
    body = inp.model_dump(mode="json")
    raw = await client.post("/api/backtests/jobs", body=body, tool_name="submit_backtest")
    return SubmitBacktestOutput.model_validate(raw)


async def poll_job(
    inp: PollJobInput,
    client: MedallionClient,
) -> PollJobOutput:
    """Fetch the current status of any job.  Does NOT loop — one request only.

    The graph node is responsible for looping until a terminal state is reached.
    Maps to: GET /api/jobs/{job_id}

    When ``status == "succeeded"``, ``result_ref`` contains the ``backtest_run_id``.
    """
    raw = await client.get(f"/api/jobs/{inp.job_id}", tool_name="poll_job")
    return PollJobOutput.model_validate(raw)


async def get_backtest_run(
    inp: GetBacktestRunInput,
    client: MedallionClient,
) -> GetBacktestRunOutput:
    """Retrieve a full backtest run summary including all performance metrics.

    Maps to: GET /api/backtests/runs/{run_id}
    """
    raw = await client.get(
        f"/api/backtests/runs/{inp.run_id}",
        tool_name="get_backtest_run",
    )
    return GetBacktestRunOutput.model_validate(raw)


async def get_backtest_trades(
    inp: GetBacktestTradesInput,
    client: MedallionClient,
) -> GetBacktestTradesOutput:
    """Retrieve the full trade log for a completed backtest run.

    Maps to: GET /api/backtests/runs/{run_id}/trades
    """
    raw = await client.get(
        f"/api/backtests/runs/{inp.run_id}/trades",
        tool_name="get_backtest_trades",
    )
    return GetBacktestTradesOutput.model_validate(raw)


async def get_equity_curve(
    inp: GetEquityCurveInput,
    client: MedallionClient,
) -> GetEquityCurveOutput:
    """Retrieve bar-by-bar equity and drawdown series for a backtest run.

    Maps to: GET /api/backtests/runs/{run_id}/equity
    """
    raw = await client.get(
        f"/api/backtests/runs/{inp.run_id}/equity",
        tool_name="get_equity_curve",
    )
    return GetEquityCurveOutput.model_validate(raw)


async def list_backtest_runs(
    inp: ListBacktestRunsInput,
    client: MedallionClient,
) -> ListBacktestRunsOutput:
    """List recent backtest runs.

    Maps to: GET /api/backtests/runs
    """
    raw = await client.get(
        "/api/backtests/runs",
        params={"limit": inp.limit},
        tool_name="list_backtest_runs",
    )
    return ListBacktestRunsOutput.model_validate(raw)


async def get_hmm_model(
    inp: GetHmmModelInput,
    client: MedallionClient,
) -> GetHmmModelOutput:
    """Retrieve HMM model metadata including regime state stats and label map.

    Maps to: GET /api/models/hmm/{model_id}
    """
    raw = await client.get(f"/api/models/hmm/{inp.model_id}", tool_name="get_hmm_model")
    return GetHmmModelOutput.model_validate(raw)
