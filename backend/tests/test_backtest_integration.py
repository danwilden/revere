"""Integration tests for Phase 4 backtest job runner and API endpoints.

Tests:
  - Full backtest job execution via run_backtest_job
  - Strategy resolution (strategy_id vs inline_strategy)
  - Data loading errors (no bars found)
  - API endpoints (404 handling, list behavior)
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta

import pytest

from backend.backtest.costs import CostModel
from backend.data.duckdb_store import DuckDBStore
from backend.data.repositories import LocalArtifactRepository, LocalMetadataRepository
from backend.jobs.backtest import run_backtest_job
from backend.jobs.status import JobManager
from backend.schemas.enums import JobStatus, Timeframe
from backend.schemas.models import Strategy
from backend.strategies.base import BaseStrategy


# ---------------------------------------------------------------------------
# Simple test strategy
# ---------------------------------------------------------------------------

class SimpleEntryExitStrategy(BaseStrategy):
    """Enters on bar 1, exits on bar 3."""

    def should_enter_long(self, bar, features, state):
        return bar.get("_bar_idx") == 1

    def should_enter_short(self, bar, features, state):
        return False

    def should_exit(self, bar, features, position, state):
        return bar.get("_bar_idx") == 3


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestBacktestJobRunner:
    """Test the full backtest job pipeline."""

    def test_full_job_execution_with_strategy_id(self, tmp_path):
        """End-to-end: submit a backtest job with a strategy_id."""
        # Setup
        market_db = str(tmp_path / "market.duckdb")
        metadata_dir = str(tmp_path / "metadata")
        artifacts_dir = str(tmp_path / "artifacts")

        market_repo = DuckDBStore(market_db)
        metadata_repo = LocalMetadataRepository(metadata_dir)
        artifact_repo = LocalArtifactRepository(artifacts_dir)
        job_manager = JobManager(metadata_repo)

        # Insert test bars
        start = datetime(2024, 1, 2, 0, 0)
        bars_data = [
            {
                "instrument_id": "EUR_USD",
                "timeframe": "H1",
                "timestamp_utc": start + timedelta(hours=i),
                "open": 1.1,
                "high": 1.101,
                "low": 1.099,
                "close": 1.1,
                "volume": 100.0,
                "source": "test",
            }
            for i in range(6)
        ]
        market_repo.upsert_bars_agg(bars_data)

        # Save a test strategy - enters on first bar (close > 1.09)
        import uuid
        strategy_id = str(uuid.uuid4())
        strategy_def = {
            "id": strategy_id,
            "name": "test_strategy",
            "strategy_type": "python",
            "definition_json": {
                "code": """
from backend.strategies.base import BaseStrategy
class TestStrat(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.entry_count = 0
    def should_enter_long(self, bar, features, state):
        if self.entry_count == 0 and bar.get("close", 0) > 1.09:
            self.entry_count += 1
            return True
        return False
    def should_enter_short(self, bar, features, state):
        return False
    def should_exit(self, bar, features, position, state):
        # Exit after 2 bars in the position
        return position and position.get("entry_time") and (bar.get("timestamp_utc") > position.get("entry_time"))
""",
                "class_name": "TestStrat",
            },
        }
        metadata_repo.save_strategy(strategy_def)

        # Create and run backtest job
        job = job_manager.create(
            job_type="backtest",
            params={
                "strategy_id": strategy_id,
                "instrument": "EUR_USD",
                "timeframe": "H1",
            },
        )

        run_backtest_job(
            job_id=job.id,
            strategy_id=strategy_id,
            inline_strategy=None,
            instrument="EUR_USD",
            timeframe=Timeframe.H1,
            test_start=start,
            test_end=start + timedelta(hours=6),
            cost_model_params={
                "spread_pips": 2.0,
                "slippage_pips": 0.5,
                "commission_per_unit": 0.0,
            },
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
        )

        # Verify job succeeded
        final_job = job_manager.get(job.id)
        assert final_job is not None
        assert final_job["status"] == JobStatus.SUCCEEDED.value
        assert final_job["result_ref"] is not None

        # Verify backtest run was saved
        backtest_run = metadata_repo.get_backtest_run(final_job["result_ref"])
        assert backtest_run is not None
        assert backtest_run["strategy_id"] == strategy_id

        # Verify metrics were saved (even if no trades, there should be metrics)
        metrics = metadata_repo.get_performance_metrics(final_job["result_ref"])
        assert len(metrics) > 0
        assert any(m["metric_name"] == "total_trades" for m in metrics)

    def test_full_job_execution_with_inline_strategy(self, tmp_path):
        """End-to-end: submit a backtest job with inline_strategy (Python code)."""
        market_db = str(tmp_path / "market.duckdb")
        metadata_dir = str(tmp_path / "metadata")
        artifacts_dir = str(tmp_path / "artifacts")

        market_repo = DuckDBStore(market_db)
        metadata_repo = LocalMetadataRepository(metadata_dir)
        artifact_repo = LocalArtifactRepository(artifacts_dir)
        job_manager = JobManager(metadata_repo)

        # Insert test bars
        start = datetime(2024, 1, 2, 0, 0)
        bars_data = [
            {
                "instrument_id": "EUR_USD",
                "timeframe": "H1",
                "timestamp_utc": start + timedelta(hours=i),
                "open": 1.1,
                "high": 1.101,
                "low": 1.099,
                "close": 1.1,
                "volume": 100.0,
                "source": "test",
            }
            for i in range(6)
        ]
        market_repo.upsert_bars_agg(bars_data)

        job = job_manager.create(
            job_type="backtest",
            params={},
        )

        # Inline Python strategy
        inline_def = {
            "strategy_type": "python",
            "definition_json": {
                "code": """
from backend.strategies.base import BaseStrategy
class InlineStrat(BaseStrategy):
    def should_enter_long(self, bar, features, state):
        return bar.get("_bar_idx") == 1
    def should_enter_short(self, bar, features, state):
        return False
    def should_exit(self, bar, features, position, state):
        return bar.get("_bar_idx") == 3
""",
                "class_name": "InlineStrat",
            },
        }

        run_backtest_job(
            job_id=job.id,
            strategy_id=None,
            inline_strategy=inline_def,
            instrument="EUR_USD",
            timeframe=Timeframe.H1,
            test_start=start,
            test_end=start + timedelta(hours=6),
            cost_model_params={
                "spread_pips": 2.0,
                "slippage_pips": 0.5,
                "commission_per_unit": 0.0,
            },
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
        )

        # Verify job succeeded
        final_job = job_manager.get(job.id)
        assert final_job is not None
        assert final_job["status"] == JobStatus.SUCCEEDED.value

    def test_job_fails_when_no_bars_found(self, tmp_path):
        """Job should fail with clear error when no bars exist for the range."""
        market_db = str(tmp_path / "market.duckdb")
        metadata_dir = str(tmp_path / "metadata")
        artifacts_dir = str(tmp_path / "artifacts")

        market_repo = DuckDBStore(market_db)
        metadata_repo = LocalMetadataRepository(metadata_dir)
        artifact_repo = LocalArtifactRepository(artifacts_dir)
        job_manager = JobManager(metadata_repo)

        # Don't insert any bars
        job = job_manager.create(
            job_type="backtest",
            params={},
        )

        inline_def = {
            "entry_long": {"field": "close", "op": "gt", "value": 1.1},
        }

        with pytest.raises(ValueError, match="No bars found"):
            run_backtest_job(
                job_id=job.id,
                strategy_id=None,
                inline_strategy=inline_def,
                instrument="EUR_USD",
                timeframe=Timeframe.H1,
                test_start=datetime(2024, 1, 1),
                test_end=datetime(2024, 1, 2),
                cost_model_params={"spread_pips": 2.0},
                metadata_repo=metadata_repo,
                market_repo=market_repo,
                artifact_repo=artifact_repo,
                job_manager=job_manager,
            )

        # Verify job was marked as failed
        final_job = job_manager.get(job.id)
        assert final_job is not None
        assert final_job["status"] == JobStatus.FAILED.value
        assert final_job["error_code"] == "BACKTEST_ERROR"

    def test_job_fails_when_strategy_not_found(self, tmp_path):
        """Job should fail when strategy_id references nonexistent strategy."""
        market_db = str(tmp_path / "market.duckdb")
        metadata_dir = str(tmp_path / "metadata")
        artifacts_dir = str(tmp_path / "artifacts")

        market_repo = DuckDBStore(market_db)
        metadata_repo = LocalMetadataRepository(metadata_dir)
        artifact_repo = LocalArtifactRepository(artifacts_dir)
        job_manager = JobManager(metadata_repo)

        # Insert bars
        start = datetime(2024, 1, 2, 0, 0)
        bars_data = [
            {
                "instrument_id": "EUR_USD",
                "timeframe": "H1",
                "timestamp_utc": start + timedelta(hours=i),
                "open": 1.1,
                "high": 1.101,
                "low": 1.099,
                "close": 1.1,
                "volume": 100.0,
                "source": "test",
            }
            for i in range(6)
        ]
        market_repo.upsert_bars_agg(bars_data)

        job = job_manager.create(
            job_type="backtest",
            params={"strategy_id": "nonexistent"},
        )

        with pytest.raises(ValueError, match="Strategy 'nonexistent' not found"):
            run_backtest_job(
                job_id=job.id,
                strategy_id="nonexistent",
                inline_strategy=None,
                instrument="EUR_USD",
                timeframe=Timeframe.H1,
                test_start=start,
                test_end=start + timedelta(hours=6),
                cost_model_params={"spread_pips": 2.0},
                metadata_repo=metadata_repo,
                market_repo=market_repo,
                artifact_repo=artifact_repo,
                job_manager=job_manager,
            )

        # Verify job was marked failed
        final_job = job_manager.get(job.id)
        assert final_job is not None
        assert final_job["status"] == JobStatus.FAILED.value


class TestBacktestAPIMockResponses:
    """Test API endpoint behaviors (404 cases, empty lists, etc)."""

    def test_get_nonexistent_backtest_run_returns_none(self, tmp_path):
        """get_backtest_run should return None for a nonexistent run_id."""
        metadata_dir = str(tmp_path / "metadata")
        metadata_repo = LocalMetadataRepository(metadata_dir)

        result = metadata_repo.get_backtest_run("nonexistent_id")
        assert result is None

    def test_list_backtest_runs_empty_when_none_exist(self, tmp_path):
        """list_backtest_runs should return empty list when no runs exist."""
        metadata_dir = str(tmp_path / "metadata")
        metadata_repo = LocalMetadataRepository(metadata_dir)

        runs = metadata_repo.list_backtest_runs(limit=20)
        assert runs == []

    def test_get_trades_for_nonexistent_run_returns_empty_list(self, tmp_path):
        """get_trades should return empty list when run has no trades."""
        metadata_dir = str(tmp_path / "metadata")
        metadata_repo = LocalMetadataRepository(metadata_dir)

        trades = metadata_repo.get_trades("nonexistent_run_id")
        assert trades == []

    def test_equity_artifact_not_found_for_new_run(self, tmp_path):
        """artifact_repo.exists should return False for a run with no equity artifact."""
        artifacts_dir = str(tmp_path / "artifacts")
        artifact_repo = LocalArtifactRepository(artifacts_dir)

        equity_key = "backtests/nonexistent_run/equity.json"
        assert not artifact_repo.exists(equity_key)

    def test_list_backtest_runs_respects_limit(self, tmp_path):
        """list_backtest_runs should return at most limit runs."""
        metadata_dir = str(tmp_path / "metadata")
        metadata_repo = LocalMetadataRepository(metadata_dir)

        # Save 25 backtest runs
        for i in range(25):
            run_data = {
                "id": f"run_{i:03d}",
                "job_id": f"job_{i:03d}",
                "status": "SUCCEEDED",
                "instrument_id": "EUR_USD",
                "timeframe": "H1",
                "test_start": datetime(2024, 1, 1),
                "test_end": datetime(2024, 1, 2),
            }
            metadata_repo.save_backtest_run(run_data)

        # Fetch with limit 10
        runs = metadata_repo.list_backtest_runs(limit=10)
        assert len(runs) == 10

    def test_get_performance_metrics_empty_when_none_saved(self, tmp_path):
        """get_performance_metrics should return empty list when no metrics exist."""
        metadata_dir = str(tmp_path / "metadata")
        metadata_repo = LocalMetadataRepository(metadata_dir)

        metrics = metadata_repo.get_performance_metrics("nonexistent_run_id")
        assert metrics == []
