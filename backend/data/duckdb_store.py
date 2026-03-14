"""DuckDB-backed MarketDataRepository for local development.

Stores all time-series data (1-minute bars, aggregated bars, features,
regime labels) in a single DuckDB file. Designed for efficient range-scan
queries needed by feature computation and backtesting.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from backend.data.repositories import MarketDataRepository
from backend.schemas.enums import Timeframe

_DDL = """
CREATE TABLE IF NOT EXISTS bars_1m (
    instrument_id TEXT NOT NULL,
    timestamp_utc TIMESTAMP NOT NULL,
    open         DOUBLE NOT NULL,
    high         DOUBLE NOT NULL,
    low          DOUBLE NOT NULL,
    close        DOUBLE NOT NULL,
    volume       DOUBLE DEFAULT 0,
    source       TEXT NOT NULL,
    quality_flag TEXT DEFAULT 'ok',
    PRIMARY KEY (instrument_id, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS bars_agg (
    instrument_id     TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    timestamp_utc     TIMESTAMP NOT NULL,
    open              DOUBLE NOT NULL,
    high              DOUBLE NOT NULL,
    low               DOUBLE NOT NULL,
    close             DOUBLE NOT NULL,
    volume            DOUBLE DEFAULT 0,
    source            TEXT NOT NULL,
    derivation_version TEXT DEFAULT '1',
    PRIMARY KEY (instrument_id, timeframe, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS feature_runs (
    id               TEXT PRIMARY KEY,
    feature_set_name TEXT NOT NULL,
    code_version     TEXT NOT NULL,
    parameters_json  TEXT DEFAULT '{}',
    start_date       TIMESTAMP,
    end_date         TIMESTAMP,
    created_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS features (
    instrument_id  TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    timestamp_utc  TIMESTAMP NOT NULL,
    feature_run_id TEXT NOT NULL,
    feature_name   TEXT NOT NULL,
    feature_value  DOUBLE,
    PRIMARY KEY (instrument_id, timeframe, timestamp_utc, feature_run_id, feature_name)
);

CREATE TABLE IF NOT EXISTS regime_labels (
    model_id                  TEXT NOT NULL,
    instrument_id             TEXT NOT NULL,
    timeframe                 TEXT NOT NULL,
    timestamp_utc             TIMESTAMP NOT NULL,
    state_id                  INTEGER,
    regime_label              TEXT,
    state_probabilities_json  TEXT DEFAULT '{}',
    PRIMARY KEY (model_id, instrument_id, timeframe, timestamp_utc)
);
"""


class DuckDBStore(MarketDataRepository):
    """Thread-safe DuckDB market data store.

    Each instance manages one connection. For the FastAPI server use a single
    shared instance (thread-local connections are handled by DuckDB internally).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._conn = duckdb.connect(self._path)
        self._conn.execute(_DDL)

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    # ------------------------------------------------------------------
    # 1-minute bars
    # ------------------------------------------------------------------

    def upsert_bars_1m(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        self._conn.execute("""
            INSERT OR REPLACE INTO bars_1m
                (instrument_id, timestamp_utc, open, high, low, close, volume, source, quality_flag)
            SELECT
                instrument_id, timestamp_utc::TIMESTAMP, open, high, low, close,
                COALESCE(volume, 0), source, COALESCE(quality_flag, 'ok')
            FROM (VALUES {placeholders}) AS t
                (instrument_id, timestamp_utc, open, high, low, close, volume, source, quality_flag)
        """.format(
            placeholders=",".join(
                f"('{r['instrument_id']}','{r['timestamp_utc']}',{r['open']},{r['high']},"
                f"{r['low']},{r['close']},{r.get('volume',0)},'{r['source']}','{r.get('quality_flag','ok')}')"
                for r in rows
            )
        ))
        return len(rows)

    def get_bars_1m(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        result = self._conn.execute(
            """
            SELECT instrument_id, timestamp_utc, open, high, low, close, volume, source, quality_flag
            FROM bars_1m
            WHERE instrument_id = ? AND timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc
            """,
            [instrument_id, start, end],
        ).fetchall()
        cols = ["instrument_id", "timestamp_utc", "open", "high", "low", "close", "volume", "source", "quality_flag"]
        return [dict(zip(cols, row)) for row in result]

    # ------------------------------------------------------------------
    # Aggregated bars
    # ------------------------------------------------------------------

    def upsert_bars_agg(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        for r in rows:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO bars_agg
                    (instrument_id, timeframe, timestamp_utc, open, high, low, close,
                     volume, source, derivation_version)
                VALUES (?, ?, ?::TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    r["instrument_id"], r["timeframe"], r["timestamp_utc"],
                    r["open"], r["high"], r["low"], r["close"],
                    r.get("volume", 0), r["source"], r.get("derivation_version", "1"),
                ],
            )
        return len(rows)

    def get_bars_agg(
        self,
        instrument_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        result = self._conn.execute(
            """
            SELECT instrument_id, timeframe, timestamp_utc, open, high, low, close,
                   volume, source, derivation_version
            FROM bars_agg
            WHERE instrument_id = ? AND timeframe = ?
              AND timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc
            """,
            [instrument_id, timeframe.value, start, end],
        ).fetchall()
        cols = ["instrument_id", "timeframe", "timestamp_utc", "open", "high", "low",
                "close", "volume", "source", "derivation_version"]
        return [dict(zip(cols, row)) for row in result]

    def get_available_range(
        self,
        instrument_id: str,
        timeframe: Timeframe,
    ) -> tuple[datetime | None, datetime | None]:
        if timeframe == Timeframe.M1:
            row = self._conn.execute(
                "SELECT MIN(timestamp_utc), MAX(timestamp_utc) FROM bars_1m WHERE instrument_id = ?",
                [instrument_id],
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT MIN(timestamp_utc), MAX(timestamp_utc)
                FROM bars_agg
                WHERE instrument_id = ? AND timeframe = ?
                """,
                [instrument_id, timeframe.value],
            ).fetchone()
        if row and row[0]:
            return row[0], row[1]
        return None, None

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    def upsert_features(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        for r in rows:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO features
                    (instrument_id, timeframe, timestamp_utc, feature_run_id, feature_name, feature_value)
                VALUES (?, ?, ?::TIMESTAMP, ?, ?, ?)
                """,
                [r["instrument_id"], r["timeframe"], r["timestamp_utc"],
                 r["feature_run_id"], r["feature_name"], r.get("feature_value")],
            )
        return len(rows)

    def get_features(
        self,
        instrument_id: str,
        timeframe: Timeframe,
        feature_run_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        result = self._conn.execute(
            """
            SELECT instrument_id, timeframe, timestamp_utc, feature_run_id, feature_name, feature_value
            FROM features
            WHERE instrument_id = ? AND timeframe = ? AND feature_run_id = ?
              AND timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc, feature_name
            """,
            [instrument_id, timeframe.value, feature_run_id, start, end],
        ).fetchall()
        cols = ["instrument_id", "timeframe", "timestamp_utc", "feature_run_id", "feature_name", "feature_value"]
        return [dict(zip(cols, row)) for row in result]

    # ------------------------------------------------------------------
    # Regime labels
    # ------------------------------------------------------------------

    def upsert_regime_labels(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        for r in rows:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO regime_labels
                    (model_id, instrument_id, timeframe, timestamp_utc,
                     state_id, regime_label, state_probabilities_json)
                VALUES (?, ?, ?, ?::TIMESTAMP, ?, ?, ?)
                """,
                [
                    r["model_id"], r["instrument_id"], r["timeframe"],
                    r["timestamp_utc"], r.get("state_id"),
                    r.get("regime_label", ""),
                    r.get("state_probabilities_json", "{}"),
                ],
            )
        return len(rows)

    def get_regime_labels(
        self,
        model_id: str,
        instrument_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        result = self._conn.execute(
            """
            SELECT model_id, instrument_id, timeframe, timestamp_utc,
                   state_id, regime_label, state_probabilities_json
            FROM regime_labels
            WHERE model_id = ? AND instrument_id = ? AND timeframe = ?
              AND timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc
            """,
            [model_id, instrument_id, timeframe.value, start, end],
        ).fetchall()
        cols = ["model_id", "instrument_id", "timeframe", "timestamp_utc",
                "state_id", "regime_label", "state_probabilities_json"]
        return [dict(zip(cols, row)) for row in result]

    def close(self) -> None:
        self._conn.close()
