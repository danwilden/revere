"""Backtest API routes.

POST /api/backtests/jobs                        — submit backtest job (202)
GET  /api/backtests/jobs/{job_id}               — poll job status
GET  /api/backtests/jobs                        — list recent backtest jobs
GET  /api/backtests/runs/{run_id}               — backtest run summary + metrics
GET  /api/backtests/runs/{run_id}/trades        — trade log
GET  /api/backtests/runs/{run_id}/equity        — equity curve + drawdown series
GET  /api/backtests/runs                        — list all backtest runs
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_artifact_repo, get_job_manager, get_market_repo, get_metadata_repo
from backend.jobs.backtest import submit_backtest_job
from backend.schemas.enums import JobType
from backend.schemas.requests import (
    BacktestEquityResponse,
    BacktestJobListResponse,
    BacktestJobRequest,
    BacktestRunListResponse,
    BacktestRunSummaryResponse,
    BacktestTradesResponse,
    JobCreatedResponse,
    JobResponse,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Job submission and polling
# ---------------------------------------------------------------------------

@router.post("/jobs", response_model=JobCreatedResponse, status_code=202)
async def submit_backtest_job_route(
    body: BacktestJobRequest,
    metadata_repo=Depends(get_metadata_repo),
    market_repo=Depends(get_market_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager=Depends(get_job_manager),
):
    """Submit a new backtest job.  Returns job_id for status polling."""
    job_id = submit_backtest_job(
        body=body,
        job_manager=job_manager,
        metadata_repo=metadata_repo,
        market_repo=market_repo,
        artifact_repo=artifact_repo,
    )
    job = job_manager.get(job_id)
    return JobCreatedResponse(job_id=job_id, status=job["status"])


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_backtest_job(
    job_id: str,
    job_manager=Depends(get_job_manager),
):
    """Poll the status of a backtest job."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.get("/jobs", response_model=BacktestJobListResponse)
async def list_backtest_jobs(
    limit: int = 20,
    job_manager=Depends(get_job_manager),
):
    """List recent backtest jobs (newest first)."""
    jobs = job_manager.list(job_type=JobType.BACKTEST.value, limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}", response_model=BacktestRunSummaryResponse)
async def get_backtest_run(
    run_id: str,
    metadata_repo=Depends(get_metadata_repo),
):
    """Return backtest run summary with inline performance metrics."""
    run = metadata_repo.get_backtest_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")
    metrics = metadata_repo.get_performance_metrics(run_id)
    return {"run": run, "metrics": metrics}


@router.get("/runs/{run_id}/trades", response_model=BacktestTradesResponse)
async def get_backtest_trades(
    run_id: str,
    metadata_repo=Depends(get_metadata_repo),
):
    """Return the complete trade log for a backtest run."""
    run = metadata_repo.get_backtest_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")
    trades = metadata_repo.get_trades(run_id)
    return {"run_id": run_id, "trades": trades, "count": len(trades)}


@router.get("/runs/{run_id}/equity", response_model=BacktestEquityResponse)
async def get_equity_curve(
    run_id: str,
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
):
    """Return the bar-by-bar equity curve and drawdown series."""
    run = metadata_repo.get_backtest_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run '{run_id}' not found")

    equity_key = f"backtests/{run_id}/equity.json"
    if not artifact_repo.exists(equity_key):
        raise HTTPException(
            status_code=404,
            detail="Equity curve not yet available (backtest may still be running)",
        )

    raw = artifact_repo.load(equity_key)
    equity_data = json.loads(raw.decode())
    return {"run_id": run_id, "equity_curve": equity_data}


@router.get("/runs", response_model=BacktestRunListResponse)
async def list_backtest_runs(
    limit: int = 20,
    metadata_repo=Depends(get_metadata_repo),
):
    """List all backtest runs (newest first)."""
    runs = metadata_repo.list_backtest_runs(limit=limit)
    return {"runs": runs, "count": len(runs)}


