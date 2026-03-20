"""Signal materialization job runner.

Background job wrapper that calls the materialize_signal pipeline and
updates job status throughout.  Designed to run inside a FastAPI
BackgroundTasks thread.
"""
from __future__ import annotations

try:
    from backend.signals.materialize import _materialize_signal_sync  # type: ignore[attr-defined]
except ImportError:
    # Team 1 may not have written _materialize_signal_sync yet.  Fall back to
    # the public materialize_signal function which accepts the same arguments.
    from backend.signals.materialize import materialize_signal as _materialize_signal_sync  # type: ignore[assignment]

from backend.schemas.requests import MaterializeSignalRequest


def run_materialize_signal_job(
    job_id: str,
    signal_id: str,
    request: MaterializeSignalRequest,
    market_repo,
    metadata_repo,
    artifact_repo,
    job_manager,
) -> None:
    """Background job wrapper around _materialize_signal_sync().

    Args:
        job_id:        Pre-created JobRun ID.
        signal_id:     ID of the Signal to materialize.
        request:       MaterializeSignalRequest carrying instrument_id,
                       timeframe, start, end.
        market_repo:   MarketDataRepository instance.
        metadata_repo: MetadataRepository instance.
        artifact_repo: ArtifactRepository instance.
        job_manager:   JobManager instance.
    """
    job_manager.start(job_id)

    try:
        result = _materialize_signal_sync(
            signal_id=signal_id,
            instrument=request.instrument_id,
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            market_repo=market_repo,
            metadata_repo=metadata_repo,
            artifact_repo=artifact_repo,
        )
        job_manager.succeed(
            job_id,
            result_ref=f"signal_id={signal_id},row_count={len(result)}",
        )
    except Exception as exc:
        job_manager.fail(
            job_id,
            error_message=str(exc),
            error_code="MATERIALIZE_FAILED",
        )
        raise
