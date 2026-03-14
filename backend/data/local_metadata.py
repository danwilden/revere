"""Local filesystem implementation of MetadataRepository for dev/test use."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.data.repositories import MetadataRepository


class LocalMetadataRepository(MetadataRepository):
    """Stores all metadata as JSON files under a local base directory.

    Each entity type is a separate JSON file (dict of id -> record).
    Simple and sufficient for local development; swap for DynamoDB in cloud.
    """

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, dict] = {}

    def _store(self, name: str) -> dict:
        if name not in self._stores:
            path = self._base / f"{name}.json"
            if path.exists():
                self._stores[name] = json.loads(path.read_text())
            else:
                self._stores[name] = {}
        return self._stores[name]

    def _save(self, name: str) -> None:
        path = self._base / f"{name}.json"
        path.write_text(json.dumps(self._stores[name], indent=2, default=str))

    def _upsert(self, store_name: str, record: dict) -> None:
        store = self._store(store_name)
        store[record["id"]] = record
        self._save(store_name)

    def _get(self, store_name: str, key: str) -> dict | None:
        return self._store(store_name).get(key)

    def _update(self, store_name: str, key: str, updates: dict) -> None:
        store = self._store(store_name)
        if key in store:
            store[key].update(updates)
            self._save(store_name)

    def _list(self, store_name: str) -> list[dict]:
        return list(self._store(store_name).values())

    # Instruments
    def upsert_instrument(self, record: dict) -> None:
        store = self._store("instruments")
        # Index by symbol for easy lookup
        store[record["symbol"]] = record
        self._save("instruments")

    def get_instrument(self, symbol: str) -> dict | None:
        return self._store("instruments").get(symbol)

    def list_instruments(self) -> list[dict]:
        return self._list("instruments")

    # Feature runs
    def save_feature_run(self, record: dict) -> None:
        self._upsert("feature_runs", record)

    def get_feature_run(self, feature_run_id: str) -> dict | None:
        return self._get("feature_runs", feature_run_id)

    # Models
    def save_model(self, record: dict) -> None:
        self._upsert("models", record)

    def update_model(self, model_id: str, updates: dict) -> None:
        self._update("models", model_id, updates)

    def get_model(self, model_id: str) -> dict | None:
        return self._get("models", model_id)

    def list_models(self, model_type: str | None = None) -> list[dict]:
        records = self._list("models")
        if model_type:
            records = [r for r in records if r.get("model_type") == model_type]
        return records

    # Signals
    def save_signal(self, record: dict) -> None:
        self._upsert("signals", record)

    def get_signal(self, signal_id: str) -> dict | None:
        return self._get("signals", signal_id)

    def list_signals(self) -> list[dict]:
        return self._list("signals")

    # Strategies
    def save_strategy(self, record: dict) -> None:
        self._upsert("strategies", record)

    def get_strategy(self, strategy_id: str) -> dict | None:
        return self._get("strategies", strategy_id)

    def list_strategies(self) -> list[dict]:
        return self._list("strategies")

    # Backtest runs
    def save_backtest_run(self, record: dict) -> None:
        self._upsert("backtest_runs", record)

    def update_backtest_run(self, run_id: str, updates: dict) -> None:
        self._update("backtest_runs", run_id, updates)

    def get_backtest_run(self, run_id: str) -> dict | None:
        return self._get("backtest_runs", run_id)

    def save_trades(self, trades: list[dict]) -> None:
        store = self._store("trades")
        for t in trades:
            store[t["id"]] = t
        self._save("trades")

    def get_trades(self, backtest_run_id: str) -> list[dict]:
        return [
            t for t in self._list("trades")
            if t.get("backtest_run_id") == backtest_run_id
        ]

    def save_performance_metrics(self, metrics: list[dict]) -> None:
        store = self._store("performance_metrics")
        for m in metrics:
            store[m["id"]] = m
        self._save("performance_metrics")

    def get_performance_metrics(self, backtest_run_id: str) -> list[dict]:
        return [
            m for m in self._list("performance_metrics")
            if m.get("backtest_run_id") == backtest_run_id
        ]

    def list_backtest_runs(self, limit: int = 50) -> list[dict]:
        records = self._list("backtest_runs")
        records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return records[:limit]

    # Job runs
    def save_job_run(self, record: dict) -> None:
        self._upsert("job_runs", record)

    def update_job_run(self, job_id: str, updates: dict) -> None:
        self._update("job_runs", job_id, updates)

    def get_job_run(self, job_id: str) -> dict | None:
        return self._get("job_runs", job_id)

    def list_job_runs(self, job_type: str | None = None, limit: int = 50) -> list[dict]:
        records = self._list("job_runs")
        if job_type:
            records = [r for r in records if r.get("job_type") == job_type]
        records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return records[:limit]

    # Agent sessions
    def save_agent_session(self, record: dict) -> None:
        self._upsert("agent_sessions", record)

    def update_agent_session(self, session_id: str, updates: dict) -> None:
        self._update("agent_sessions", session_id, updates)

    def get_agent_session(self, session_id: str) -> dict | None:
        return self._get("agent_sessions", session_id)

    def list_agent_sessions(self, limit: int = 20) -> list[dict]:
        records = self._list("agent_sessions")
        records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return records[:limit]
