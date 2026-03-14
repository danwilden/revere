"""Ingestion job runner.

Orchestrates the full data ingestion pipeline:
  1. Create JobRun record (status: queued)
  2. For each instrument:
     a. Fetch bars from source connector in chunks
     b. Normalize bars (dedup, OHLC validation, quality flags)
     c. Upsert into DuckDB bars_1m
     d. Update job progress
  3. Run bar aggregation for H1, H4, D timeframes
  4. Mark job as succeeded or failed

Designed to run synchronously — called from a background thread or worker.
Progress updates are written to the metadata store so the API can poll them.

Usage (from API route or worker):
    from backend.jobs.ingestion import run_ingestion_job

    job = job_manager.create(JobType.INGESTION, params={"instruments": [...], ...})
    run_ingestion_job(job.id, request, market_repo, job_manager)
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone

from backend.connectors.oanda import OandaConnector, OandaConnectorError
from backend.connectors.dukascopy import DukascopyConnector
from backend.data.aggregate import aggregate_bars
from backend.data.normalize import normalize_bars
from backend.data.quality import check_bars_quality
from backend.schemas.enums import DataSource, Timeframe


_AGG_TIMEFRAMES = [Timeframe.H1, Timeframe.H4, Timeframe.D]


def run_ingestion_job(
    job_id: str,
    instruments: list[str],
    source: DataSource,
    start_date: datetime,
    end_date: datetime,
    market_repo,
    job_manager,
    dukascopy_dir: str | None = None,
) -> None:
    """Run a full ingestion job synchronously.

    Args:
        job_id:         ID of the already-created JobRun record.
        instruments:    List of instrument symbols e.g. ["EUR_USD", "GBP_USD"]
        source:         DataSource.OANDA or DataSource.DUKASCOPY
        start_date:     Inclusive start date (UTC)
        end_date:       Exclusive end date (UTC)
        market_repo:    MarketDataRepository instance (DuckDBStore)
        job_manager:    JobManager instance
        dukascopy_dir:  Path to directory of Dukascopy CSV files (required for
                        DataSource.DUKASCOPY; ignored for OANDA)
    """
    job_manager.start(job_id, stage_label="initializing")

    try:
        n_instruments = len(instruments)
        total_bars_written = 0

        for idx, instrument in enumerate(instruments):
            base_pct = (idx / n_instruments) * 90.0  # 90% for fetching, 10% for agg
            job_manager.progress(
                job_id,
                pct=base_pct,
                stage_label=f"fetching {instrument} from {source.value}",
            )

            bars = _fetch_bars(
                instrument=instrument,
                source=source,
                start_date=start_date,
                end_date=end_date,
                dukascopy_dir=dukascopy_dir,
            )

            # Normalize
            normalized = normalize_bars(bars)

            # Quality check (stored in job result summary — not blocking)
            quality_report = check_bars_quality(normalized, expected_gap_minutes=1)

            # Upsert 1m bars
            written = market_repo.upsert_bars_1m(normalized)
            total_bars_written += written

            job_manager.progress(
                job_id,
                pct=base_pct + (90.0 / n_instruments * 0.7),
                stage_label=f"{instrument}: {written} bars stored, running aggregation",
            )

            # Aggregate to higher timeframes
            _aggregate_and_store(
                instrument=instrument,
                market_repo=market_repo,
                start=start_date,
                end=end_date,
                source=source.value,
            )

            job_manager.progress(
                job_id,
                pct=base_pct + (90.0 / n_instruments),
                stage_label=f"{instrument}: complete",
            )

        job_manager.succeed(
            job_id,
            result_ref=f"bars_written={total_bars_written},instruments={','.join(instruments)}",
        )

    except Exception as exc:
        job_manager.fail(
            job_id,
            error_message=str(exc),
            error_code=type(exc).__name__,
        )
        raise


def _fetch_bars(
    instrument: str,
    source: DataSource,
    start_date: datetime,
    end_date: datetime,
    dukascopy_dir: str | None,
) -> list[dict]:
    """Dispatch to the appropriate connector and return raw bar dicts."""
    start_utc = _ensure_utc(start_date)
    end_utc = _ensure_utc(end_date)

    if source == DataSource.OANDA:
        connector = OandaConnector()
        return connector.fetch_bars_1m(instrument, start_utc, end_utc)

    elif source == DataSource.DUKASCOPY:
        if not dukascopy_dir:
            raise ValueError(
                "dukascopy_dir must be provided for DataSource.DUKASCOPY"
            )
        connector = DukascopyConnector()
        return connector.parse_csv_dir(instrument, dukascopy_dir)

    else:
        raise ValueError(f"Unsupported data source: {source}")


def _aggregate_and_store(
    instrument: str,
    market_repo,
    start: datetime,
    end: datetime,
    source: str,
) -> None:
    """Fetch 1m bars from DuckDB and write aggregated bars for H1, H4, D."""
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)

    bars_1m = market_repo.get_bars_1m(instrument, start_utc, end_utc)
    if not bars_1m:
        return

    for tf in _AGG_TIMEFRAMES:
        agg_bars = aggregate_bars(bars_1m, tf.value, source=source)
        if agg_bars:
            market_repo.upsert_bars_agg(agg_bars)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
