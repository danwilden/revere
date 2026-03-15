"""Medallion Platform — FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.deps import get_artifact_repo, get_job_manager, get_market_repo, get_metadata_repo
from backend.schemas.enums import JobStatus


def _recover_stale_jobs() -> None:
    """Fail any RUNNING jobs left over from a previous server process.

    A job that is RUNNING at startup is definitionally orphaned — its thread
    died with the previous process and will never call succeed() or fail().
    """
    jm = get_job_manager()
    for job in jm.list(limit=1000):
        if job.get("status") == JobStatus.RUNNING.value:
            jm.fail(
                job["id"],
                "Server restarted — job was interrupted",
                "SERVER_RESTART",
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize all singletons at startup so the first request is not slow.
    get_market_repo()
    get_metadata_repo()
    get_artifact_repo()
    _recover_stale_jobs()
    yield
    # Cleanup on shutdown
    get_market_repo().close()


app = FastAPI(
    title="Medallion — Forex Strategy Research Platform",
    version="0.1.0",
    description="Research, backtest, and trade Forex strategies.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Routers — imported and registered here as phases are built
# ---------------------------------------------------------------------------

# Phase 1
from apps.api.routes import ingestion, instruments, market_data
app.include_router(ingestion.router, prefix="/api/ingestion", tags=["ingestion"])
app.include_router(instruments.router, prefix="/api/instruments", tags=["instruments"])
app.include_router(market_data.router, prefix="/api/market-data", tags=["market-data"])

# Phase 2
from apps.api.routes import models as models_routes, signals as signals_routes
app.include_router(models_routes.router, prefix="/api/models", tags=["models"])
app.include_router(signals_routes.router, prefix="/api/signals", tags=["signals"])

# Phase 3
from apps.api.routes import strategies as strategies_routes
app.include_router(strategies_routes.router, prefix="/api/strategies", tags=["strategies"])

# Phase 4
from apps.api.routes import backtests as backtests_routes, jobs as jobs_routes
app.include_router(backtests_routes.router, prefix="/api/backtests", tags=["backtests"])
app.include_router(jobs_routes.router, prefix="/api/jobs", tags=["jobs"])

# Dukascopy download + ingest
from apps.api.routes import dukascopy as dukascopy_routes
app.include_router(dukascopy_routes.router, prefix="/api/dukascopy", tags=["dukascopy"])
