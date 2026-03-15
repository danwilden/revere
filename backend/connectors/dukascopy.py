"""Dukascopy connector — parse downloaded tick/minute CSV files.

Dukascopy provides historical data as downloadable CSV files. This connector
reads those files and normalizes them to our standard bar dict format.

Supported CSV formats:
1. Minute OHLCV (exported from JForex or Dukascopy tick converter):
       Timestamp,Open,High,Low,Close,Volume
       01.01.2024 00:00:00.000,1.10500,1.10520,1.10480,1.10510,125.3

2. Bid/Ask tick CSV (raw Dukascopy format):
       Timestamp,Bid,Ask,Volume
   These are aggregated to 1-minute bars on the fly.

Usage:
    from backend.connectors.dukascopy import DukascopyConnector
    connector = DukascopyConnector()
    bars = connector.parse_csv("EUR_USD", "/path/to/EURUSD_Ticks_2024.csv")
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path


class DukascopyConnectorError(Exception):
    """Raised on parse errors."""


class DukascopyConnector:
    """Reads Dukascopy CSV files and produces normalized bar dicts."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_csv(
        self,
        instrument: str,
        path: str | Path,
        mode: str = "auto",
    ) -> list[dict]:
        """Parse a Dukascopy CSV file into bar dicts.

        Args:
            instrument: Platform symbol e.g. "EUR_USD"
            path:       Path to the CSV file
            mode:       "ohlcv" | "tick" | "auto" (detect from header)

        Returns:
            List of bar dicts with keys:
                instrument_id, timestamp_utc, open, high, low, close, volume, source
        """
        path = Path(path)
        if not path.exists():
            raise DukascopyConnectorError(f"File not found: {path}")

        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return []

            detected_mode = mode if mode != "auto" else _detect_mode(header)

            if detected_mode == "ohlcv":
                return _parse_ohlcv_rows(instrument, reader)
            elif detected_mode == "tick":
                return _parse_tick_rows(instrument, reader)
            else:
                raise DukascopyConnectorError(
                    f"Unrecognised CSV format. Header: {header}"
                )

    def parse_csv_dir(
        self,
        instrument: str,
        directory: str | Path,
        glob_pattern: str = "*.csv",
    ) -> list[dict]:
        """Parse all CSVs in a directory and merge into a single bar list."""
        directory = Path(directory)
        all_bars: list[dict] = []
        for csv_path in sorted(directory.glob(glob_pattern)):
            bars = self.parse_csv(instrument, csv_path)
            all_bars.extend(bars)
        return all_bars


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_mode(header: list[str]) -> str:
    """Detect CSV format from the header row."""
    lower = [h.strip().lower() for h in header]
    if "open" in lower and "high" in lower:
        return "ohlcv"
    if "bid" in lower and "ask" in lower:
        return "tick"
    return "unknown"


def _parse_ohlcv_rows(instrument: str, reader: csv.reader) -> list[dict]:
    """Parse Dukascopy OHLCV minute CSV rows.

    Expected columns (case-insensitive, position-based after header skip):
        Timestamp, Open, High, Low, Close, Volume
    Timestamp format: "DD.MM.YYYY HH:MM:SS.mmm" (Dukascopy default)
    """
    bars: list[dict] = []
    for row in reader:
        if not row or not row[0].strip():
            continue
        try:
            ts = _parse_dukascopy_ts(row[0].strip())
            bars.append({
                "instrument_id": instrument,
                "timestamp_utc": ts,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else 0.0,
                "source": "dukascopy",
            })
        except (ValueError, IndexError) as exc:
            raise DukascopyConnectorError(
                f"Failed to parse row {row!r}: {exc}"
            ) from exc
    return bars


def _parse_tick_rows(instrument: str, reader: csv.reader) -> list[dict]:
    """Parse Dukascopy tick CSV and aggregate to 1-minute bars.

    Expected columns: Timestamp, Bid, Ask, Volume
    Aggregation: open=first mid, high=max mid, low=min mid, close=last mid.
    Timestamp is floored to the minute.
    """
    from collections import defaultdict

    # bucket: minute_ts -> {"open", "high", "low", "close", "volume", "n"}
    buckets: dict[datetime, dict] = defaultdict(lambda: {
        "open": None, "high": -1e18, "low": 1e18, "close": None, "volume": 0.0,
    })

    for row in reader:
        if not row or not row[0].strip():
            continue
        try:
            ts = _parse_dukascopy_ts(row[0].strip())
            bid = float(row[1])
            ask = float(row[2])
            vol = float(row[3]) if len(row) > 3 else 0.0
            mid = (bid + ask) / 2.0

            # Floor to minute
            minute_ts = ts.replace(second=0, microsecond=0)
            b = buckets[minute_ts]
            if b["open"] is None:
                b["open"] = mid
            b["high"] = max(b["high"], mid)
            b["low"] = min(b["low"], mid)
            b["close"] = mid
            b["volume"] += vol
        except (ValueError, IndexError) as exc:
            raise DukascopyConnectorError(
                f"Failed to parse tick row {row!r}: {exc}"
            ) from exc

    bars: list[dict] = []
    for minute_ts, b in sorted(buckets.items()):
        bars.append({
            "instrument_id": instrument,
            "timestamp_utc": minute_ts,
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
            "source": "dukascopy",
        })
    return bars


def _parse_dukascopy_ts(ts_str: str) -> datetime:
    """Parse Dukascopy timestamp to UTC datetime.

    Handles formats:
    - "1641160980000"          Unix milliseconds (dukascopy-node CLI default)
    - "01.01.2024 00:01:00.000"  Dukascopy JForex / tick-converter export
    - "2024-01-01 00:01:00"      ISO-like alternative export format
    """
    ts_str = ts_str.strip()
    # Unix milliseconds: dukascopy-node CLI emits plain integer strings.
    if ts_str.isdigit():
        return datetime.fromtimestamp(int(ts_str) / 1000.0, tz=timezone.utc)
    # Dukascopy JForex format: DD.MM.YYYY HH:MM:SS.mmm
    if "." in ts_str[:3]:
        # Strip sub-second part
        base = ts_str.split(".")[0] if ts_str.count(".") == 3 else ts_str
        # Reconstruct: first two dots are day separators, last is decimal
        parts = ts_str.split(" ")
        date_part = parts[0]  # "01.01.2024"
        time_part = parts[1].split(".")[0] if len(parts) > 1 else "00:00:00"
        day, month, year = date_part.split(".")
        dt = datetime.strptime(f"{year}-{month}-{day} {time_part}", "%Y-%m-%d %H:%M:%S")
    else:
        # ISO-like: strip sub-second
        base = ts_str.split(".")[0]
        dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")

    return dt.replace(tzinfo=timezone.utc)
