"""
OANDA candle data fetcher with parquet caching.

Output DataFrame contract (always):
    Index:   DatetimeIndex (UTC, name="time")
    Columns: open, high, low, close  (float64, mid prices)
             volume                  (int64, tick count)
             complete                (bool, bar is closed)

Usage:
    from forex_system.data.candles import CandleFetcher
    fetcher = CandleFetcher()
    df = fetcher.fetch("EUR_USD", "H1", start="2020-01-01", end="2024-01-01")
    df = df[df["complete"]]  # filter incomplete bars before use
"""

import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger
from oandapyV20.contrib.factories import InstrumentsCandlesFactory

from forex_system.config import settings
from forex_system.data.oanda_client import client


class CandleFetcher:
    """
    Fetches OHLCV candles from OANDA, paginating automatically via
    InstrumentsCandlesFactory (handles 5000-candle-per-request limit).

    Results are cached to data/raw/ as parquet files. Set use_cache=False
    or force_refresh=True to bypass the cache.
    """

    # Seconds to sleep between API requests during bulk pulls
    REQUEST_SLEEP_S: float = 0.1

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or settings.data_raw
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    def fetch(
        self,
        instrument: str,
        granularity: str,
        start: str | datetime,
        end: str | datetime | None = None,
        price: str = "M",           # M=mid, B=bid, A=ask
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch candles for a single instrument/granularity window.

        Args:
            instrument: e.g. "EUR_USD"
            granularity: e.g. "H1", "H4", "D"
            start: start datetime (inclusive)
            end: end datetime (exclusive); defaults to now
            price: "M" for mid prices (default)
            use_cache: load from parquet cache if available
            force_refresh: re-fetch from API even if cache exists

        Returns:
            DataFrame with index=DatetimeIndex(UTC) and OHLCV columns.
        """
        cache_path = self._cache_path(instrument, granularity, start, end)

        if use_cache and not force_refresh and cache_path.exists():
            logger.info(f"Cache hit: {cache_path.name}")
            return pd.read_parquet(cache_path)

        df = self._fetch_from_api(instrument, granularity, start, end, price)

        if use_cache and not df.empty:
            df.to_parquet(cache_path)
            logger.info(
                f"Cached {len(df)} {granularity} candles → {cache_path.name}"
            )

        return df

    def fetch_all_pairs(
        self,
        granularity: str,
        start: str,
        end: str | None = None,
        instruments: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch all major pairs for a given granularity.

        Returns:
            {instrument: DataFrame} dict
        """
        pairs = instruments or settings.major_pairs
        result: dict[str, pd.DataFrame] = {}
        for pair in pairs:
            logger.info(f"Fetching {pair} {granularity} ...")
            result[pair] = self.fetch(
                pair, granularity, start, end, use_cache=use_cache
            )
        return result

    # ── Private helpers ──────────────────────────────────────────────────

    def _fetch_from_api(
        self,
        instrument: str,
        granularity: str,
        start: str | datetime,
        end: str | datetime | None,
        price: str,
    ) -> pd.DataFrame:
        from_ts = pd.Timestamp(start, tz="UTC")
        to_ts = (
            pd.Timestamp(end, tz="UTC")
            if end
            else pd.Timestamp.utcnow()
        )
        # oandapyV20 InstrumentsCandlesFactory expects RFC3339 with 'Z' suffix, not +00:00
        _rfc3339_z = lambda ts: ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "granularity": granularity,
            "price": price,
            "from": _rfc3339_z(from_ts),
            "to": _rfc3339_z(to_ts),
        }

        records: list[dict] = []
        n_requests = 0

        for req in InstrumentsCandlesFactory(instrument, params):
            resp = client.request(req)
            n_requests += 1

            for candle in resp.get("candles", []):
                # Mid price dict uses single-char keys: "o", "h", "l", "c"
                mid = candle.get("mid", candle.get("M", {}))
                records.append(
                    {
                        "time": candle["time"],
                        "open": float(mid["o"]),
                        "high": float(mid["h"]),
                        "low": float(mid["l"]),
                        "close": float(mid["c"]),
                        "volume": int(candle["volume"]),
                        "complete": bool(candle["complete"]),
                    }
                )

            if n_requests > 1:
                time.sleep(self.REQUEST_SLEEP_S)

        logger.info(
            f"{instrument} {granularity}: {len(records)} candles "
            f"via {n_requests} requests"
        )

        if not records:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume", "complete"]
            )

        df = pd.DataFrame(records)
        # RFC3339 nanosecond timestamps → UTC DatetimeIndex
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time").sort_index()
        df[["open", "high", "low", "close"]] = df[
            ["open", "high", "low", "close"]
        ].astype("float64")
        df["volume"] = df["volume"].astype("int64")
        return df

    def _cache_path(
        self,
        instrument: str,
        granularity: str,
        start: str | datetime,
        end: str | datetime | None,
    ) -> Path:
        start_str = str(start)[:10].replace(":", "")
        end_str = (str(end)[:10].replace(":", "") if end else "now")
        fname = f"{instrument}_{granularity}_{start_str}_{end_str}.parquet"
        return self.cache_dir / fname
