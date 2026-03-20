"""Feature evaluation via ANOVA F-statistic across HMM regime classes.

Given a feature Series and a list of regime label dicts, computes a one-way
ANOVA F-statistic to measure how well the feature discriminates between
regime classes. Higher F-statistics indicate stronger regime separation.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from scipy.stats import f_oneway

from backend.agents.tools.schemas import FeatureEvalResult

logger = logging.getLogger(__name__)


class FeatureEvaluator:
    """Evaluate a feature's discriminative power across HMM regime labels."""

    def evaluate(
        self,
        series: pd.Series,
        regime_labels: list[dict[str, Any]],
        feature_name: str = "",
        leakage_risk: str = "none",
    ) -> FeatureEvalResult:
        """Run one-way ANOVA grouped by regime_label.

        Parameters
        ----------
        series:
            Feature values with DatetimeIndex.
        regime_labels:
            List of dicts with keys ``timestamp_utc`` and ``label``.
        feature_name:
            Name of the feature being evaluated.
        leakage_risk:
            Leakage risk level from the FeatureSpec.

        Returns
        -------
        FeatureEvalResult with ``registered=False`` always (registration is
        decided by FeatureLibrary).
        """
        labels_df = pd.DataFrame(regime_labels)
        if labels_df.empty or "label" not in labels_df.columns:
            logger.warning("No regime labels provided -- returning F=0.0")
            return FeatureEvalResult(
                feature_name=feature_name,
                f_statistic=0.0,
                regime_breakdown={},
                leakage_risk=leakage_risk,
                registered=False,
            )

        labels_df["timestamp_utc"] = pd.to_datetime(labels_df["timestamp_utc"])
        labels_df = labels_df.set_index("timestamp_utc")

        # Join series to labels on timestamp
        joined = pd.DataFrame({"value": series}).join(labels_df["label"], how="inner")
        joined = joined.dropna(subset=["value"])

        # Build regime breakdown and collect groups for ANOVA
        regime_breakdown: dict[str, float] = {}
        groups: list[Any] = []
        for label, group in joined.groupby("label"):
            vals = group["value"].dropna().values
            regime_breakdown[str(label)] = float(group["value"].mean())
            if len(vals) >= 2:
                groups.append(vals)

        if len(groups) < 2:
            logger.warning(
                "Fewer than 2 regime classes with >= 2 obs for '%s' -- returning F=0.0",
                feature_name,
            )
            return FeatureEvalResult(
                feature_name=feature_name,
                f_statistic=0.0,
                regime_breakdown=regime_breakdown,
                leakage_risk=leakage_risk,
                registered=False,
            )

        stat, _ = f_oneway(*groups)
        f_stat = float(stat) if not (stat != stat) else 0.0  # handle NaN

        return FeatureEvalResult(
            feature_name=feature_name,
            f_statistic=f_stat,
            regime_breakdown=regime_breakdown,
            leakage_risk=leakage_risk,
            registered=False,
        )
