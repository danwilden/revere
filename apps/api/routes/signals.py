"""Signal bank API routes.

POST /api/signals                                   — create signal bank entry
GET  /api/signals                                   — list all signals
GET  /api/signals/context                           — available signal fields for a window
POST /api/signals/risk-filter                       — create a risk-filter signal
GET  /api/signals/{signal_id}                       — get signal by ID
POST /api/signals/{signal_id}/materialize           — async materialize (returns 202 JobRun)
GET  /api/signals/{signal_id}/materialize/jobs/{job_id} — poll materialization job

IMPORTANT: route ordering below is intentional.
  /context and /risk-filter must be registered BEFORE /{signal_id} so FastAPI
  does not treat the literal path segment as a path-parameter value.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.deps import (
    get_artifact_repo,
    get_job_manager,
    get_market_repo,
    get_metadata_repo,
)
from backend.jobs.signal_materialize import run_materialize_signal_job
from backend.schemas.enums import JobType, SignalType
from backend.schemas.models import JobRun, Signal
from backend.schemas.requests import (
    CreateRiskFilterRequest,
    CreateSignalRequest,
    MaterializeSignalRequest,
    SignalContextResponse,
    SignalCreateRequest,
    SignalMaterializeRequest,
)
from backend.signals.bank import create_signal_from_hmm, get_signal, list_signals
from backend.signals.automl_signal import create_signal_from_automl

try:
    from backend.signals.risk_filter import build_risk_filter_signal  # type: ignore[import]
except ImportError:
    build_risk_filter_signal = None  # type: ignore[assignment]

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/signals — create signal (extended: all signal types)
# ---------------------------------------------------------------------------

@router.post("", status_code=201, response_model=Signal)
def create_signal(
    req: CreateSignalRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    """Create a signal bank entry for any supported signal type.

    Dispatches on signal_type:
      - "hmm_regime": requires model_id + feature_run_id
      - "automl_direction_prob" / "automl_return_bucket": requires automl_job_id
      - "risk_filter": requires rules_node
      - Unknown type: 422
    """
    signal_type = req.signal_type

    if signal_type == SignalType.HMM_REGIME.value or signal_type == SignalType.HMM_REGIME:
        if not req.model_id:
            raise HTTPException(
                status_code=422,
                detail="model_id is required for hmm_regime signals",
            )
        if not req.feature_run_id:
            raise HTTPException(
                status_code=422,
                detail="feature_run_id is required for hmm_regime signals",
            )
        signal = create_signal_from_hmm(
            name=req.name,
            model_id=req.model_id,
            feature_run_id=req.feature_run_id,
            metadata_repo=metadata_repo,
        )
        return signal

    if signal_type in (
        SignalType.AUTOML_DIRECTION_PROB.value,
        SignalType.AUTOML_RETURN_BUCKET.value,
        SignalType.AUTOML_DIRECTION_PROB,
        SignalType.AUTOML_RETURN_BUCKET,
    ):
        if not req.automl_job_id:
            raise HTTPException(
                status_code=422,
                detail="automl_job_id is required for automl signal types",
            )
        from backend.schemas.models import AutoMLJobRecord

        # Load the AutoML record from metadata
        automl_record_dict = metadata_repo._get("automl_jobs", req.automl_job_id)
        if not automl_record_dict:
            raise HTTPException(
                status_code=404,
                detail=f"AutoML job {req.automl_job_id} not found",
            )
        automl_record = AutoMLJobRecord(**automl_record_dict)
        signal = create_signal_from_automl(
            automl_record=automl_record,
            metadata_repo=metadata_repo,
            signal_name=req.name,
        )
        return signal

    if signal_type in (SignalType.RISK_FILTER.value, SignalType.RISK_FILTER):
        if not req.rules_node:
            raise HTTPException(
                status_code=422,
                detail="rules_node is required for risk_filter signals",
            )
        if build_risk_filter_signal is None:
            raise HTTPException(
                status_code=501,
                detail="Risk filter signal type is not yet available",
            )
        signal = build_risk_filter_signal(
            name=req.name,
            rules_node=req.rules_node,
            description=req.metadata.get("description", ""),
            metadata_repo=metadata_repo,
        )
        return signal

    raise HTTPException(
        status_code=422,
        detail=f"Unknown or unsupported signal_type: '{signal_type}'",
    )


# ---------------------------------------------------------------------------
# GET /api/signals — list all signals
# ---------------------------------------------------------------------------

@router.get("", response_model=list[Signal])
def list_all_signals(metadata_repo=Depends(get_metadata_repo)):
    return list_signals(metadata_repo)


# ---------------------------------------------------------------------------
# GET /api/signals/context  — MUST be before /{signal_id}
# ---------------------------------------------------------------------------

@router.get("/context", response_model=SignalContextResponse)
def get_signal_context(
    instrument_id: str = Query(...),
    timeframe: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    metadata_repo=Depends(get_metadata_repo),
    market_repo=Depends(get_market_repo),
):
    """Return the list of signal field names materialized for a given window.

    Queries the features table (DuckDB) for distinct feature_name values
    where the feature_run_id corresponds to a known signal.  Maps each
    feature_name back to a user-facing field_name via the Signal metadata.
    """
    # Collect all known signal IDs and their field_name metadata
    all_signals = metadata_repo.list_signals()
    signal_map: dict[str, dict] = {s["id"]: s for s in all_signals}

    if not signal_map:
        return SignalContextResponse(
            instrument_id=instrument_id,
            timeframe=timeframe,
            start=start,
            end=end,
            available_fields=[],
        )

    available_fields: list[str] = []
    for signal_id, signal_record in signal_map.items():
        field_name = signal_record.get("metadata", {}).get("field_name")
        if field_name:
            available_fields.append(field_name)

    return SignalContextResponse(
        instrument_id=instrument_id,
        timeframe=timeframe,
        start=start,
        end=end,
        available_fields=available_fields,
    )


# ---------------------------------------------------------------------------
# POST /api/signals/risk-filter  — MUST be before /{signal_id}
# ---------------------------------------------------------------------------

@router.post("/risk-filter", status_code=201, response_model=Signal)
def create_risk_filter_signal(
    req: CreateRiskFilterRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    """Create a risk-filter signal backed by a rules DSL node."""
    if build_risk_filter_signal is None:
        raise HTTPException(
            status_code=501,
            detail="Risk filter signal type is not yet available",
        )

    signal = build_risk_filter_signal(
        name=req.name,
        rules_node=req.rules_node,
        description=req.description,
        metadata_repo=metadata_repo,
    )
    return signal


# ---------------------------------------------------------------------------
# GET /api/signals/{signal_id}
# ---------------------------------------------------------------------------

@router.get("/{signal_id}", response_model=Signal)
def get_signal_by_id(signal_id: str, metadata_repo=Depends(get_metadata_repo)):
    record = get_signal(signal_id, metadata_repo)
    if not record:
        raise HTTPException(status_code=404, detail="Signal not found")
    return record


# ---------------------------------------------------------------------------
# POST /api/signals/{signal_id}/materialize  — async job, returns 202
# ---------------------------------------------------------------------------

@router.post(
    "/{signal_id}/materialize",
    status_code=202,
    response_model=JobRun,
)
def materialize(
    signal_id: str,
    req: MaterializeSignalRequest,
    background_tasks: BackgroundTasks,
    market_repo=Depends(get_market_repo),
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
    job_manager=Depends(get_job_manager),
):
    """Kick off an async materialization job.  Returns 202 with JobRun immediately."""
    signal_record = get_signal(signal_id, metadata_repo)
    if not signal_record:
        raise HTTPException(status_code=404, detail="Signal not found")

    job = job_manager.create(
        job_type=JobType.SIGNAL_MATERIALIZE,
        params={
            "signal_id": signal_id,
            "instrument_id": req.instrument_id,
            "timeframe": req.timeframe,
            "start": req.start,
            "end": req.end,
        },
    )

    background_tasks.add_task(
        run_materialize_signal_job,
        job_id=job.id,
        signal_id=signal_id,
        request=req,
        market_repo=market_repo,
        metadata_repo=metadata_repo,
        artifact_repo=artifact_repo,
        job_manager=job_manager,
    )

    return job


# ---------------------------------------------------------------------------
# GET /api/signals/{signal_id}/materialize/jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{signal_id}/materialize/jobs/{job_id}",
    response_model=JobRun,
)
def get_materialize_job(
    signal_id: str,
    job_id: str,
    job_manager=Depends(get_job_manager),
):
    """Poll the status of a materialization job."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
