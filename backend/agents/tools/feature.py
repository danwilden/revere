"""Feature discovery tool executors.

Four async tools used by feature_researcher_node:
  - propose_feature  : validate a FeatureSpec dict from the LLM
  - compute_feature  : run feature code against bar data
  - evaluate_feature : ANOVA F-statistic across HMM regime labels
  - register_feature : persist survivor to FeatureLibrary
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from backend.agents.tools.client import MedallionClient
from backend.agents.tools.schemas import (
    ALLOWED_FAMILIES,
    ComputeFeatureInput,
    ComputeFeatureOutput,
    EvaluateFeatureInput,
    EvaluateFeatureOutput,
    FeatureEvalResult,
    FeatureSpec,
    ProposeFeatureInput,
    ProposeFeatureOutput,
    RegisterFeatureInput,
    RegisterFeatureOutput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level caches — process-scoped, survive across node invocations
# ---------------------------------------------------------------------------

_FEATURE_SERIES_CACHE: dict[str, pd.Series] = {}
_FEATURE_SPEC_CACHE: dict[str, FeatureSpec] = {}
_FEATURE_EVAL_CACHE: dict[str, FeatureEvalResult] = {}


def clear_session_caches(feature_names: list[str]) -> None:
    """Remove a specific set of feature names from all caches (for testing)."""
    for name in feature_names:
        _FEATURE_SERIES_CACHE.pop(name, None)
        _FEATURE_SPEC_CACHE.pop(name, None)
        _FEATURE_EVAL_CACHE.pop(name, None)


# ---------------------------------------------------------------------------
# Tool 1: propose_feature
# ---------------------------------------------------------------------------

async def propose_feature(
    inp: ProposeFeatureInput,
    client: MedallionClient,  # noqa: ARG001 — unused but required by tool protocol
) -> ProposeFeatureOutput:
    """Validate a raw FeatureSpec dict from the LLM.

    Checks Pydantic v2 schema compliance and family allowlist.
    Pure validation — no I/O. Caches the spec on success.
    """
    errors: list[str] = []

    # Pydantic validation
    try:
        spec = FeatureSpec.model_validate(inp.spec)
    except Exception as exc:  # pydantic ValidationError
        return ProposeFeatureOutput(valid=False, errors=[str(exc)], spec=None)

    # Family allowlist check (fail fast before any compute)
    if spec.family not in ALLOWED_FAMILIES:
        errors.append(
            f"family '{spec.family}' is not allowed. "
            f"Must be one of: {sorted(ALLOWED_FAMILIES)}"
        )

    # Leakage warning (not a hard block here — library.register() enforces it)
    if spec.leakage_risk not in ("none", "low", "medium", "high"):
        errors.append(
            f"leakage_risk '{spec.leakage_risk}' is not valid. "
            "Must be one of: none, low, medium, high"
        )

    if errors:
        return ProposeFeatureOutput(valid=False, errors=errors, spec=None)

    # Cache validated spec
    _FEATURE_SPEC_CACHE[spec.name] = spec
    return ProposeFeatureOutput(valid=True, errors=[], spec=spec.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Tool 2: compute_feature
# ---------------------------------------------------------------------------

async def compute_feature(
    inp: ComputeFeatureInput,
    client: MedallionClient,  # noqa: ARG001
) -> ComputeFeatureOutput:
    """Execute feature code against bar data using the subprocess sandbox.

    Reads bars from DuckDB, passes them to FeatureComputer, caches the Series.
    """
    from backend.deps import get_market_repo
    from backend.features.compute import FeatureComputer
    from backend.features.sandbox import SandboxError, SandboxTimeoutError, SandboxValidationError
    from backend.schemas.enums import Timeframe

    # Resolve timeframe enum
    try:
        tf = Timeframe(inp.timeframe)
    except ValueError:
        return ComputeFeatureOutput(
            feature_name=inp.feature_name,
            success=False,
            error=f"Unknown timeframe: {inp.timeframe}",
        )

    # Parse dates
    try:
        start_dt = datetime.fromisoformat(inp.start)
        end_dt = datetime.fromisoformat(inp.end)
    except ValueError as exc:
        return ComputeFeatureOutput(
            feature_name=inp.feature_name,
            success=False,
            error=f"Invalid date: {exc}",
        )

    # Build a temporary FeatureSpec with just the code (for FeatureComputer)
    cached_spec = _FEATURE_SPEC_CACHE.get(inp.feature_name)
    if cached_spec is None:
        # Build a minimal spec from the tool input
        try:
            tmp_spec = FeatureSpec(
                name=inp.feature_name,
                family="momentum",  # placeholder — not used by FeatureComputer
                formula_description="",
                lookback_bars=0,
                dependency_columns=[],
                transformation="",
                expected_intuition="",
                leakage_risk="none",
                code=inp.code,
            )
        except Exception as exc:
            return ComputeFeatureOutput(
                feature_name=inp.feature_name,
                success=False,
                error=f"Could not build spec: {exc}",
            )
    else:
        # Use cached spec but override code with what was passed (may be a revision)
        tmp_spec = cached_spec.model_copy(update={"code": inp.code})

    try:
        market_repo = get_market_repo()
        computer = FeatureComputer(market_repo)
        series = computer.compute(tmp_spec, inp.instrument, tf, start_dt, end_dt)
    except (SandboxError, SandboxTimeoutError, SandboxValidationError) as exc:
        return ComputeFeatureOutput(
            feature_name=inp.feature_name,
            success=False,
            error=str(exc),
        )
    except ValueError as exc:
        return ComputeFeatureOutput(
            feature_name=inp.feature_name,
            success=False,
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("compute_feature unexpected error for '%s'", inp.feature_name)
        return ComputeFeatureOutput(
            feature_name=inp.feature_name,
            success=False,
            error=str(exc),
        )

    # Cache the Series
    _FEATURE_SERIES_CACHE[inp.feature_name] = series

    # Also update the cached spec's code with the version that succeeded
    if inp.feature_name in _FEATURE_SPEC_CACHE:
        _FEATURE_SPEC_CACHE[inp.feature_name] = _FEATURE_SPEC_CACHE[inp.feature_name].model_copy(
            update={"code": inp.code}
        )

    # Build sample values (first 5 non-null)
    non_null = series.dropna()
    sample_values = [float(v) for v in non_null.head(5).tolist()]

    return ComputeFeatureOutput(
        feature_name=inp.feature_name,
        success=True,
        series_length=int(non_null.shape[0]),
        sample_values=sample_values,
    )


# ---------------------------------------------------------------------------
# Tool 3: evaluate_feature
# ---------------------------------------------------------------------------

async def evaluate_feature(
    inp: EvaluateFeatureInput,
    client: MedallionClient,  # noqa: ARG001
) -> EvaluateFeatureOutput:
    """Run ANOVA F-statistic on cached Series against HMM regime labels."""
    from backend.deps import get_market_repo
    from backend.features.evaluate import FeatureEvaluator
    from backend.features.feature_library import REGISTRATION_THRESHOLD
    from backend.schemas.enums import Timeframe

    # Retrieve cached Series
    series = _FEATURE_SERIES_CACHE.get(inp.feature_name)
    if series is None:
        # Return zero-score result if series not cached
        return EvaluateFeatureOutput(
            feature_name=inp.feature_name,
            f_statistic=0.0,
            regime_breakdown={},
            leakage_risk="none",
            passes_threshold=False,
        )

    # Retrieve cached spec for leakage_risk
    spec = _FEATURE_SPEC_CACHE.get(inp.feature_name)
    leakage_risk = spec.leakage_risk if spec else "none"

    # Load regime labels from DuckDB
    try:
        tf = Timeframe(inp.timeframe)
        start_dt = datetime.fromisoformat(inp.start)
        end_dt = datetime.fromisoformat(inp.end)
        market_repo = get_market_repo()
        raw_labels = market_repo.get_regime_labels(
            inp.model_id, inp.instrument, tf, start_dt, end_dt
        )
        # Convert to [{"timestamp_utc": ..., "label": ...}] format
        regime_label_dicts = [
            {"timestamp_utc": r["timestamp_utc"], "label": r["regime_label"]}
            for r in raw_labels
            if r.get("regime_label")
        ]
    except Exception as exc:
        logger.warning("evaluate_feature: could not load regime labels: %s", exc)
        regime_label_dicts = []

    evaluator = FeatureEvaluator()
    result = evaluator.evaluate(
        series=series,
        regime_labels=regime_label_dicts,
        feature_name=inp.feature_name,
        leakage_risk=leakage_risk,
    )

    # Cache the eval result
    _FEATURE_EVAL_CACHE[inp.feature_name] = result

    passes = (
        result.f_statistic > REGISTRATION_THRESHOLD
        and result.leakage_risk != "high"
    )

    return EvaluateFeatureOutput(
        feature_name=inp.feature_name,
        f_statistic=result.f_statistic,
        regime_breakdown={k: float(v) for k, v in result.regime_breakdown.items()},
        leakage_risk=result.leakage_risk,
        passes_threshold=passes,
    )


# ---------------------------------------------------------------------------
# Tool 4: register_feature
# ---------------------------------------------------------------------------

async def register_feature(
    inp: RegisterFeatureInput,
    client: MedallionClient,  # noqa: ARG001
) -> RegisterFeatureOutput:
    """Register the feature to FeatureLibrary if it passes threshold."""
    from backend.deps import get_feature_library

    spec = _FEATURE_SPEC_CACHE.get(inp.feature_name)
    eval_result = _FEATURE_EVAL_CACHE.get(inp.feature_name)

    if spec is None or eval_result is None:
        return RegisterFeatureOutput(
            feature_name=inp.feature_name,
            registered=False,
            reason="spec or eval_result not found in cache — call propose_feature and evaluate_feature first",
        )

    library = get_feature_library()

    # Check if already exists
    if library.get(inp.feature_name) is not None:
        return RegisterFeatureOutput(
            feature_name=inp.feature_name,
            registered=False,
            reason="already_exists",
        )

    # Determine reason if it will be blocked
    from backend.features.feature_library import REGISTRATION_THRESHOLD

    if eval_result.leakage_risk == "high":
        return RegisterFeatureOutput(
            feature_name=inp.feature_name,
            registered=False,
            reason="leakage_blocked",
        )

    if eval_result.f_statistic <= REGISTRATION_THRESHOLD:
        return RegisterFeatureOutput(
            feature_name=inp.feature_name,
            registered=False,
            reason="below_threshold",
        )

    updated = library.register(spec, eval_result)

    reason = "registered" if updated.registered else "below_threshold"
    return RegisterFeatureOutput(
        feature_name=inp.feature_name,
        registered=updated.registered,
        reason=reason,
    )
