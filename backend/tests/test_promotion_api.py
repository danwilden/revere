"""Tests for Phase 5F promotion API routes.

All tests are deterministic with in-memory repositories and mocked background tasks.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.data.repositories import LocalMetadataRepository
from backend.schemas.enums import ExperimentStatus, JobStatus, JobType
from backend.schemas.requests import RobustnessResult
from apps.api.main import app
from backend import deps


# ============================================================================
# Test utilities
# ============================================================================

def setup_app_with_repos(metadata_repo, artifact_repo, job_manager, market_repo):
    """Set up FastAPI app with injected dependencies."""
    app.dependency_overrides[deps.get_metadata_repo] = lambda: metadata_repo
    app.dependency_overrides[deps.get_artifact_repo] = lambda: artifact_repo
    app.dependency_overrides[deps.get_job_manager] = lambda: job_manager
    app.dependency_overrides[deps.get_market_repo] = lambda: market_repo
    return TestClient(app)


def teardown_app():
    """Clear FastAPI dependency overrides."""
    app.dependency_overrides.clear()


def _make_experiment(
    experiment_id="exp-abc",
    status="active",
    best_backtest_run_id="run-123",
    robustness_job_id=None,
    **kwargs
) -> dict:
    """Build a minimal experiment record dict."""
    base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
    return {
        "id": experiment_id,
        "name": "Test Experiment",
        "description": "",
        "instrument": "EUR_USD",
        "timeframe": "H1",
        "test_start": base_dt,
        "test_end": datetime(2024, 12, 31, tzinfo=timezone.utc).isoformat(),
        "model_id": None,
        "feature_run_id": None,
        "status": status,
        "created_at": base_dt,
        "updated_at": base_dt,
        "requested_by": "test",
        "generation_count": 0,
        "best_strategy_id": "strat-123",
        "best_backtest_run_id": best_backtest_run_id,
        "tags": [],
        "robustness_job_id": robustness_job_id,
        "tier": None,
        "discard_reason": None,
        **kwargs,
    }


def _make_passing_robustness_result(
    experiment_id="exp-abc",
    battery_job_id="job-xyz",
) -> RobustnessResult:
    """Build a minimal passing RobustnessResult."""
    base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    from backend.schemas.requests import (
        HoldoutResult, WalkForwardResult, WalkForwardWindow,
        CostStressResult, CostStressVariant, ParamSensitivityResult,
    )
    return RobustnessResult(
        experiment_id=experiment_id,
        battery_job_id=battery_job_id,
        computed_at=base_dt,
        holdout=HoldoutResult(
            backtest_run_id="r1",
            test_start=base_dt,
            test_end=base_dt,
            net_return_pct=5.0,
            sharpe_ratio=0.8,
            max_drawdown_pct=-5.0,
            trade_count=20,
            passed=True,
        ),
        walk_forward=WalkForwardResult(
            windows=[
                WalkForwardWindow(
                    window_index=i,
                    train_start=base_dt,
                    train_end=base_dt,
                    test_start=base_dt,
                    test_end=base_dt,
                    backtest_run_id=f"wf-{i}",
                    net_return_pct=2.0,
                    sharpe_ratio=0.5,
                    trade_count=15,
                    passed=True,
                )
                for i in range(5)
            ],
            windows_passed=4,
            windows_total=5,
            passed=True,
        ),
        cost_stress=CostStressResult(
            variants=[
                CostStressVariant(
                    multiplier=2.0,
                    backtest_run_id="r2",
                    net_return_pct=2.0,
                    sharpe_ratio=0.5,
                    passed=True,
                ),
                CostStressVariant(
                    multiplier=3.0,
                    backtest_run_id="r3",
                    net_return_pct=1.0,
                    sharpe_ratio=0.3,
                    passed=True,
                ),
            ],
            passed=True,
        ),
        param_sensitivity=ParamSensitivityResult(
            steps=[],
            return_range_pct=10.0,
            base_net_return_pct=50.0,
            passed=True,
        ),
        promoted=True,
        block_reasons=[],
    )


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_metadata_repo(tmp_path: Path) -> LocalMetadataRepository:
    """Create a fresh in-memory metadata repository for testing."""
    return LocalMetadataRepository(tmp_path / "test_metadata")


@pytest.fixture
def tmp_artifact_repo(tmp_path: Path):
    """Create a fresh artifact repository."""
    from backend.data.repositories import LocalArtifactRepository
    return LocalArtifactRepository(tmp_path / "artifacts")


@pytest.fixture
def mock_job_manager():
    """Create a mock job manager."""
    return MagicMock()


@pytest.fixture
def mock_market_repo():
    """Create a mock market repository."""
    return MagicMock()


# ============================================================================
# Tests: POST /api/experiments/{id}/promote
# ============================================================================

class TestPromoteExperiment:
    def test_promote_404_when_not_found(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /promote returns 404 when experiment not found."""
        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/nonexistent/promote")
            assert response.status_code == 404
        finally:
            teardown_app()

    def test_promote_409_when_no_best_backtest_run_id(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /promote returns 409 when best_backtest_run_id is None."""
        exp = _make_experiment(best_backtest_run_id=None)
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/promote")
            assert response.status_code == 409
            assert "best_backtest_run_id" in response.json()["detail"]
        finally:
            teardown_app()

    def test_promote_409_when_archived(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /promote returns 409 when status is archived."""
        exp = _make_experiment(status="archived")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/promote")
            assert response.status_code == 409
            assert "archived" in response.json()["detail"]
        finally:
            teardown_app()

    def test_promote_409_when_discarded(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /promote returns 409 when status is discarded."""
        exp = _make_experiment(status="discarded")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/promote")
            assert response.status_code == 409
            assert "discarded" in response.json()["detail"]
        finally:
            teardown_app()

    def test_promote_409_when_battery_already_running(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /promote returns 409 when a battery is already running."""
        exp = _make_experiment(experiment_id="exp-xyz")
        tmp_metadata_repo._upsert("api_experiments", exp)

        # Mock job_manager to report a running battery
        mock_running_job = MagicMock()
        mock_running_job.params = {"experiment_id": "exp-xyz"}
        mock_running_job.status.value = "running"
        mock_job_manager.list.return_value = [mock_running_job]

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/exp-xyz/promote")
            assert response.status_code == 409
            assert "already running" in response.json()["detail"]
        finally:
            teardown_app()

    def test_promote_202_happy_path(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /promote returns 202 with job_id when happy path."""
        from backend.schemas.enums import JobStatus

        exp = _make_experiment(experiment_id="exp-happy")
        tmp_metadata_repo._upsert("api_experiments", exp)

        # Mock job_manager
        mock_job = MagicMock()
        mock_job.id = "job-battery-1"
        mock_job.status = JobStatus.QUEUED
        mock_job_manager.create.return_value = mock_job
        mock_job_manager.list.return_value = []  # No running battery

        try:
            with patch("apps.api.routes.experiments.threading.Thread"):
                client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
                response = client.post("/api/experiments/exp-happy/promote")
                assert response.status_code == 202
                data = response.json()
                assert data["job_id"] == "job-battery-1"
                assert data["status"] == "queued"
        finally:
            teardown_app()


# ============================================================================
# Tests: GET /api/experiments/{id}/robustness
# ============================================================================

class TestGetRobustnessStatus:
    def test_robustness_404_when_experiment_not_found(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """GET /robustness returns 404 when experiment not found."""
        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.get("/api/experiments/nonexistent/robustness")
            assert response.status_code == 404
        finally:
            teardown_app()

    def test_robustness_200_with_null_when_no_battery(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """GET /robustness returns 200 with job_id=null when no battery run."""
        exp = _make_experiment(experiment_id="exp-no-battery")
        tmp_metadata_repo._upsert("api_experiments", exp)
        mock_job_manager.list.return_value = []

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.get("/api/experiments/exp-no-battery/robustness")
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] is None
            assert data["job_status"] is None
            assert data["result"] is None
        finally:
            teardown_app()

    def test_robustness_200_running_status(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """GET /robustness returns 200 with job_status='running' when battery is running."""
        exp = _make_experiment(experiment_id="exp-running")
        tmp_metadata_repo._upsert("api_experiments", exp)

        mock_job = MagicMock()
        mock_job.id = "job-battery-running"
        mock_job.status.value = "running"
        mock_job.progress_pct = 50.0
        mock_job.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_job.params = {"experiment_id": "exp-running"}
        mock_job_manager.list.return_value = [mock_job]

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.get("/api/experiments/exp-running/robustness")
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "job-battery-running"
            assert data["job_status"] == "running"
            assert data["progress_pct"] == 50.0
        finally:
            teardown_app()

    def test_robustness_200_succeeded_with_result(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """GET /robustness returns 200 with result when battery succeeded."""
        exp = _make_experiment(experiment_id="exp-passed")
        tmp_metadata_repo._upsert("api_experiments", exp)

        # Create and persist a robustness result
        result = _make_passing_robustness_result(experiment_id="exp-passed", battery_job_id="job-battery-123")
        artifact_key = f"robustness/exp-passed/battery_job-battery-123.json"
        tmp_artifact_repo.save(
            artifact_key,
            json.dumps(result.model_dump(mode="json"), default=str).encode()
        )

        mock_job = MagicMock()
        mock_job.id = "job-battery-123"
        mock_job.status.value = "succeeded"
        mock_job.progress_pct = 100.0
        mock_job.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_job.params = {"experiment_id": "exp-passed"}
        mock_job_manager.list.return_value = [mock_job]

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.get("/api/experiments/exp-passed/robustness")
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "job-battery-123"
            assert data["job_status"] == "succeeded"
            assert data["result"] is not None
            assert data["result"]["promoted"] is True
        finally:
            teardown_app()

    def test_robustness_200_failed_status(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """GET /robustness returns 200 with job_status='failed' when battery failed."""
        exp = _make_experiment(experiment_id="exp-failed")
        tmp_metadata_repo._upsert("api_experiments", exp)

        mock_job = MagicMock()
        mock_job.id = "job-battery-failed"
        mock_job.status.value = "failed"
        mock_job.progress_pct = 75.0
        mock_job.error_message = "Backtest failed due to missing data"
        mock_job.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_job.params = {"experiment_id": "exp-failed"}
        mock_job_manager.list.return_value = [mock_job]

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.get("/api/experiments/exp-failed/robustness")
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "job-battery-failed"
            assert data["job_status"] == "failed"
            assert data["error_message"] == "Backtest failed due to missing data"
        finally:
            teardown_app()


# ============================================================================
# Tests: POST /api/experiments/{id}/approve
# ============================================================================

class TestApproveExperiment:
    def test_approve_404_when_not_found(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /approve returns 404 when experiment not found."""
        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/nonexistent/approve")
            assert response.status_code == 404
        finally:
            teardown_app()

    def test_approve_409_when_archived(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /approve returns 409 when experiment is archived."""
        exp = _make_experiment(status="archived")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/approve")
            assert response.status_code == 409
            assert "archived" in response.json()["detail"]
        finally:
            teardown_app()

    def test_approve_409_when_discarded(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /approve returns 409 when experiment is discarded."""
        exp = _make_experiment(status="discarded")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/approve")
            assert response.status_code == 409
        finally:
            teardown_app()

    def test_approve_409_when_no_succeeded_battery(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /approve returns 409 when no succeeded battery job exists."""
        exp = _make_experiment(experiment_id="exp-no-success")
        tmp_metadata_repo._upsert("api_experiments", exp)
        mock_job_manager.list.return_value = []

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/exp-no-success/approve")
            assert response.status_code == 409
            assert "No succeeded robustness battery" in response.json()["detail"]
        finally:
            teardown_app()

    def test_approve_409_when_battery_not_promoted(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /approve returns 409 when battery result has promoted=False."""
        exp = _make_experiment(experiment_id="exp-not-promoted")
        tmp_metadata_repo._upsert("api_experiments", exp)

        # Create a failed robustness result
        result = _make_passing_robustness_result(experiment_id="exp-not-promoted")
        result.promoted = False
        result.block_reasons = ["holdout_negative_return"]
        artifact_key = f"robustness/exp-not-promoted/battery_job-failed.json"
        tmp_artifact_repo.save(
            artifact_key,
            json.dumps(result.model_dump(mode="json"), default=str).encode()
        )

        mock_job = MagicMock()
        mock_job.id = "job-failed"
        mock_job.status.value = "succeeded"
        mock_job.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_job.params = {"experiment_id": "exp-not-promoted"}
        mock_job_manager.list.return_value = [mock_job]

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/exp-not-promoted/approve")
            assert response.status_code == 409
            assert "not pass promotion gate" in response.json()["detail"]
        finally:
            teardown_app()

    def test_approve_200_happy_path(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /approve returns 200 and sets status to validated when happy path."""
        exp = _make_experiment(experiment_id="exp-approve-happy")
        tmp_metadata_repo._upsert("api_experiments", exp)

        # Create and persist a passing robustness result
        result = _make_passing_robustness_result(
            experiment_id="exp-approve-happy",
            battery_job_id="job-battery-approved"
        )
        artifact_key = f"robustness/exp-approve-happy/battery_job-battery-approved.json"
        tmp_artifact_repo.save(
            artifact_key,
            json.dumps(result.model_dump(mode="json"), default=str).encode()
        )

        mock_job = MagicMock()
        mock_job.id = "job-battery-approved"
        mock_job.status.value = "succeeded"
        mock_job.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_job.params = {"experiment_id": "exp-approve-happy"}
        mock_job_manager.list.return_value = [mock_job]

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/exp-approve-happy/approve")
            assert response.status_code == 200
            data = response.json()
            assert data["experiment"]["status"] == "validated"
            assert data["experiment"]["tier"] == "validated"
        finally:
            teardown_app()


# ============================================================================
# Tests: POST /api/experiments/{id}/discard
# ============================================================================

class TestDiscardExperiment:
    def test_discard_404_when_not_found(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /discard returns 404 when experiment not found."""
        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/nonexistent/discard", json={"reason": "Not good"})
            assert response.status_code == 404
        finally:
            teardown_app()

    def test_discard_409_when_already_archived(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /discard returns 409 when experiment is already archived."""
        exp = _make_experiment(status="archived")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/discard", json={"reason": "Bad"})
            assert response.status_code == 409
            assert "already" in response.json()["detail"]
        finally:
            teardown_app()

    def test_discard_409_when_already_discarded(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /discard returns 409 when experiment is already discarded."""
        exp = _make_experiment(status="discarded", discard_reason="Previous reason")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(f"/api/experiments/{exp['id']}/discard", json={"reason": "Another reason"})
            assert response.status_code == 409
        finally:
            teardown_app()

    def test_discard_422_when_reason_empty(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /discard returns 422 when reason is empty string."""
        exp = _make_experiment(experiment_id="exp-discard-empty")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post("/api/experiments/exp-discard-empty/discard", json={"reason": ""})
            assert response.status_code == 422
        finally:
            teardown_app()

    def test_discard_200_happy_path(self, tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo):
        """POST /discard returns 200 and sets status to discarded when happy path."""
        exp = _make_experiment(experiment_id="exp-discard-happy")
        tmp_metadata_repo._upsert("api_experiments", exp)

        try:
            client = setup_app_with_repos(tmp_metadata_repo, tmp_artifact_repo, mock_job_manager, mock_market_repo)
            response = client.post(
                "/api/experiments/exp-discard-happy/discard",
                json={"reason": "Strategy failed robustness checks"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["experiment"]["status"] == "discarded"
            assert data["experiment"]["discard_reason"] == "Strategy failed robustness checks"
        finally:
            teardown_app()
