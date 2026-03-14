"""OANDA V20 connector — fetches 1-minute bars and normalizes to Bar1m dicts.

Key behaviors (from legacy + OANDA notes in memory):
- client.request(ep) mutates ep in place; response lives on ep.response
- Mid price keys: "o"/"h"/"l"/"c" under candle["mid"]
- candle["complete"] == False means a forming bar — we skip these
- InstrumentsCandlesFactory handles 5000-candle pagination automatically
- Timestamps returned by OANDA are RFC3339 strings; we convert to UTC datetime

Output: list of raw dicts matching the bars_1m table schema (without id,
quality_flag — those are set by normalize.py).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterator

import oandapyV20
from oandapyV20 import API
from oandapyV20.contrib.factories import InstrumentsCandlesFactory
from oandapyV20.exceptions import V20Error

from backend.config import settings


class OandaConnectorError(Exception):
    """Raised on OANDA API errors."""


class OandaConnector:
    """Authenticated OANDA V20 client for fetching historical 1-minute bars.

    Creates a single API connection at construction time. Not thread-safe —
    use one instance per thread/task.
    """

    REQUEST_SLEEP_S: float = 0.1  # polite delay between paginated requests

    def __init__(self) -> None:
        self._api = API(
            access_token=settings.oanda_access_token,
            environment=settings.oanda_environment,  # "practice" | "live"
        )
        self._account_id = settings.oanda_account_id

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_bars_1m(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Fetch complete 1-minute bars for *instrument* in [start, end).

        Args:
            instrument: OANDA symbol e.g. "EUR_USD"
            start:      Inclusive start (UTC-aware datetime)
            end:        Exclusive end (UTC-aware datetime)

        Returns:
            List of dicts with keys:
                instrument_id, timestamp_utc, open, high, low, close, volume, source
            Only complete bars are returned (forming bar at end is excluded).

        Raises:
            OandaConnectorError on API failures.
        """
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)

        params = {
            "granularity": "M1",
            "price": "M",
            "from": _rfc3339(start_utc),
            "to": _rfc3339(end_utc),
        }

        records: list[dict] = []
        n_requests = 0

        try:
            for req in InstrumentsCandlesFactory(instrument, params):
                self._api.request(req)
                n_requests += 1

                for candle in req.response.get("candles", []):
                    # Skip forming (incomplete) bars
                    if not candle.get("complete", True):
                        continue

                    mid = candle.get("mid", candle.get("M", {}))
                    ts = _parse_oanda_ts(candle["time"])

                    records.append({
                        "instrument_id": instrument,
                        "timestamp_utc": ts,
                        "open": float(mid["o"]),
                        "high": float(mid["h"]),
                        "low": float(mid["l"]),
                        "close": float(mid["c"]),
                        "volume": float(candle.get("volume", 0)),
                        "source": "oanda",
                    })

                if n_requests > 1:
                    time.sleep(self.REQUEST_SLEEP_S)

        except V20Error as exc:
            raise OandaConnectorError(str(exc)) from exc

        return records

    def fetch_bars_1m_chunked(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
        chunk_days: int = 30,
    ) -> Iterator[list[dict]]:
        """Yield lists of bars in chunk_days-sized windows.

        Useful for ingestion jobs that want to report progress per chunk
        rather than waiting for the full range to complete.
        """
        from datetime import timedelta

        cursor = _ensure_utc(start)
        end_utc = _ensure_utc(end)

        while cursor < end_utc:
            chunk_end = min(cursor + timedelta(days=chunk_days), end_utc)
            bars = self.fetch_bars_1m(instrument, cursor, chunk_end)
            yield bars
            cursor = chunk_end


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _rfc3339(dt: datetime) -> str:
    """Format datetime as RFC3339 with 'Z' suffix (required by oandapyV20)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_oanda_ts(ts_str: str) -> datetime:
    """Parse OANDA RFC3339 timestamp string to UTC datetime.

    OANDA returns timestamps like '2024-01-02T00:01:00.000000000Z'.
    We strip sub-second precision before parsing.
    """
    # Truncate at seconds boundary then parse
    if "." in ts_str:
        ts_str = ts_str.split(".")[0] + "Z"
    dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)
