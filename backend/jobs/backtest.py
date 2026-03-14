"""Backtest job runner.

Orchestrates the full backtest pipeline:
  1. Load the strategy definition and instantiate the right strategy class.
  2. Load bars + features + regime labels via the data loader.
  3. Create a BacktestRun metadata record.
  4. Run the event-driven engine.
  5. Persist trades, performance metrics, and equity/drawdown artifact.
  6. Update job and backtest run status.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend.backtest.costs import CostModel
from backend.backtest.data_loader import load_backtest_frame
from backend.backtest.engine import run_backtest
from backend.data.repositories import ArtifactRepository, MarketDataRepository, MetadataRepository
from backend.jobs.status import JobManager
from backend.schemas.enums import JobStatus, Timeframe
from backend.schemas.models import BacktestRun


def run_backtest_job(
    job_id: str,
    strategy_id: str | None,
    inline_strategy: dict[str, Any] | None,
    instrument: str,
    timeframe: Timeframe,
    test_start: datetime,
    test_end: datetime,
    cost_model_params: dict[str, Any],
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
    initial_equity: float = 100_000.0,
    feature_run_id: str | None = None,
    model_id: str | None = None,
    strategy_params: dict[str, Any] | None = None,
) -> str:
    """Execute a backtest job end-to-end and persist all results.

    Returns the backtest_run_id of the completed run.
    Raises on failure (job_manager.fail() is called before re-raising).
    """
    try:
        # ----------------------------------------------------------------
        # Stage 1: Load and instantiate strategy
        # ----------------------------------------------------------------
        job_manager.start(job_id, stage_label="loading_strategy")

        strategy_def = _resolve_strategy(strategy_id, inline_strategy, metadata_repo)
        strategy_instance = _build_strategy(strategy_def)
        params = strategy_params or strategy_def.get("definition_json", {})

        job_manager.progress(job_id, 20.0, stage_label="loading_data")

        # ----------------------------------------------------------------
        # Stage 2: Load aligned backtest frame
        # ----------------------------------------------------------------
        bars = load_backtest_frame(
            instrument_id=instrument,
            timeframe=timeframe,
            start=test_start,
            end=test_end,
            market_repo=market_repo,
            feature_run_id=feature_run_id,
            model_id=model_id,
        )

        if not bars:
            raise ValueError(
                f"No bars found for {instrument} {timeframe.value} "
                f"in range [{test_start.isoformat()}, {test_end.isoformat()})"
            )

        job_manager.progress(job_id, 40.0, stage_label="running_engine")

        # ----------------------------------------------------------------
        # Stage 3: Create BacktestRun record (before engine runs)
        # ----------------------------------------------------------------
        cost_model = CostModel.from_dict(cost_model_params)
        backtest_run = BacktestRun(
            job_id=job_id,
            strategy_id=strategy_id,
            inline_definition=inline_strategy if not strategy_id else None,
            instrument_id=instrument,
            timeframe=timeframe,
            test_start=test_start,
            test_end=test_end,
            parameters_json=params,
            cost_model_json=cost_model.to_dict(),
            status=JobStatus.RUNNING,
        )
        metadata_repo.save_backtest_run(backtest_run.model_dump())

        # ----------------------------------------------------------------
        # Stage 4: Run the event-driven engine
        # ----------------------------------------------------------------
        trades, metrics, equity, drawdown = run_backtest(
            strategy=strategy_instance,
            bars=bars,
            backtest_run=backtest_run,
            cost_model=cost_model,
            initial_equity=initial_equity,
            params=params,
        )

        job_manager.progress(job_id, 80.0, stage_label="persisting_results")

        # ----------------------------------------------------------------
        # Stage 5: Persist results
        # ----------------------------------------------------------------
        metadata_repo.save_trades([t.model_dump() for t in trades])
        metadata_repo.save_performance_metrics([m.model_dump() for m in metrics])

        # Equity + drawdown stored as a JSON artifact for the /equity endpoint.
        equity_payload = _build_equity_payload(
            [b["timestamp_utc"] for b in bars], equity, drawdown
        )
        equity_key = f"backtests/{backtest_run.id}/equity.json"
        artifact_repo.save(equity_key, json.dumps(equity_payload, default=str).encode())

        # Update run status now that all results are persisted.
        metadata_repo.update_backtest_run(backtest_run.id, {
            "status": JobStatus.SUCCEEDED.value,
            "result_ref": equity_key,
        })

        job_manager.succeed(job_id, result_ref=backtest_run.id)
        return backtest_run.id

    except Exception as exc:
        job_manager.fail(
            job_id=job_id,
            error_message=str(exc),
            error_code="BACKTEST_ERROR",
        )
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_strategy(
    strategy_id: str | None,
    inline_strategy: dict[str, Any] | None,
    metadata_repo: MetadataRepository,
) -> dict[str, Any]:
    """Load strategy definition from the metadata store or use the inline dict."""
    if strategy_id:
        record = metadata_repo.get_strategy(strategy_id)
        if record is None:
            raise ValueError(f"Strategy '{strategy_id}' not found")
        return record
    if inline_strategy:
        return inline_strategy
    raise ValueError("Either strategy_id or inline_strategy must be provided")


def _build_strategy(strategy_def: dict):
    """Instantiate the correct BaseStrategy subclass from a strategy record."""
    from backend.schemas.enums import StrategyType
    from backend.strategies.code_strategy import CodeStrategy
    from backend.strategies.rules_strategy import RulesStrategy

    # strategy_def may be a full Strategy record (with strategy_type + definition_json)
    # or a bare inline definition dict (treat as rules engine definition).
    raw_type = strategy_def.get("strategy_type", "")
    definition_json = strategy_def.get("definition_json", strategy_def)

    if raw_type in (StrategyType.RULES_ENGINE.value, StrategyType.RULES_ENGINE):
        return RulesStrategy(definition_json)

    if raw_type in (StrategyType.PYTHON.value, StrategyType.PYTHON):
        code = definition_json.get("code", "")
        class_name = definition_json.get("class_name")
        return CodeStrategy(code, class_name=class_name)

    # Fallback: detect rules engine from definition keys.
    if "entry_long" in definition_json or "entry_short" in definition_json:
        return RulesStrategy(definition_json)

    raise ValueError(
        f"Cannot determine strategy type from definition (strategy_type={raw_type!r})"
    )


def _build_equity_payload(
    timestamps: list[datetime],
    equity: list[float],
    drawdown: list[float],
) -> list[dict]:
    """Format equity + drawdown as a list of {timestamp, equity, drawdown} dicts."""
    return [
        {
            "timestamp": t.isoformat() if hasattr(t, "isoformat") else str(t),
            "equity": e,
            "drawdown": d,
        }
        for t, e, d in zip(timestamps, equity, drawdown)
    ]
