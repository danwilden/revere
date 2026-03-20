"""Process pending backtests when an ingestion job completes.

When a Dukascopy download job succeeds, any backtest registered for that job_id
is submitted automatically so the user does not need to re-confirm in chat.
"""
from __future__ import annotations

import logging

from backend.deps import get_artifact_repo, get_metadata_repo
from backend.features.compute import run_feature_pipeline, FEATURE_CODE_VERSION
from backend.jobs.backtest import submit_backtest_job
from backend.schemas.requests import BacktestJobRequest

logger = logging.getLogger(__name__)

# Computed feature fields that require a feature run to be joined into bars.
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


def _inline_strategy_needs_features(node: object) -> bool:
    """Return True if any rule node references a computed feature field."""
    if isinstance(node, dict):
        if node.get("field") in _FEATURE_PIPELINE_FIELDS:
            return True
        if node.get("field2") in _FEATURE_PIPELINE_FIELDS:
            return True
        return any(_inline_strategy_needs_features(v) for v in node.values())
    if isinstance(node, list):
        return any(_inline_strategy_needs_features(child) for child in node)
    return False


def process_pending_backtests_for_job(
    job_id: str,
    job_manager,
    market_repo,
) -> None:
    """If a backtest was registered for this ingestion job_id, submit it and clear the record.

    Called from the Dukascopy job runner after job_manager.succeed(job_id, ...).
    Uses get_metadata_repo() and get_artifact_repo() so the caller does not need to pass them.
    """
    metadata_repo = get_metadata_repo()
    payload = metadata_repo.get_pending_backtest(job_id)
    if not payload:
        return
    try:
        # Stored payload has id; BacktestJobRequest ignores extra fields. test_start/test_end are ISO strings.
        body = BacktestJobRequest.model_validate(payload)
    except Exception as exc:
        logger.warning("Invalid pending backtest payload for job_id=%s: %s", job_id, exc)
        metadata_repo.delete_pending_backtest(job_id)
        return
    # If the strategy references feature fields and feature_run_id was not
    # resolved at registration time (bars weren't available yet), resolve now.
    if body.feature_run_id is None and body.inline_strategy is not None:
        if _inline_strategy_needs_features(body.inline_strategy):
            try:
                feature_run_id = run_feature_pipeline(
                    instrument_id=body.instrument,
                    timeframe=body.timeframe,
                    start=body.test_start,
                    end=body.test_end,
                    market_repo=market_repo,
                    metadata_repo=metadata_repo,
                )
                body = body.model_copy(update={"feature_run_id": feature_run_id})
            except Exception as exc:
                # Log warning and proceed — backtest will fail with a clear error
                logger.warning(
                    "Could not resolve feature_run_id for pending backtest: %s", exc
                )

    artifact_repo = get_artifact_repo()
    try:
        backtest_job_id = submit_backtest_job(
            body=body,
            job_manager=job_manager,
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
        )
        logger.info(
            "Submitted pending backtest for ingestion job_id=%s -> backtest job_id=%s",
            job_id,
            backtest_job_id,
        )
    finally:
        metadata_repo.delete_pending_backtest(job_id)
