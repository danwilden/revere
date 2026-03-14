"""
Structured logging setup for the trading system.

Every run logs to:
    - stderr: human-readable colored output
    - JSONL file: machine-readable structured records (one JSON per line)

The JSONL file is the audit trail — it records all signals, orders, fills,
P&L, and metadata for every execution session.

Usage:
    from forex_system.monitoring.logger import setup_logging, log_run_metadata

    log_path = setup_logging(run_name="daily_paper_20240115")
    # Now all logger.info() calls go to both console and the JSONL file.

    log_run_metadata(
        data_timestamps={"EUR_USD_H1": "2024-01-15T17:00:00Z"},
        feature_hash="a1b2c3d4",
        model_version="EUR_USD_H1_lightgbm_fold4",
        signals=[{"instrument": "EUR_USD", "direction": 1, "prob": 0.62}],
    )
"""

import sys
from pathlib import Path

from loguru import logger

from forex_system.config import settings


def setup_logging(
    log_dir: Path | None = None,
    run_name: str = "run",
) -> Path:
    """
    Configure loguru for console (human) and JSONL file (audit) output.

    Args:
        log_dir:  Directory to write the JSONL log file. Defaults to data/reports/.
        run_name: Used as the log file basename. e.g. "daily_paper_20240115".

    Returns:
        Path to the JSONL log file.
    """
    log_dir = log_dir or settings.data_reports
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_name}.jsonl"

    # Remove the default loguru handler
    logger.remove()

    # Console: human-readable with colors
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        level=settings.log_level,
        colorize=True,
    )

    # File: JSONL (serialize=True outputs JSON records)
    logger.add(
        str(log_path),
        level="DEBUG",
        serialize=True,    # Each log record is a JSON object on its own line
        rotation="50 MB",
        retention="30 days",
        compression="gz",
    )

    logger.info(f"Logging initialized | run={run_name} | file={log_path}")
    return log_path


def log_run_metadata(
    data_timestamps: dict[str, str],
    feature_hash: str,
    model_version: str,
    signals: list[dict],
) -> None:
    """
    Emit a structured audit log entry for a full daily/session run.

    Args:
        data_timestamps: {instrument_granularity: last_bar_time}
        feature_hash:    From FeaturePipeline.feature_hash()
        model_version:   Model artifact filename used
        signals:         List of signal dicts {instrument, direction, prob, ...}
    """
    logger.info(
        "RUN_METADATA | "
        f"feature_hash={feature_hash} | "
        f"model={model_version} | "
        f"n_signals={len(signals)} | "
        f"data_keys={list(data_timestamps.keys())}"
    )


def log_order(
    instrument: str,
    units: int,
    order_type: str,
    stop_loss: float | None = None,
    fill_price: float | None = None,
    pnl: float | None = None,
) -> None:
    """Log an order submission or fill for the audit trail."""
    logger.info(
        f"ORDER | {instrument} {units:+d} {order_type} | "
        f"stop={stop_loss} fill={fill_price} pnl={pnl}"
    )
