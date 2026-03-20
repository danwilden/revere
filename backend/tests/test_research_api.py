"""Tests for Phase 5B research API routes.

All tests are deterministic with mocked graph execution and in-memory repositories.
No real LangGraph execution or AWS calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend.data.repositories import LocalMetadataRepository
from backend.lab.experiment_registry import (
    ExperimentRegistry,
    ExperimentStatus,
)
from backend.schemas.requests import ResearchRunRequest
from apps.api.main import app
from backend import deps


# ============================================================================
# Test Context Manager for Dependency Injection
# ============================================================================

def setup_app_with_registry(registry: ExperimentRegistry) -> TestClient:
    """Set up FastAPI app with a specific registry."""
    from backend.deps import get_experiment_registry
    app.dependency_overrides[get_experiment_registry] = lambda: registry
    return TestClient(app)


def teardown_app():
    """Clear FastAPI dependency overrides."""
    app.dependency_overrides.clear()


# ============================================================================
# POST /api/research/run Tests
# ============================================================================

class TestPostResearchRun:
    """test_post_research_run_returns_202"""
    def test_post_research_run_returns_202(self, tmp_path: Path):
        """POST /api/research/run should return 202 with experiment_id and status."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_1")
        test_registry = ExperimentRegistry(tmp_repo)

        try:
            # Patch both the dependency and the background task runner
            with patch("apps.api.routes.research._run_research_graph"):
                client = setup_app_with_registry(test_registry)
                payload = {
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "test_start": "2023-01-01",
                    "test_end": "2024-01-01",
                    "task": "generate_seed",
                    "requested_by": "test_user",
                }
                response = client.post("/api/research/run", json=payload)

                assert response.status_code == 202
                data = response.json()
                assert "experiment_id" in data
                assert "session_id" in data
                assert "status" in data
                assert data["status"] == "pending"
        finally:
            teardown_app()

    def test_post_research_run_mutate_requires_parent(self, tmp_path: Path):
        """POST with task='mutate' and no parent_experiment_id should return 422."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_2")
        test_registry = ExperimentRegistry(tmp_repo)

        try:
            client = setup_app_with_registry(test_registry)
            payload = {
                "instrument": "EUR_USD",
                "timeframe": "H4",
                "test_start": "2023-01-01",
                "test_end": "2024-01-01",
                "task": "mutate",
                "requested_by": "test_user",
                # parent_experiment_id is missing
            }
            response = client.post("/api/research/run", json=payload)

            assert response.status_code == 422
        finally:
            teardown_app()


# ============================================================================
# GET /api/research/runs/{id} Tests
# ============================================================================

class TestGetResearchRun:
    """test_get_research_run_returns_record"""
    def test_get_research_run_returns_record(self, tmp_path: Path):
        """GET /api/research/runs/{id} should return the experiment record."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_3")
        test_registry = ExperimentRegistry(tmp_repo)

        # Create an experiment
        record = test_registry.create(
            session_id="test-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )

        try:
            client = setup_app_with_registry(test_registry)
            response = client.get(f"/api/research/runs/{record.id}")

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == record.id
            assert data["instrument"] == "EUR_USD"
            assert data["timeframe"] == "H4"
        finally:
            teardown_app()

    def test_get_research_run_404(self, tmp_path: Path):
        """GET /api/research/runs/{id} with unknown id should return 404."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_4")
        test_registry = ExperimentRegistry(tmp_repo)

        try:
            client = setup_app_with_registry(test_registry)
            response = client.get("/api/research/runs/nonexistent-id-12345")

            assert response.status_code == 404
        finally:
            teardown_app()


# ============================================================================
# GET /api/research/runs Tests (list)
# ============================================================================

class TestListResearchRuns:
    """Test GET /api/research/runs list endpoint"""
    def test_list_research_runs_returns_records(self, tmp_path: Path):
        """GET /api/research/runs should return list of experiment records."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_5")
        test_registry = ExperimentRegistry(tmp_repo)

        # Create 2 experiments
        for i in range(2):
            test_registry.create(
                session_id=f"session-{i}",
                instrument="EUR_USD",
                timeframe="H4",
                test_start="2023-01-01",
                test_end="2024-01-01",
            )

        try:
            client = setup_app_with_registry(test_registry)
            response = client.get("/api/research/runs")

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) >= 2
        finally:
            teardown_app()

    def test_list_research_runs_filters_by_instrument(self, tmp_path: Path):
        """GET /api/research/runs?instrument=EUR_USD should filter."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_6")
        test_registry = ExperimentRegistry(tmp_repo)

        # Create EUR_USD and GBP_USD experiments
        test_registry.create(
            session_id="eur-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        test_registry.create(
            session_id="gbp-session",
            instrument="GBP_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )

        try:
            client = setup_app_with_registry(test_registry)
            response = client.get("/api/research/runs?instrument=EUR_USD")

            assert response.status_code == 200
            data = response.json()
            assert all(r["instrument"] == "EUR_USD" for r in data)
        finally:
            teardown_app()


# ============================================================================
# Integration Tests
# ============================================================================

class TestResearchRunIntegration:
    """Integration tests for the full research run flow."""
    def test_post_then_get_research_run(self, tmp_path: Path):
        """Create a research run then retrieve it."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_7")
        test_registry = ExperimentRegistry(tmp_repo)

        try:
            with patch("apps.api.routes.research._run_research_graph"):
                client = setup_app_with_registry(test_registry)
                payload = {
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "test_start": "2023-01-01",
                    "test_end": "2024-01-01",
                    "task": "generate_seed",
                    "requested_by": "integration_test",
                }
                post_response = client.post("/api/research/run", json=payload)

                assert post_response.status_code == 202
                experiment_id = post_response.json()["experiment_id"]

                # Now retrieve it
                get_response = client.get(f"/api/research/runs/{experiment_id}")
                assert get_response.status_code == 200
                data = get_response.json()
                assert data["id"] == experiment_id
                assert data["instrument"] == "EUR_USD"
        finally:
            teardown_app()

    def test_mutate_with_parent_creates_child(self, tmp_path: Path):
        """Test mutate task with valid parent_experiment_id."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_8")
        test_registry = ExperimentRegistry(tmp_repo)

        # Create parent
        parent = test_registry.create(
            session_id="parent-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
            generation=0,
        )

        try:
            with patch("apps.api.routes.research._run_research_graph"):
                client = setup_app_with_registry(test_registry)
                payload = {
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "test_start": "2023-01-01",
                    "test_end": "2024-01-01",
                    "task": "mutate",
                    "requested_by": "integration_test",
                    "parent_experiment_id": parent.id,
                }
                response = client.post("/api/research/run", json=payload)

                assert response.status_code == 202
                experiment_id = response.json()["experiment_id"]

                # Verify it was created as a child
                child = test_registry.get(experiment_id)
                assert child.parent_id == parent.id
                assert child.generation == 1
        finally:
            teardown_app()

    def test_mutate_with_nonexistent_parent_returns_404(self, tmp_path: Path):
        """Test mutate task with nonexistent parent returns 404."""
        tmp_repo = LocalMetadataRepository(tmp_path / "test_metadata_9")
        test_registry = ExperimentRegistry(tmp_repo)

        try:
            client = setup_app_with_registry(test_registry)
            payload = {
                "instrument": "EUR_USD",
                "timeframe": "H4",
                "test_start": "2023-01-01",
                "test_end": "2024-01-01",
                "task": "mutate",
                "requested_by": "integration_test",
                "parent_experiment_id": "nonexistent-parent-id",
            }
            response = client.post("/api/research/run", json=payload)

            assert response.status_code == 404
        finally:
            teardown_app()
