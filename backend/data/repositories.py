"""Abstract repository interfaces for all storage layers.

Three distinct storage categories:
- MarketDataRepository: time-series bars, features, regime labels (DuckDB locally)
- MetadataRepository: strategies, signals, models, job runs (SQLite locally, DynamoDB in cloud)
- ArtifactRepository: model files, backtest exports, raw datasets (filesystem locally, S3 in cloud)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from backend.schemas.enums import Timeframe


# ---------------------------------------------------------------------------
# Market data repository (time-series)
# ---------------------------------------------------------------------------

class MarketDataRepository(ABC):
    @abstractmethod
    def upsert_bars_1m(self, rows: list[dict]) -> int:
        """Insert or replace 1-minute bars. Returns count inserted."""

    @abstractmethod
    def upsert_bars_agg(self, rows: list[dict]) -> int:
        """Insert or replace aggregated bars."""

    @abstractmethod
    def get_bars_1m(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Return 1-minute bars in [start, end) as list of dicts."""

    @abstractmethod
    def get_bars_agg(
        self,
        instrument_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Return aggregated bars in [start, end)."""

    @abstractmethod
    def get_available_range(
        self,
        instrument_id: str,
        timeframe: Timeframe,
    ) -> tuple[datetime | None, datetime | None]:
        """Return (min_ts, max_ts) of stored bars, or (None, None) if empty."""

    @abstractmethod
    def upsert_features(self, rows: list[dict]) -> int:
        """Insert or replace computed feature rows."""

    @abstractmethod
    def get_features(
        self,
        instrument_id: str,
        timeframe: Timeframe,
        feature_run_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Return feature rows for the given run in [start, end)."""

    @abstractmethod
    def upsert_regime_labels(self, rows: list[dict]) -> int:
        """Insert or replace regime label rows."""

    @abstractmethod
    def get_regime_labels(
        self,
        model_id: str,
        instrument_id: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Return regime label rows in [start, end)."""


# ---------------------------------------------------------------------------
# Metadata repository (application entities)
# ---------------------------------------------------------------------------

class MetadataRepository(ABC):
    # --- Instruments ---
    @abstractmethod
    def upsert_instrument(self, record: dict) -> None: ...

    @abstractmethod
    def get_instrument(self, symbol: str) -> dict | None: ...

    @abstractmethod
    def list_instruments(self) -> list[dict]: ...

    # --- Feature runs ---
    @abstractmethod
    def save_feature_run(self, record: dict) -> None: ...

    @abstractmethod
    def get_feature_run(self, feature_run_id: str) -> dict | None: ...

    # --- Models ---
    @abstractmethod
    def save_model(self, record: dict) -> None: ...

    @abstractmethod
    def update_model(self, model_id: str, updates: dict) -> None: ...

    @abstractmethod
    def get_model(self, model_id: str) -> dict | None: ...

    @abstractmethod
    def list_models(self, model_type: str | None = None) -> list[dict]: ...

    # --- Signals ---
    @abstractmethod
    def save_signal(self, record: dict) -> None: ...

    @abstractmethod
    def get_signal(self, signal_id: str) -> dict | None: ...

    @abstractmethod
    def list_signals(self) -> list[dict]: ...

    # --- Strategies ---
    @abstractmethod
    def save_strategy(self, record: dict) -> None: ...

    @abstractmethod
    def get_strategy(self, strategy_id: str) -> dict | None: ...

    @abstractmethod
    def list_strategies(self) -> list[dict]: ...

    # --- Backtest runs ---
    @abstractmethod
    def save_backtest_run(self, record: dict) -> None: ...

    @abstractmethod
    def update_backtest_run(self, run_id: str, updates: dict) -> None: ...

    @abstractmethod
    def get_backtest_run(self, run_id: str) -> dict | None: ...

    @abstractmethod
    def save_trades(self, trades: list[dict]) -> None: ...

    @abstractmethod
    def get_trades(self, backtest_run_id: str) -> list[dict]: ...

    @abstractmethod
    def save_performance_metrics(self, metrics: list[dict]) -> None: ...

    @abstractmethod
    def get_performance_metrics(self, backtest_run_id: str) -> list[dict]: ...

    @abstractmethod
    def list_backtest_runs(self, limit: int = 50) -> list[dict]: ...

    # --- Job runs ---
    @abstractmethod
    def save_job_run(self, record: dict) -> None: ...

    @abstractmethod
    def update_job_run(self, job_id: str, updates: dict) -> None: ...

    @abstractmethod
    def get_job_run(self, job_id: str) -> dict | None: ...

    @abstractmethod
    def list_job_runs(self, job_type: str | None = None, limit: int = 50) -> list[dict]: ...

    # --- Agent sessions ---
    @abstractmethod
    def save_agent_session(self, record: dict) -> None: ...

    @abstractmethod
    def update_agent_session(self, session_id: str, updates: dict) -> None: ...

    @abstractmethod
    def get_agent_session(self, session_id: str) -> dict | None: ...

    @abstractmethod
    def list_agent_sessions(self, limit: int = 20) -> list[dict]: ...


# ---------------------------------------------------------------------------
# Artifact repository (files)
# ---------------------------------------------------------------------------

class ArtifactRepository(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes) -> str:
        """Persist bytes under key, return a reference URI."""

    @abstractmethod
    def load(self, ref: str) -> bytes:
        """Load bytes from a reference URI."""

    @abstractmethod
    def exists(self, ref: str) -> bool: ...


# ---------------------------------------------------------------------------
# Backwards-compatible re-exports (circular import safeguard)
# ---------------------------------------------------------------------------

def __getattr__(name: str):
    """Lazy-load implementations to avoid circular imports."""
    if name == "LocalMetadataRepository":
        from backend.data.local_metadata import LocalMetadataRepository as _LocalMetadataRepository
        return _LocalMetadataRepository
    elif name == "LocalArtifactRepository":
        from backend.data.local_artifacts import LocalArtifactRepository as _LocalArtifactRepository
        return _LocalArtifactRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MarketDataRepository",
    "MetadataRepository",
    "ArtifactRepository",
    "LocalArtifactRepository",
    "LocalMetadataRepository",
]
