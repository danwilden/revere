"""Signal bank API routes.

POST /api/signals                          — create signal bank entry
GET  /api/signals                          — list all signals
GET  /api/signals/{signalId}               — get signal by ID
POST /api/signals/{signalId}/materialize   — materialize signal values over a window
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.deps import get_artifact_repo, get_market_repo, get_metadata_repo
from backend.schemas.enums import SignalType
from backend.schemas.requests import SignalCreateRequest, SignalMaterializeRequest
from backend.signals.bank import create_signal_from_hmm, get_signal, list_signals
from backend.signals.materialize import materialize_signal

router = APIRouter()


@router.post("", status_code=201)
def create_signal(
    req: SignalCreateRequest,
    metadata_repo=Depends(get_metadata_repo),
):
    if req.signal_type == SignalType.HMM_REGIME:
        source_model_id = req.source_model_id
        if not source_model_id:
            raise HTTPException(
                status_code=400,
                detail="source_model_id is required for hmm_regime signals",
            )
        feature_run_id = req.definition_json.get("feature_run_id")
        if not feature_run_id:
            raise HTTPException(
                status_code=400,
                detail="definition_json.feature_run_id is required for hmm_regime signals",
            )
        signal = create_signal_from_hmm(
            name=req.name,
            model_id=source_model_id,
            feature_run_id=feature_run_id,
            metadata_repo=metadata_repo,
        )
        return signal.model_dump(mode="json")

    raise HTTPException(
        status_code=400,
        detail=f"Signal type '{req.signal_type}' creation not yet supported",
    )


@router.get("")
def list_all_signals(metadata_repo=Depends(get_metadata_repo)):
    return list_signals(metadata_repo)


@router.get("/{signal_id}")
def get_signal_by_id(signal_id: str, metadata_repo=Depends(get_metadata_repo)):
    record = get_signal(signal_id, metadata_repo)
    if not record:
        raise HTTPException(status_code=404, detail="Signal not found")
    return record


@router.post("/{signal_id}/materialize")
def materialize(
    signal_id: str,
    req: SignalMaterializeRequest,
    market_repo=Depends(get_market_repo),
    metadata_repo=Depends(get_metadata_repo),
    artifact_repo=Depends(get_artifact_repo),
):
    signal_record = get_signal(signal_id, metadata_repo)
    if not signal_record:
        raise HTTPException(status_code=404, detail="Signal not found")

    try:
        rows = materialize_signal(
            signal_id=signal_id,
            instrument=req.instrument,
            timeframe=req.timeframe,
            start=req.start_date,
            end=req.end_date,
            market_repo=market_repo,
            metadata_repo=metadata_repo,
            artifact_repo=artifact_repo,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))

    return {"signal_id": signal_id, "count": len(rows), "rows": rows}
