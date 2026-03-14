"""Bar normalization pipeline.

Takes raw bar dicts from any connector and produces clean, validated bar dicts
ready to be upserted into DuckDB.

Steps applied (in order):
1. Deduplicate by (instrument_id, timestamp_utc) — keep last occurrence
2. Sort by timestamp_utc ascending
3. Validate OHLC consistency: high >= open, high >= close, low <= open, low <= close
4. Set quality_flag per bar

Quality flags (from QualityFlag enum):
  ok            — passed all checks
  ohlc_invalid  — high/low constraints violated
  duplicate     — timestamp appeared more than once (flagged, not dropped)
"""
from __future__ import annotations

from datetime import datetime


def normalize_bars(rows: list[dict]) -> list[dict]:
    """Normalize a list of raw bar dicts.

    Args:
        rows: Raw dicts from a connector. Must have keys:
              instrument_id, timestamp_utc, open, high, low, close, volume, source

    Returns:
        Deduplicated, sorted, validated list with quality_flag set.
    """
    if not rows:
        return []

    # 1. Track duplicates before dedup
    seen: dict[tuple, int] = {}  # (instrument_id, ts) -> count
    for r in rows:
        key = (_instrument_key(r), _ts_key(r["timestamp_utc"]))
        seen[key] = seen.get(key, 0) + 1

    duplicate_keys = {k for k, count in seen.items() if count > 1}

    # 2. Deduplicate — keep last occurrence per key
    deduped: dict[tuple, dict] = {}
    for r in rows:
        key = (_instrument_key(r), _ts_key(r["timestamp_utc"]))
        deduped[key] = r

    # 3. Sort by timestamp
    sorted_rows = sorted(deduped.values(), key=lambda r: _ts_key(r["timestamp_utc"]))

    # 4. Validate OHLC and assign quality_flag
    result: list[dict] = []
    for r in sorted_rows:
        row = dict(r)
        key = (_instrument_key(row), _ts_key(row["timestamp_utc"]))

        if key in duplicate_keys:
            row["quality_flag"] = "duplicate"
        elif not _ohlc_valid(row):
            row["quality_flag"] = "ohlc_invalid"
        else:
            row.setdefault("quality_flag", "ok")

        result.append(row)

    return result


def _ohlc_valid(row: dict) -> bool:
    """Return True if OHLC prices satisfy: high >= o/c >= low, high >= low."""
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    return (
        h >= o
        and h >= c
        and l <= o
        and l <= c
        and h >= l
    )


def _instrument_key(row: dict) -> str:
    return row.get("instrument_id", "")


def _ts_key(ts) -> datetime:
    """Normalize timestamp to datetime for consistent dict keying."""
    if isinstance(ts, datetime):
        return ts
    # Handle string timestamps
    if isinstance(ts, str):
        ts = ts.rstrip("Z").split(".")[0]
        return datetime.fromisoformat(ts)
    return ts
