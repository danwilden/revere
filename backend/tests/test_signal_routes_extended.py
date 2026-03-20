"""Route-level tests for extended signals API (Phase 5E).

Covers:
  - POST /api/signals/{id}/materialize   → 202 async job
  - GET  /api/signals/{id}/materialize/jobs/{job_id}
  - GET  /api/signals/context
  - POST /api/signals/risk-filter
  - Extended POST /api/signals (all signal types)
  - Route ordering (context/risk-filter before {signal_id})
  - response_model annotations via OpenAPI schema
  - run_materialize_signal_job unit behaviour
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from backend.data.local_metadata import LocalMetadataRepository
from backend.deps import (
    get_artifact_repo,
    get_job_manager,
    get_market_repo,
    get_metadata_repo,
)
from backend.jobs.status import JobManager
from backend.schemas.enums import JobStatus, JobType, SignalType
from backend.schemas.models import Signal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_metadata(tmp_path):
    return LocalMetadataRepository(tmp_path / "metadata")


@pytest.fixture()
def tmp_artifact_repo(tmp_path):
    from backend.data.local_artifacts import LocalArtifactRepository
    return LocalArtifactRepository(tmp_path / "artifacts")


@pytest.fixture()
def job_manager(tmp_metadata):
    return JobManager(tmp_metadata)


@pytest.fixture()
def mock_market_repo():
    return MagicMock()


@pytest.fixture()
def client(tmp_metadata, tmp_artifact_repo, job_manager, mock_market_repo):
    app.dependency_overrides[get_metadata_repo] = lambda: tmp_metadata
    app.dependency_overrides[get_artifact_repo] = lambda: tmp_artifact_repo
    app.dependency_overrides[get_job_manager] = lambda: job_manager
    app.dependency_overrides[get_market_repo] = lambda: mock_market_repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_signal(tmp_metadata, signal_id: str = "sig-001", signal_type: str = "hmm_regime") -> dict:
    """Insert a minimal Signal record directly into metadata store."""
    record = {
        "id": signal_id,
        "name": "Test Signal",
        "signal_type": signal_type,
        "definition_json": {"model_id": "model-001", "feature_run_id": "feat-001"},
        "metadata": {"field_name": "hmm_regime"},
        "source_model_id": "model-001",
        "version": 1,
        "created_at": "2026-01-01T00:00:00",
    }
    tmp_metadata.save_signal(record)
    return record


# ---------------------------------------------------------------------------
# Test 1: POST /api/signals/{id}/materialize returns 202 (not 200 inline)
# ---------------------------------------------------------------------------

def test_materialize_returns_202(client, tmp_metadata):
    _seed_signal(tmp_metadata)

    with patch("apps.api.routes.signals.run_materialize_signal_job"):
        resp = client.post(
            "/api/signals/sig-001/materialize",
            json={
                "instrument_id": "EUR_USD",
                "timeframe": "H1",
                "start": "2024-01-01",
                "end": "2024-06-30",
            },
        )

    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Test 2: POST materialize body contains job_id and status
# ---------------------------------------------------------------------------

def test_materialize_body_has_job_id_and_status(client, tmp_metadata):
    _seed_signal(tmp_metadata)

    with patch("apps.api.routes.signals.run_materialize_signal_job"):
        resp = client.post(
            "/api/signals/sig-001/materialize",
            json={
                "instrument_id": "EUR_USD",
                "timeframe": "H1",
                "start": "2024-01-01",
                "end": "2024-06-30",
            },
        )

    body = resp.json()
    assert "id" in body
    assert "status" in body
    assert body["job_type"] == JobType.SIGNAL_MATERIALIZE.value


# ---------------------------------------------------------------------------
# Test 3: GET /api/signals/{id}/materialize/jobs/{job_id} returns job status
# ---------------------------------------------------------------------------

def test_get_materialize_job_returns_job_status(client, tmp_metadata, job_manager):
    _seed_signal(tmp_metadata)

    # Create a job directly via job_manager to control status
    job = job_manager.create(job_type=JobType.SIGNAL_MATERIALIZE, params={})

    resp = client.get(f"/api/signals/sig-001/materialize/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job.id
    assert body["job_type"] == JobType.SIGNAL_MATERIALIZE.value


# ---------------------------------------------------------------------------
# Test 4: GET /api/signals/context returns SignalContextResponse with fields
# ---------------------------------------------------------------------------

def test_get_context_returns_available_fields(client, tmp_metadata):
    _seed_signal(tmp_metadata, signal_id="sig-ctx-001")

    resp = client.get(
        "/api/signals/context",
        params={
            "instrument_id": "EUR_USD",
            "timeframe": "H1",
            "start": "2024-01-01",
            "end": "2024-06-30",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["instrument_id"] == "EUR_USD"
    assert body["timeframe"] == "H1"
    assert "available_fields" in body
    assert "hmm_regime" in body["available_fields"]


# ---------------------------------------------------------------------------
# Test 5: GET /api/signals/context with no signals returns empty available_fields
# ---------------------------------------------------------------------------

def test_get_context_empty_when_no_signals(client):
    resp = client.get(
        "/api/signals/context",
        params={
            "instrument_id": "EUR_USD",
            "timeframe": "H1",
            "start": "2024-01-01",
            "end": "2024-06-30",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["available_fields"] == []


# ---------------------------------------------------------------------------
# Test 6: POST /api/signals/risk-filter creates Signal with signal_type=risk_filter
# ---------------------------------------------------------------------------

def test_create_risk_filter_signal_type(client, tmp_metadata):
    mock_signal = Signal(
        id="rf-001",
        name="My Risk Filter",
        signal_type=SignalType.RISK_FILTER,
        definition_json={"rules_node": {"type": "all", "rules": []}},
        metadata={"field_name": "risk_filter"},
        version=1,
    )

    with patch(
        "apps.api.routes.signals.build_risk_filter_signal",
        return_value=mock_signal,
    ):
        resp = client.post(
            "/api/signals/risk-filter",
            json={
                "name": "My Risk Filter",
                "description": "Filters high-vol periods",
                "rules_node": {"type": "all", "rules": []},
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["signal_type"] == SignalType.RISK_FILTER.value


# ---------------------------------------------------------------------------
# Test 7: POST /api/signals/risk-filter returns 201 with Signal body
# ---------------------------------------------------------------------------

def test_create_risk_filter_returns_201_with_signal(client):
    mock_signal = Signal(
        id="rf-002",
        name="Filter 2",
        signal_type=SignalType.RISK_FILTER,
        definition_json={},
        metadata={"field_name": "risk_filter"},
        version=1,
    )

    with patch(
        "apps.api.routes.signals.build_risk_filter_signal",
        return_value=mock_signal,
    ):
        resp = client.post(
            "/api/signals/risk-filter",
            json={
                "name": "Filter 2",
                "description": "desc",
                "rules_node": {"type": "not", "rule": {"field": "adx_14", "op": "lt", "value": 20}},
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert body["name"] == "Filter 2"


# ---------------------------------------------------------------------------
# Test 8: POST /api/signals with signal_type=automl_direction_prob dispatches
# ---------------------------------------------------------------------------

def test_create_signal_automl_type_dispatches(client, tmp_metadata):
    from backend.schemas.models import AutoMLJobRecord
    automl_record = AutoMLJobRecord(
        id="automl-job-001",
        job_id="automl-job-001",
        instrument_id="EUR_USD",
        timeframe="H1",
        feature_run_id="feat-001",
        model_id="model-001",
        target_type="direction",
        status="completed",
    )
    tmp_metadata._upsert("automl_jobs", automl_record.model_dump(mode="json"))

    mock_signal = Signal(
        id="s-automl-001",
        name="AutoML Sig",
        signal_type=SignalType.AUTOML_DIRECTION_PROB,
        definition_json={},
        metadata={"field_name": "automl_direction_prob"},
        version=1,
    )

    with patch("apps.api.routes.signals.create_signal_from_automl", return_value=mock_signal):
        resp = client.post(
            "/api/signals",
            json={
                "name": "AutoML Sig",
                "signal_type": "automl_direction_prob",
                "automl_job_id": "automl-job-001",
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["signal_type"] == SignalType.AUTOML_DIRECTION_PROB.value


# ---------------------------------------------------------------------------
# Test 9: POST /api/signals with automl_direction_prob missing automl_job_id → 422
# ---------------------------------------------------------------------------

def test_create_signal_automl_missing_job_id_returns_422(client):
    resp = client.post(
        "/api/signals",
        json={
            "name": "AutoML Sig",
            "signal_type": "automl_direction_prob",
            # automl_job_id intentionally omitted
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 10: POST /api/signals with risk_filter dispatches to build_risk_filter_signal
# ---------------------------------------------------------------------------

def test_create_signal_risk_filter_dispatches(client):
    mock_signal = Signal(
        id="rf-via-create",
        name="RF via create",
        signal_type=SignalType.RISK_FILTER,
        definition_json={},
        metadata={"field_name": "risk_filter"},
        version=1,
    )

    with patch(
        "apps.api.routes.signals.build_risk_filter_signal",
        return_value=mock_signal,
    ):
        resp = client.post(
            "/api/signals",
            json={
                "name": "RF via create",
                "signal_type": "risk_filter",
                "rules_node": {"type": "all", "rules": []},
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["signal_type"] == SignalType.RISK_FILTER.value


# ---------------------------------------------------------------------------
# Test 11: POST /api/signals with risk_filter missing rules_node → 422
# ---------------------------------------------------------------------------

def test_create_signal_risk_filter_missing_rules_node_returns_422(client):
    resp = client.post(
        "/api/signals",
        json={
            "name": "Bad RF",
            "signal_type": "risk_filter",
            # rules_node intentionally omitted
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 12: POST /api/signals with unknown signal_type → 422
# ---------------------------------------------------------------------------

def test_create_signal_unknown_type_returns_422(client):
    resp = client.post(
        "/api/signals",
        json={
            "name": "Bad Signal",
            "signal_type": "totally_unknown_type",
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 13: POST /api/signals with hmm_regime still works (regression)
# ---------------------------------------------------------------------------

def test_create_signal_hmm_regime_regression(client, tmp_metadata):
    # Seed a model record so create_signal_from_hmm doesn't raise
    model_record = {
        "id": "model-reg-001",
        "model_type": "hmm",
        "instrument_id": "EUR_USD",
        "timeframe": "H1",
        "training_start": "2023-01-01T00:00:00",
        "training_end": "2023-12-31T00:00:00",
        "parameters_json": {},
        "artifact_ref": None,
        "label_map_json": {},
        "created_at": "2023-12-31T00:00:00",
        "status": "succeeded",
    }
    tmp_metadata.save_model(model_record)

    resp = client.post(
        "/api/signals",
        json={
            "name": "HMM Signal Regression",
            "signal_type": "hmm_regime",
            "model_id": "model-reg-001",
            "feature_run_id": "feat-reg-001",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["signal_type"] == SignalType.HMM_REGIME.value


# ---------------------------------------------------------------------------
# Test 14: All new routes have typed response_model annotations (OpenAPI schema)
# ---------------------------------------------------------------------------

def test_signal_routes_have_response_model_annotations(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})

    signal_paths = [p for p in paths if p.startswith("/api/signals")]
    assert len(signal_paths) >= 5, f"Expected >= 5 signal paths, got: {signal_paths}"

    for path in signal_paths:
        path_item = paths[path]
        for method, operation in path_item.items():
            if method not in ("get", "post"):
                continue
            responses = operation.get("responses", {})
            success_codes = [c for c in responses if c.startswith("2")]
            assert success_codes, f"{method.upper()} {path} has no 2xx response"
            for code in success_codes:
                content = responses[code].get("content", {})
                if content:
                    schema_ref = content.get("application/json", {}).get("schema")
                    assert schema_ref is not None, (
                        f"{method.upper()} {path} ({code}) missing schema"
                    )


# ---------------------------------------------------------------------------
# Test 15: GET /api/signals/context is NOT treated as /{signal_id}
# ---------------------------------------------------------------------------

def test_context_route_not_matched_as_signal_id(client):
    """Ensure FastAPI routes GET /api/signals/context to the context handler,
    not to get_signal_by_id (which would return 404 for id='context')."""
    resp = client.get(
        "/api/signals/context",
        params={
            "instrument_id": "EUR_USD",
            "timeframe": "H1",
            "start": "2024-01-01",
            "end": "2024-12-31",
        },
    )
    # If the route were matched as /{signal_id}, we'd get a 404 "Signal not found"
    # and the body would be {"detail": "Signal not found"}.
    # The correct route returns a SignalContextResponse — must have available_fields.
    assert resp.status_code == 200
    body = resp.json()
    assert "available_fields" in body, (
        "context route was incorrectly matched as /{signal_id}; "
        f"got body: {body}"
    )


# ---------------------------------------------------------------------------
# Test 16: run_materialize_signal_job calls job_manager.succeed() on success
# ---------------------------------------------------------------------------

def test_run_materialize_signal_job_calls_succeed_on_success():
    from backend.jobs.signal_materialize import run_materialize_signal_job
    from backend.schemas.requests import MaterializeSignalRequest

    mock_job_manager = MagicMock()
    mock_market_repo = MagicMock()
    mock_metadata_repo = MagicMock()
    mock_artifact_repo = MagicMock()

    fake_rows = [{"timestamp_utc": "2024-01-01", "regime_label": "TREND_BULL_LOW_VOL"}]

    request = MaterializeSignalRequest(
        instrument_id="EUR_USD",
        timeframe="H1",
        start="2024-01-01",
        end="2024-06-30",
    )

    materialize_path = "backend.jobs.signal_materialize._materialize_signal_sync"
    with patch(materialize_path, return_value=fake_rows):
        run_materialize_signal_job(
            job_id="job-test-001",
            signal_id="sig-test-001",
            request=request,
            market_repo=mock_market_repo,
            metadata_repo=mock_metadata_repo,
            artifact_repo=mock_artifact_repo,
            job_manager=mock_job_manager,
        )

    mock_job_manager.start.assert_called_once_with("job-test-001")
    mock_job_manager.succeed.assert_called_once()
    call_kwargs = mock_job_manager.succeed.call_args
    assert call_kwargs[1]["result_ref"] is not None or call_kwargs[0][0] == "job-test-001"
