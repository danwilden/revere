"""Phase 0 foundation tests: schemas, DuckDB store, job model."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pytest

from backend.schemas.enums import DataSource, JobStatus, JobType, Timeframe
from backend.schemas.models import Bar1m, Instrument, JobRun, Strategy


class TestSchemas:
    def test_instrument_defaults(self):
        inst = Instrument(
            symbol="EUR_USD",
            base_currency="EUR",
            quote_currency="USD",
            category="major",
            pip_size=0.0001,
            price_precision=5,
        )
        assert inst.active_flag is True
        assert inst.id != ""

    def test_bar1m_creation(self):
        bar = Bar1m(
            instrument_id="instr-1",
            timestamp_utc=datetime(2024, 1, 1, 12, 0),
            open=1.1000,
            high=1.1010,
            low=1.0990,
            close=1.1005,
            source=DataSource.OANDA,
        )
        assert bar.quality_flag.value == "ok"
        assert bar.volume == 0.0

    def test_job_run_defaults(self):
        job = JobRun(job_type=JobType.INGESTION)
        assert job.status == JobStatus.QUEUED
        assert job.progress_pct == 0.0

    def test_strategy_creation(self):
        strat = Strategy(
            name="breakout_v1",
            strategy_type="rules_engine",
            definition_json={"entry_long": {"all": []}},
        )
        assert strat.active_flag is True
        assert strat.version == 1


class TestDuckDBStore:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        from backend.data.duckdb_store import DuckDBStore
        self.store = DuckDBStore(os.path.join(self._tmpdir, "test.duckdb"))

    def teardown_method(self):
        self.store.close()

    def test_tables_created(self):
        tables = {
            r[0] for r in self.store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "bars_1m" in tables
        assert "bars_agg" in tables
        assert "features" in tables
        assert "regime_labels" in tables

    def test_upsert_and_get_bars_1m(self):
        rows = [
            {
                "instrument_id": "EUR_USD",
                "timestamp_utc": "2024-01-01 12:00:00",
                "open": 1.1000,
                "high": 1.1010,
                "low": 1.0990,
                "close": 1.1005,
                "volume": 100.0,
                "source": "oanda",
                "quality_flag": "ok",
            }
        ]
        count = self.store.upsert_bars_1m(rows)
        assert count == 1

        result = self.store.get_bars_1m(
            "EUR_USD",
            datetime(2024, 1, 1),
            datetime(2024, 1, 2),
        )
        assert len(result) == 1
        assert abs(result[0]["close"] - 1.1005) < 1e-6

    def test_available_range_empty(self):
        lo, hi = self.store.get_available_range("EUR_USD", Timeframe.M1)
        assert lo is None
        assert hi is None

    def test_available_range_after_insert(self):
        self.store.upsert_bars_1m([
            {
                "instrument_id": "EUR_USD",
                "timestamp_utc": "2024-01-15 08:00:00",
                "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105,
                "volume": 0, "source": "oanda", "quality_flag": "ok",
            }
        ])
        lo, hi = self.store.get_available_range("EUR_USD", Timeframe.M1)
        assert lo is not None
        assert hi is not None


class TestJobManager:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        from backend.data.repositories import LocalMetadataRepository
        from backend.jobs.status import JobManager
        repo = LocalMetadataRepository(self._tmpdir)
        self.mgr = JobManager(repo)

    def test_create_queued(self):
        job = self.mgr.create(JobType.INGESTION, {"pairs": ["EUR_USD"]})
        assert job.status == JobStatus.QUEUED
        assert job.params_json["pairs"] == ["EUR_USD"]

    def test_transitions(self):
        job = self.mgr.create(JobType.HMM_TRAINING)

        self.mgr.start(job.id, "initializing")
        rec = self.mgr.get(job.id)
        assert rec["status"] == "running"
        assert rec["stage_label"] == "initializing"

        self.mgr.progress(job.id, 50.0, "training")
        rec = self.mgr.get(job.id)
        assert rec["progress_pct"] == 50.0

        self.mgr.succeed(job.id, result_ref="models/hmm-1.pkl")
        rec = self.mgr.get(job.id)
        assert rec["status"] == "succeeded"
        assert rec["result_ref"] == "models/hmm-1.pkl"
        assert rec["progress_pct"] == 100.0

    def test_fail_transition(self):
        job = self.mgr.create(JobType.BACKTEST)
        self.mgr.start(job.id)
        self.mgr.fail(job.id, "Out of memory", "OOM_ERROR")
        rec = self.mgr.get(job.id)
        assert rec["status"] == "failed"
        assert rec["error_code"] == "OOM_ERROR"

    def test_list_by_type(self):
        self.mgr.create(JobType.INGESTION)
        self.mgr.create(JobType.INGESTION)
        self.mgr.create(JobType.HMM_TRAINING)

        ingestion_jobs = self.mgr.list(job_type="ingestion")
        assert len(ingestion_jobs) == 2

        all_jobs = self.mgr.list()
        assert len(all_jobs) == 3
