"""Tests for Phase 5C feature discovery API routes.

All tests use real LocalMetadataRepository + JobManager with tmp_path,
and mock feature_researcher_node to avoid real Bedrock calls or DuckDB access.

Coverage targets:
- POST /api/features/discover — 202, validation errors
- GET  /api/features/discover/{job_id} — status, results on SUCCEEDED, 404
- GET  /api/features/library — empty list, family filter, min_f_statistic filter
- GET  /api/features/library/{name} — found, 404
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.data.local_metadata import LocalMetadataRepository
from backend.features.feature_library import FeatureLibrary
from backend.jobs.status import JobManager
from backend.schemas.enums import JobStatus, JobType
from backend.schemas.requests import FeatureEvalResult
from apps.api.main import app
from backend import deps


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_eval_result(
    name: str = "rsi_14",
    family: str = "momentum",
    discovery_run_id: str = "run-001",
    f_statistic: float | None = 4.5,
) -> FeatureEvalResult:
    return FeatureEvalResult(
        name=name,
        family=family,
        description=f"Test feature {name}",
        f_statistic=f_statistic,
        p_value=0.01,
        leakage_score=0.0,
        regime_discriminability=0.8,
        correlation_with_returns=0.15,
        evaluation_notes="test notes",
        discovery_run_id=discovery_run_id,
    )


def _setup_app(tmp_path: Path):
    """Wire tmp_path-based singletons into the FastAPI app. Returns (client, jm, lib)."""
    meta_repo = LocalMetadataRepository(tmp_path / "metadata")
    job_manager = JobManager(meta_repo)
    feature_library = FeatureLibrary(meta_repo)

    app.dependency_overrides[deps.get_job_manager] = lambda: job_manager
    app.dependency_overrides[deps.get_feature_library] = lambda: feature_library
    client = TestClient(app, raise_server_exceptions=False)
    return client, job_manager, feature_library


def _teardown_app():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /api/features/discover
# ---------------------------------------------------------------------------


class TestPostFeatureDiscover:

    def test_returns_202_with_job_id_and_queued_status(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            assert resp.status_code == 202
            data = resp.json()
            assert "job_id" in data
            assert data["status"] == JobStatus.QUEUED.value
        finally:
            _teardown_app()

    def test_stores_job_with_feature_discovery_type(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]
            job = jm.get(job_id)
            assert job is not None
            assert job["job_type"] == JobType.FEATURE_DISCOVERY.value
        finally:
            _teardown_app()

    def test_stores_discovery_run_id_in_params(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                resp = client.post("/api/features/discover", json={
                    "instrument": "GBP_USD",
                    "timeframe": "H1",
                    "eval_start": "2023-06-01",
                    "eval_end": "2023-12-01",
                    "families": ["momentum"],
                    "max_candidates": 5,
                    "requested_by": "test",
                })
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]
            job = jm.get(job_id)
            params = job.get("params_json", {})
            assert "discovery_run_id" in params
            assert params["instrument"] == "GBP_USD"
            assert params["families"] == ["momentum"]
            assert params["max_candidates"] == 5
        finally:
            _teardown_app()

    def test_422_when_eval_end_before_eval_start(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.post("/api/features/discover", json={
                "instrument": "EUR_USD",
                "timeframe": "H4",
                "eval_start": "2024-01-01",
                "eval_end": "2023-01-01",  # before start
            })
            assert resp.status_code == 422
        finally:
            _teardown_app()

    def test_422_when_eval_end_equals_eval_start(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.post("/api/features/discover", json={
                "instrument": "EUR_USD",
                "timeframe": "H4",
                "eval_start": "2023-01-01",
                "eval_end": "2023-01-01",  # equal, not after
            })
            assert resp.status_code == 422
        finally:
            _teardown_app()

    def test_422_when_instrument_blank(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.post("/api/features/discover", json={
                "instrument": "   ",
                "timeframe": "H4",
                "eval_start": "2023-01-01",
                "eval_end": "2024-01-01",
            })
            assert resp.status_code == 422
        finally:
            _teardown_app()

    def test_422_when_timeframe_blank(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.post("/api/features/discover", json={
                "instrument": "EUR_USD",
                "timeframe": "",
                "eval_start": "2023-01-01",
                "eval_end": "2024-01-01",
            })
            assert resp.status_code == 422
        finally:
            _teardown_app()

    def test_max_candidates_out_of_range_returns_422(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.post("/api/features/discover", json={
                "instrument": "EUR_USD",
                "timeframe": "H4",
                "eval_start": "2023-01-01",
                "eval_end": "2024-01-01",
                "max_candidates": 200,  # exceeds le=100
            })
            assert resp.status_code == 422
        finally:
            _teardown_app()

    def test_optional_fields_have_defaults(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]
            job = jm.get(job_id)
            params = job["params_json"]
            assert params["feature_run_id"] is None
            assert params["model_id"] is None
            assert params["families"] == []
            assert params["max_candidates"] == 20
        finally:
            _teardown_app()


# ---------------------------------------------------------------------------
# GET /api/features/discover/{job_id}
# ---------------------------------------------------------------------------


class TestGetFeatureDiscoveryJob:

    def test_returns_queued_status_immediately_after_create(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                post_resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            job_id = post_resp.json()["job_id"]
            resp = client.get(f"/api/features/discover/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == job_id
            assert data["status"] == JobStatus.QUEUED.value
            assert data["feature_eval_results"] == []
        finally:
            _teardown_app()

    def test_404_when_job_not_found(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.get("/api/features/discover/nonexistent-job-id")
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()
        finally:
            _teardown_app()

    def test_404_when_job_is_different_type(self, tmp_path: Path):
        """A BACKTEST job id must return 404 from /discover/{id}."""
        client, jm, lib = _setup_app(tmp_path)
        try:
            # Create a non-feature-discovery job directly
            job = jm.create(
                job_type=JobType.BACKTEST,
                requested_by="test",
                params={},
            )
            resp = client.get(f"/api/features/discover/{job.id}")
            assert resp.status_code == 404
            detail = resp.json()["detail"]
            assert "not a feature discovery job" in detail
        finally:
            _teardown_app()

    def test_feature_eval_results_populated_on_succeeded(self, tmp_path: Path):
        """When the job SUCCEEDS and the library has matching records, results appear."""
        client, jm, lib = _setup_app(tmp_path)
        try:
            # Create a discovery job and manually succeed it
            with patch("apps.api.routes.features._run_feature_discovery"):
                post_resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            job_id = post_resp.json()["job_id"]

            # Retrieve the discovery_run_id from job params
            job_dict = jm.get(job_id)
            discovery_run_id = job_dict["params_json"]["discovery_run_id"]

            # Populate the library with a record for this run
            eval_result = _make_eval_result(
                name="rsi_14",
                discovery_run_id=discovery_run_id,
            )
            lib.upsert(
                eval_result,
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )

            # Manually succeed the job
            jm.succeed(job_id, result_ref=discovery_run_id)

            resp = client.get(f"/api/features/discover/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == JobStatus.SUCCEEDED.value
            assert len(data["feature_eval_results"]) == 1
            assert data["feature_eval_results"][0]["name"] == "rsi_14"
            assert data["discovery_run_id"] == discovery_run_id
        finally:
            _teardown_app()

    def test_feature_eval_results_empty_when_running(self, tmp_path: Path):
        """feature_eval_results must be [] for non-SUCCEEDED status."""
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                post_resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            job_id = post_resp.json()["job_id"]
            jm.start(job_id)  # set to RUNNING

            resp = client.get(f"/api/features/discover/{job_id}")
            assert resp.status_code == 200
            assert resp.json()["feature_eval_results"] == []
        finally:
            _teardown_app()

    def test_response_includes_timing_fields(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            with patch("apps.api.routes.features._run_feature_discovery"):
                post_resp = client.post("/api/features/discover", json={
                    "instrument": "EUR_USD",
                    "timeframe": "H4",
                    "eval_start": "2023-01-01",
                    "eval_end": "2024-01-01",
                })
            job_id = post_resp.json()["job_id"]
            resp = client.get(f"/api/features/discover/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert "created_at" in data
            assert "progress_pct" in data
            assert "stage_label" in data
        finally:
            _teardown_app()


# ---------------------------------------------------------------------------
# GET /api/features/library
# ---------------------------------------------------------------------------


class TestGetFeatureLibrary:

    def test_returns_empty_list_when_no_features(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.get("/api/features/library")
            assert resp.status_code == 200
            data = resp.json()
            assert data["features"] == []
            assert data["count"] == 0
        finally:
            _teardown_app()

    def test_returns_registered_features(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            lib.upsert(
                _make_eval_result("rsi_14", "momentum"),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            resp = client.get("/api/features/library")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["features"][0]["name"] == "rsi_14"
        finally:
            _teardown_app()

    def test_filters_by_family(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            lib.upsert(
                _make_eval_result("rsi_14", "momentum"),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            lib.upsert(
                _make_eval_result("atr_14", "volatility"),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            resp = client.get("/api/features/library?family=momentum")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["features"][0]["family"] == "momentum"
        finally:
            _teardown_app()

    def test_filters_by_min_f_statistic(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            lib.upsert(
                _make_eval_result("low_f", "momentum", f_statistic=1.5),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            lib.upsert(
                _make_eval_result("high_f", "momentum", f_statistic=5.0),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            resp = client.get("/api/features/library?min_f_statistic=3.0")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["features"][0]["name"] == "high_f"
        finally:
            _teardown_app()

    def test_filters_by_max_leakage(self, tmp_path: Path):
        """Features with leakage_score above max_leakage are excluded."""
        client, jm, lib = _setup_app(tmp_path)
        try:
            # low leakage feature
            low_leak = FeatureEvalResult(
                name="low_leak",
                family="trend",
                description="low leakage feature",
                f_statistic=4.0,
                p_value=0.01,
                leakage_score=0.1,
                regime_discriminability=None,
                correlation_with_returns=None,
                evaluation_notes="",
                discovery_run_id="run-x",
            )
            high_leak = FeatureEvalResult(
                name="high_leak",
                family="trend",
                description="high leakage feature",
                f_statistic=4.0,
                p_value=0.01,
                leakage_score=0.9,
                regime_discriminability=None,
                correlation_with_returns=None,
                evaluation_notes="",
                discovery_run_id="run-x",
            )
            lib.upsert(low_leak, instrument="EUR_USD", timeframe="H4", eval_start="2023-01-01", eval_end="2024-01-01")
            lib.upsert(high_leak, instrument="EUR_USD", timeframe="H4", eval_start="2023-01-01", eval_end="2024-01-01")

            resp = client.get("/api/features/library?max_leakage=0.5")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["features"][0]["name"] == "low_leak"
        finally:
            _teardown_app()

    def test_limit_parameter(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            for i in range(10):
                lib.upsert(
                    _make_eval_result(f"feature_{i:02d}", "momentum"),
                    instrument="EUR_USD",
                    timeframe="H4",
                    eval_start="2023-01-01",
                    eval_end="2024-01-01",
                )
            resp = client.get("/api/features/library?limit=3")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 3
            assert len(data["features"]) == 3
        finally:
            _teardown_app()

    def test_combined_family_and_f_statistic_filter(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            lib.upsert(_make_eval_result("mom_low_f", "momentum", f_statistic=1.0),
                       instrument="EUR_USD", timeframe="H4", eval_start="2023-01-01", eval_end="2024-01-01")
            lib.upsert(_make_eval_result("mom_high_f", "momentum", f_statistic=6.0),
                       instrument="EUR_USD", timeframe="H4", eval_start="2023-01-01", eval_end="2024-01-01")
            lib.upsert(_make_eval_result("vol_high_f", "volatility", f_statistic=6.0),
                       instrument="EUR_USD", timeframe="H4", eval_start="2023-01-01", eval_end="2024-01-01")

            resp = client.get("/api/features/library?family=momentum&min_f_statistic=3.0")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["features"][0]["name"] == "mom_high_f"
        finally:
            _teardown_app()


# ---------------------------------------------------------------------------
# GET /api/features/library/{name}
# ---------------------------------------------------------------------------


class TestGetFeatureByName:

    def test_returns_feature_when_it_exists(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            lib.upsert(
                _make_eval_result("rsi_14", "momentum"),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            resp = client.get("/api/features/library/rsi_14")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "rsi_14"
            assert data["family"] == "momentum"
            assert "id" in data
            assert "discovered_at" in data
            assert "last_evaluated_at" in data
        finally:
            _teardown_app()

    def test_404_when_feature_not_found(self, tmp_path: Path):
        client, jm, lib = _setup_app(tmp_path)
        try:
            resp = client.get("/api/features/library/nonexistent_feature")
            assert resp.status_code == 404
            detail = resp.json()["detail"]
            assert "nonexistent_feature" in detail
            assert "not found in library" in detail
        finally:
            _teardown_app()

    def test_upsert_preserves_id_and_discovered_at(self, tmp_path: Path):
        """Re-upserting same feature name preserves id and discovered_at."""
        client, jm, lib = _setup_app(tmp_path)
        try:
            lib.upsert(
                _make_eval_result("rsi_14", "momentum", f_statistic=3.0),
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
            )
            first_resp = client.get("/api/features/library/rsi_14")
            first_data = first_resp.json()
            original_id = first_data["id"]
            original_discovered_at = first_data["discovered_at"]

            # Re-upsert with updated scores
            lib.upsert(
                _make_eval_result("rsi_14", "momentum", f_statistic=7.0),
                instrument="GBP_USD",
                timeframe="H1",
                eval_start="2023-06-01",
                eval_end="2024-06-01",
            )
            second_resp = client.get("/api/features/library/rsi_14")
            second_data = second_resp.json()

            # id and discovered_at preserved; scores + instrument updated
            assert second_data["id"] == original_id
            assert second_data["discovered_at"] == original_discovered_at
            assert second_data["f_statistic"] == 7.0
            assert second_data["instrument"] == "GBP_USD"
        finally:
            _teardown_app()


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestFeatureDiscoverRequestSchema:

    def test_max_candidates_ge_1(self):
        from backend.schemas.requests import FeatureDiscoverRequest
        import pytest as _pytest
        with _pytest.raises(Exception):
            FeatureDiscoverRequest(
                instrument="EUR_USD",
                timeframe="H4",
                eval_start="2023-01-01",
                eval_end="2024-01-01",
                max_candidates=0,
            )

    def test_families_defaults_to_empty_list(self):
        from backend.schemas.requests import FeatureDiscoverRequest
        req = FeatureDiscoverRequest(
            instrument="EUR_USD",
            timeframe="H4",
            eval_start="2023-01-01",
            eval_end="2024-01-01",
        )
        assert req.families == []

    def test_requested_by_defaults_to_api(self):
        from backend.schemas.requests import FeatureDiscoverRequest
        req = FeatureDiscoverRequest(
            instrument="EUR_USD",
            timeframe="H4",
            eval_start="2023-01-01",
            eval_end="2024-01-01",
        )
        assert req.requested_by == "api"


# ---------------------------------------------------------------------------
# JobType enum test
# ---------------------------------------------------------------------------


class TestJobTypeEnum:

    def test_feature_discovery_enum_exists(self):
        assert JobType.FEATURE_DISCOVERY.value == "FEATURE_DISCOVERY"

    def test_feature_discovery_distinct_from_feature_generation(self):
        assert JobType.FEATURE_DISCOVERY != JobType.FEATURE_GENERATION
