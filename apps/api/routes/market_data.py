"""GET /api/market-data/ranges — available date ranges per instrument/timeframe."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_market_repo
from backend.schemas.enums import Timeframe

router = APIRouter()


@router.get("/ranges")
async def get_available_ranges(
    instrument: str | None = None,
    market_repo=Depends(get_market_repo),
):
    """Return the available date range for each instrument and timeframe.

    Query params:
        instrument  — filter to a single instrument (e.g. EUR_USD)

    Returns a list of coverage records: {instrument_id, timeframe, start, end}
    """
    from backend.connectors.instruments import all_specs, get_spec
    from backend.config import settings

    if instrument:
        try:
            get_spec(instrument)  # validates the symbol exists
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown instrument '{instrument}'",
            )
        symbols = [instrument]
    else:
        symbols = settings.default_pairs

    results = []
    for symbol in symbols:
        for tf in Timeframe:
            start, end = market_repo.get_available_range(symbol, tf)
            results.append({
                "instrument_id": symbol,
                "timeframe": tf.value,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "has_data": start is not None,
            })

    return {"ranges": results}


@router.get("/bars")
async def get_bars(
    instrument: str,
    timeframe: str = "M1",
    start: str | None = None,
    end: str | None = None,
    limit: int = 500,
    market_repo=Depends(get_market_repo),
):
    """Return raw bars for an instrument and timeframe.

    Primarily used for debugging and quick data inspection.
    For large datasets, prefer DuckDB direct queries.

    Query params:
        instrument  — required, e.g. EUR_USD
        timeframe   — M1 (default), H1, H4, D
        start       — ISO datetime string (UTC), defaults to earliest available
        end         — ISO datetime string (UTC), defaults to latest available
        limit       — max bars to return (default 500, max 5000)
    """
    from datetime import datetime, timezone

    limit = min(limit, 5000)

    try:
        tf = Timeframe(timeframe.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}'. Must be one of {[t.value for t in Timeframe]}",
        )

    avail_start, avail_end = market_repo.get_available_range(instrument, tf)
    if avail_start is None:
        return {"instrument_id": instrument, "timeframe": tf.value, "bars": [], "count": 0}

    range_start = datetime.fromisoformat(start) if start else avail_start
    range_end = datetime.fromisoformat(end) if end else avail_end

    # Ensure UTC
    if range_start.tzinfo is None:
        range_start = range_start.replace(tzinfo=timezone.utc)
    if range_end.tzinfo is None:
        range_end = range_end.replace(tzinfo=timezone.utc)

    if tf == Timeframe.M1:
        bars = market_repo.get_bars_1m(instrument, range_start, range_end)
    else:
        bars = market_repo.get_bars_agg(instrument, tf, range_start, range_end)

    # Apply limit (tail — return the most recent bars)
    if len(bars) > limit:
        bars = bars[-limit:]

    # Serialize timestamps
    for b in bars:
        if hasattr(b.get("timestamp_utc"), "isoformat"):
            b["timestamp_utc"] = b["timestamp_utc"].isoformat()

    return {
        "instrument_id": instrument,
        "timeframe": tf.value,
        "bars": bars,
        "count": len(bars),
    }
