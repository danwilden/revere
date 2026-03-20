"""Feature library -- persist and query discovered features.

Registered features are stored in the metadata repository under the "features"
store name. A feature must pass both a minimum F-statistic threshold and a
leakage-risk check to be registered.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

REGISTRATION_THRESHOLD = 2.0  # minimum F-statistic for registration (strict >)


class FeatureLibrary:
    """Persist and query discovered feature specs and evaluation results.

    Storage: metadata repository "features" store, keyed by feature_name.
    Records use ``id = feature_name`` to satisfy LocalMetadataRepository._upsert
    which keys by ``record["id"]``.
    """

    _STORE = "features"

    def __init__(self, metadata_repo: Any) -> None:
        self._repo = metadata_repo

    def register(self, spec: Any, eval_result: Any) -> Any:
        """Attempt to register a feature. Returns updated eval result.

        Blocked if:
        - leakage_risk == "high" (regardless of F-statistic)
        - f_statistic <= REGISTRATION_THRESHOLD (strict >)
        - feature_name already exists in the library

        Parameters
        ----------
        spec:
            FeatureSpec Pydantic model with feature metadata and code.
        eval_result:
            FeatureEvalResult Pydantic model with evaluation statistics.

        Returns
        -------
        Updated FeatureEvalResult with ``registered`` set appropriately.
        """
        from backend.agents.tools.schemas import FeatureEvalResult

        feature_name = eval_result.feature_name

        # Check leakage risk
        if eval_result.leakage_risk == "high":
            logger.info(
                "Feature '%s' blocked: leakage_risk='high'", feature_name
            )
            return FeatureEvalResult(
                feature_name=feature_name,
                f_statistic=eval_result.f_statistic,
                regime_breakdown=eval_result.regime_breakdown,
                leakage_risk=eval_result.leakage_risk,
                registered=False,
            )

        # Check F-statistic threshold (strict >)
        if eval_result.f_statistic <= REGISTRATION_THRESHOLD:
            logger.info(
                "Feature '%s' blocked: f_statistic=%.3f <= threshold=%.1f",
                feature_name,
                eval_result.f_statistic,
                REGISTRATION_THRESHOLD,
            )
            return FeatureEvalResult(
                feature_name=feature_name,
                f_statistic=eval_result.f_statistic,
                regime_breakdown=eval_result.regime_breakdown,
                leakage_risk=eval_result.leakage_risk,
                registered=False,
            )

        # Check for duplicates
        existing = self.get(feature_name)
        if existing is not None:
            logger.info(
                "Feature '%s' blocked: already exists in library", feature_name
            )
            return FeatureEvalResult(
                feature_name=feature_name,
                f_statistic=eval_result.f_statistic,
                regime_breakdown=eval_result.regime_breakdown,
                leakage_risk=eval_result.leakage_risk,
                registered=False,
            )

        # Build and persist the record
        now = datetime.now(tz=timezone.utc).isoformat()
        record: dict[str, Any] = {
            "id": feature_name,  # required by _upsert keying
            "feature_name": feature_name,
            "family": getattr(spec, "family", ""),
            "formula_description": getattr(spec, "formula_description", ""),
            "lookback_bars": getattr(spec, "lookback_bars", 0),
            "dependency_columns": getattr(spec, "dependency_columns", []),
            "transformation": getattr(spec, "transformation", ""),
            "expected_intuition": getattr(spec, "expected_intuition", ""),
            "leakage_risk": eval_result.leakage_risk,
            "code": getattr(spec, "code", ""),
            "f_statistic": eval_result.f_statistic,
            "regime_breakdown": eval_result.regime_breakdown,
            "registered": True,
            "registered_at": now,
        }
        self._repo._upsert(self._STORE, record)

        logger.info(
            "Feature '%s' registered (F=%.3f)", feature_name, eval_result.f_statistic
        )
        return FeatureEvalResult(
            feature_name=feature_name,
            f_statistic=eval_result.f_statistic,
            regime_breakdown=eval_result.regime_breakdown,
            leakage_risk=eval_result.leakage_risk,
            registered=True,
        )

    # ------------------------------------------------------------------
    # API-facing interface (used by apps/api/routes/features.py)
    # ------------------------------------------------------------------

    def upsert(
        self,
        result: Any,  # FeatureEvalResult from backend.schemas.requests
        instrument: str,
        timeframe: str,
        eval_start: str,
        eval_end: str,
    ) -> Any:
        """Insert or update a feature record keyed by name.

        On first insert: assign a new UUID id and set discovered_at = now.
        On subsequent upsert: preserve id and discovered_at; refresh all
        score fields and bump last_evaluated_at.

        Records are stored with ``id = feature_name`` (same convention as the
        agent-facing register() method) so that _upsert keying is consistent.

        Returns a FeatureSpec (backend.schemas.requests) built from the record.
        """
        import uuid as _uuid
        from backend.schemas.requests import FeatureSpec

        now = datetime.now(tz=timezone.utc).isoformat()
        name = result.name

        existing = self._repo._get(self._STORE, name)
        if existing is None:
            record: dict[str, Any] = {
                "id": name,  # key used by _upsert/_get/_list
                "name": name,
                "feature_name": name,  # legacy field kept for agent compatibility
                "family": result.family,
                "description": result.description,
                "f_statistic": result.f_statistic,
                "p_value": result.p_value,
                "leakage_score": result.leakage_score,
                "regime_discriminability": result.regime_discriminability,
                "correlation_with_returns": result.correlation_with_returns,
                "evaluation_notes": result.evaluation_notes,
                "discovery_run_id": result.discovery_run_id,
                "instrument": instrument,
                "timeframe": timeframe,
                "eval_start": eval_start,
                "eval_end": eval_end,
                "discovered_at": now,
                "last_evaluated_at": now,
            }
        else:
            record = dict(existing)
            record.update({
                "family": result.family,
                "description": result.description,
                "f_statistic": result.f_statistic,
                "p_value": result.p_value,
                "leakage_score": result.leakage_score,
                "regime_discriminability": result.regime_discriminability,
                "correlation_with_returns": result.correlation_with_returns,
                "evaluation_notes": result.evaluation_notes,
                "discovery_run_id": result.discovery_run_id,
                "instrument": instrument,
                "timeframe": timeframe,
                "eval_start": eval_start,
                "eval_end": eval_end,
                "last_evaluated_at": now,
            })

        self._repo._upsert(self._STORE, record)
        return FeatureSpec.model_validate(record)

    def get_by_name(self, name: str) -> Any:
        """Return the FeatureSpec for the given canonical name.

        Raises KeyError if not found. This is the API route's lookup method.
        """
        from backend.schemas.requests import FeatureSpec

        raw = self._repo._get(self._STORE, name)
        if raw is None:
            raise KeyError(name)
        return FeatureSpec.model_validate(raw)

    def list_features(
        self,
        family: str | None = None,
        max_leakage: float | None = None,
        min_f_statistic: float | None = None,
        limit: int = 50,
    ) -> list[Any]:
        """Return FeatureSpec records, newest discovered_at first.

        Filters applied in order: family, max_leakage, min_f_statistic.
        Features with None for a filter target field are excluded when that
        filter is active.
        """
        from backend.schemas.requests import FeatureSpec

        records: list[dict] = self._repo._list(self._STORE)

        if family is not None:
            records = [r for r in records if r.get("family") == family]

        if max_leakage is not None:
            records = [
                r for r in records
                if r.get("leakage_score") is not None
                and r["leakage_score"] <= max_leakage
            ]

        if min_f_statistic is not None:
            records = [
                r for r in records
                if r.get("f_statistic") is not None
                and r["f_statistic"] >= min_f_statistic
            ]

        records.sort(
            key=lambda r: str(r.get("discovered_at", "")),
            reverse=True,
        )
        records = records[:limit]
        return [FeatureSpec.model_validate(r) for r in records]

    def list_by_discovery_run(self, discovery_run_id: str) -> list[Any]:
        """Return all FeatureEvalResult-shaped records from a specific discovery run.

        Used by GET /api/features/discover/{job_id} to populate
        feature_eval_results after SUCCEEDED.
        """
        from backend.schemas.requests import FeatureEvalResult

        all_records: list[dict] = self._repo._list(self._STORE)
        results = []
        for record in all_records:
            if record.get("discovery_run_id") == discovery_run_id:
                results.append(FeatureEvalResult(
                    name=record.get("name") or record.get("feature_name", ""),
                    family=record.get("family", ""),
                    description=record.get("description", ""),
                    f_statistic=record.get("f_statistic"),
                    p_value=record.get("p_value"),
                    leakage_score=record.get("leakage_score"),
                    regime_discriminability=record.get("regime_discriminability"),
                    correlation_with_returns=record.get("correlation_with_returns"),
                    evaluation_notes=record.get("evaluation_notes", ""),
                    discovery_run_id=record["discovery_run_id"],
                ))
        return results

    # ------------------------------------------------------------------
    # Agent-facing interface (used by backend/agents/tools/feature.py)
    # ------------------------------------------------------------------

    def get(self, feature_name: str) -> dict[str, Any] | None:
        """Retrieve a registered feature by name. Returns None if not found."""
        return self._repo._get(self._STORE, feature_name)

    def list_all(self) -> list[dict[str, Any]]:
        """All registered features sorted by f_statistic descending."""
        records = self._repo._list(self._STORE)
        records.sort(key=lambda r: r.get("f_statistic", 0.0), reverse=True)
        return records

    def query(
        self,
        family: str | None = None,
        min_f_statistic: float | None = None,
        leakage_risk: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query registered features with optional filters.

        Parameters
        ----------
        family:
            Filter by feature family (e.g. "momentum", "volatility").
        min_f_statistic:
            Only return features with f_statistic >= this value.
        leakage_risk:
            Filter by leakage risk level.

        Returns
        -------
        Filtered list sorted by f_statistic descending.
        """
        records = self._repo._list(self._STORE)

        if family is not None:
            records = [r for r in records if r.get("family") == family]
        if min_f_statistic is not None:
            records = [
                r for r in records if r.get("f_statistic", 0.0) >= min_f_statistic
            ]
        if leakage_risk is not None:
            records = [r for r in records if r.get("leakage_risk") == leakage_risk]

        records.sort(key=lambda r: r.get("f_statistic", 0.0), reverse=True)
        return records
