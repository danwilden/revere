"""Bar aggregation — derive higher timeframes from 1-minute base data.

Supported output timeframes: H1, H4, D (derived from M1 bars in DuckDB).

Aggregation rules:
  open  = first bar's open in the window
  high  = max of highs
  low   = min of lows
  close = last bar's close in the window
  volume = sum of volumes

Session boundaries:
  H1 — aligned to clock hour (00:00, 01:00, ...)
  H4 — aligned to 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC
  D  — aligned to 00:00 UTC (forex day)

Usage:
    from backend.data.aggregate import aggregate_bars
    bars_h1 = aggregate_bars(bars_1m, "H1", source="oanda")
"""
from __future__ import annotations

from datetime import datetime, timezone


_SUPPORTED_TIMEFRAMES = {"H1", "H4", "D"}


def aggregate_bars(
    bars_1m: list[dict],
    timeframe: str,
    source: str = "derived",
    derivation_version: str = "1",
) -> list[dict]:
    """Aggregate 1-minute bars into a higher timeframe.

    Args:
        bars_1m:            Sorted list of 1m bar dicts (from DuckDB or connector).
                            Must have: instrument_id, timestamp_utc, open, high,
                            low, close, volume.
        timeframe:          "H1", "H4", or "D"
        source:             Propagated to output bars.
        derivation_version: Opaque version tag stored in bars_agg.

    Returns:
        List of aggregated bar dicts for bars_agg, sorted by timestamp_utc.
        Each dict has: instrument_id, timeframe, timestamp_utc, open, high,
        low, close, volume, source, derivation_version.

    Raises:
        ValueError if timeframe is not supported or bars_1m is empty.
    """
    if not bars_1m:
        return []

    tf = timeframe.upper()
    if tf not in _SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. Must be one of {_SUPPORTED_TIMEFRAMES}"
        )

    instrument_id = bars_1m[0]["instrument_id"]
    _floor = _make_floor_fn(tf)

    # Group bars by their window start
    buckets: dict[datetime, list[dict]] = {}
    for bar in bars_1m:
        ts = _coerce_ts(bar["timestamp_utc"])
        window_start = _floor(ts)
        if window_start not in buckets:
            buckets[window_start] = []
        buckets[window_start].append(bar)

    result: list[dict] = []
    for window_start in sorted(buckets):
        group = buckets[window_start]
        agg_bar = {
            "instrument_id": instrument_id,
            "timeframe": tf,
            "timestamp_utc": window_start,
            "open": group[0]["open"],
            "high": max(b["high"] for b in group),
            "low": min(b["low"] for b in group),
            "close": group[-1]["close"],
            "volume": sum(b.get("volume", 0.0) for b in group),
            "source": source,
            "derivation_version": derivation_version,
        }
        result.append(agg_bar)

    return result


# ---------------------------------------------------------------------------
# Floor functions for each timeframe
# ---------------------------------------------------------------------------

def _make_floor_fn(timeframe: str):
    if timeframe == "H1":
        return _floor_h1
    if timeframe == "H4":
        return _floor_h4
    if timeframe == "D":
        return _floor_d
    raise ValueError(f"No floor function for {timeframe}")


def _floor_h1(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _floor_h4(dt: datetime) -> datetime:
    hour_block = (dt.hour // 4) * 4
    return dt.replace(hour=hour_block, minute=0, second=0, microsecond=0)


def _floor_d(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _coerce_ts(ts) -> datetime:
    """Ensure timestamp is a timezone-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    if isinstance(ts, str):
        ts = ts.rstrip("Z").split(".")[0]
        dt = datetime.fromisoformat(ts)
        return dt.replace(tzinfo=timezone.utc)
    raise TypeError(f"Cannot coerce {type(ts)} to datetime")
