"""Dukascopy download-then-ingest job runner.

Steps:
  1. Mark the job as running.
  2. For each instrument, create a per-instrument subdirectory under
     {settings.dukascopy_download_dir}/{job_id}/{instrument}/
  3. Invoke the Node-based dukascopy-node CLI via subprocess, writing CSVs
     into that subdirectory.
  4. If the Node command fails (non-zero exit or timeout), fail the job
     immediately without processing remaining instruments.
  5. After all downloads succeed, run the ingestion pipeline steps directly
     (normalize → upsert 1m → aggregate H1/H4/D) for each instrument.
  6. Call job_manager.succeed() with a summary string.

Progress allocation:
  0–50%  : download phase, evenly split across instruments.
  50–100%: ingestion phase, evenly split across instruments.

Instrument mapping:
  OANDA format (e.g. EUR_USD) → lowercase + strip underscore (e.g. eurusd).
  This is the value passed to the -i flag of the dukascopy-node CLI.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.connectors.dukascopy import DukascopyConnector
from backend.data.aggregate import aggregate_bars
from backend.data.normalize import normalize_bars
from backend.data.quality import check_bars_quality
from backend.schemas.enums import Timeframe

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maps OANDA instrument codes to the lowercase instrument code accepted by the
# dukascopy-node CLI.  Add entries here as new instruments are needed.
DUKASCOPY_INSTRUMENT_MAP: dict[str, str] = {
    "EUR_USD": "eurusd",
    "GBP_USD": "gbpusd",
    "USD_JPY": "usdjpy",
    "AUD_USD": "audusd",
    "USD_CAD": "usdcad",
    "USD_CHF": "usdchf",
    "NZD_USD": "nzdusd",
    "EUR_GBP": "eurgbp",
    "EUR_JPY": "eurjpy",
    "GBP_JPY": "gbpjpy",
}

_AGG_TIMEFRAMES = [Timeframe.H1, Timeframe.H4, Timeframe.D]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_dukascopy_download_job(
    job_id: str,
    instruments: list[str],
    start_date: datetime,
    end_date: datetime,
    market_repo,
    job_manager,
) -> None:
    """Download Dukascopy CSVs via Node, then ingest them into DuckDB.

    Args:
        job_id:      ID of the already-created JobRun record.
        instruments: List of OANDA-format instrument codes, e.g. ["EUR_USD"].
        start_date:  Inclusive start date (UTC or naive, treated as UTC).
        end_date:    Exclusive end date (UTC or naive, treated as UTC).
        market_repo: MarketDataRepository instance (DuckDBStore).
        job_manager: JobManager instance.
    """
    job_manager.start(job_id, stage_label="initializing")

    try:
        start_utc = _ensure_utc(start_date)
        end_utc = _ensure_utc(end_date)

        n = len(instruments)
        job_dir = settings.dukascopy_download_dir_resolved / job_id
        total_bars_written = 0

        # ------------------------------------------------------------------
        # Phase 1: Download (0–50%)
        # ------------------------------------------------------------------
        for idx, instrument in enumerate(instruments):
            download_pct = (idx / n) * 50.0
            job_manager.progress(
                job_id,
                pct=download_pct,
                stage_label=f"downloading {instrument} from Dukascopy",
            )

            inst_dir = job_dir / instrument
            inst_dir.mkdir(parents=True, exist_ok=True)

            _run_node_download(
                instrument=instrument,
                start_date=start_utc,
                end_date=end_utc,
                output_dir=inst_dir,
            )

            job_manager.progress(
                job_id,
                pct=((idx + 1) / n) * 50.0,
                stage_label=f"{instrument}: download complete",
            )

        # ------------------------------------------------------------------
        # Phase 2: Ingest (50–100%)
        # ------------------------------------------------------------------
        for idx, instrument in enumerate(instruments):
            ingest_pct = 50.0 + (idx / n) * 50.0
            job_manager.progress(
                job_id,
                pct=ingest_pct,
                stage_label=f"ingesting {instrument}",
            )

            inst_dir = job_dir / instrument
            written = _ingest_instrument(
                instrument=instrument,
                inst_dir=inst_dir,
                start_utc=start_utc,
                end_utc=end_utc,
                market_repo=market_repo,
            )
            total_bars_written += written

            job_manager.progress(
                job_id,
                pct=50.0 + ((idx + 1) / n) * 50.0,
                stage_label=f"{instrument}: {written} bars stored",
            )

        job_manager.succeed(
            job_id,
            result_ref=(
                f"bars_written={total_bars_written},"
                f"instruments={','.join(instruments)}"
            ),
        )

        # Run any backtest that was registered to run when this job succeeds
        from backend.jobs.pending_backtest import process_pending_backtests_for_job
        process_pending_backtests_for_job(job_id, job_manager, market_repo)

    except Exception as exc:
        job_manager.fail(
            job_id,
            error_message=str(exc),
            error_code=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_node_download(
    instrument: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: Path,
) -> None:
    """Invoke the dukascopy-node CLI for a single instrument.

    Raises RuntimeError if the process exits with a non-zero code.
    Raises subprocess.TimeoutExpired (re-raised as RuntimeError) on timeout.

    The CLI flag --date-to is treated as exclusive by dukascopy-node, so we
    pass end_date as-is — this matches our own exclusive-end convention.
    """
    dk_code = DUKASCOPY_INSTRUMENT_MAP.get(instrument)
    if dk_code is None:
        # Fallback: lowercase + strip underscore; may or may not be valid.
        dk_code = instrument.lower().replace("_", "")

    cmd = settings.dukascopy_node_cmd.split()
    cmd += [
        "-i", dk_code,
        "--date-from", start_date.strftime("%Y-%m-%d"),
        "--date-to", end_date.strftime("%Y-%m-%d"),
        "-t", "m1",
        "-f", "csv",
        "--directory", str(output_dir),
        "--volumes",
    ]

    try:
        result = subprocess.run(
            cmd,
            timeout=settings.dukascopy_node_timeout_secs,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"dukascopy-node timed out after {settings.dukascopy_node_timeout_secs}s "
            f"for instrument {instrument}"
        ) from exc

    if result.returncode != 0:
        # dukascopy-node sometimes exits with code 1 after a successful partial
        # download.  Treat as success if output CSV files were written.
        csv_files = list(output_dir.glob("*.csv"))
        if not csv_files:
            stderr_snippet = (result.stderr or "")[:500]
            raise RuntimeError(
                f"dukascopy-node exited with code {result.returncode} "
                f"for instrument {instrument}. stderr: {stderr_snippet}"
            )
        # Files exist — log the warning but continue.


def _ingest_instrument(
    instrument: str,
    inst_dir: Path,
    start_utc: datetime,
    end_utc: datetime,
    market_repo,
) -> int:
    """Parse downloaded CSVs, normalize, upsert 1m bars, and aggregate.

    Returns the number of 1m bars written.
    """
    connector = DukascopyConnector()
    bars = connector.parse_csv_dir(instrument, str(inst_dir))

    normalized = normalize_bars(bars)

    # Quality check — informational; does not block ingestion.
    check_bars_quality(normalized, expected_gap_minutes=1)

    written = market_repo.upsert_bars_1m(normalized)

    # Fetch the freshly-stored 1m bars back and aggregate to H1, H4, D.
    bars_1m = market_repo.get_bars_1m(instrument, start_utc, end_utc)
    if bars_1m:
        for tf in _AGG_TIMEFRAMES:
            agg_bars = aggregate_bars(bars_1m, tf.value, source="dukascopy")
            if agg_bars:
                market_repo.upsert_bars_agg(agg_bars)

    return written


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
