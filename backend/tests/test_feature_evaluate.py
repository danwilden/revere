"""Tests for backend.features.evaluate -- ANOVA F-statistic evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.features.evaluate import FeatureEvaluator


def _make_regime_labels(
    index: pd.DatetimeIndex,
    labels: list[str],
) -> list[dict]:
    """Build regime label dicts from an index and label list."""
    return [
        {"timestamp_utc": ts.isoformat(), "label": lbl}
        for ts, lbl in zip(index, labels)
    ]


class TestFeatureEvaluator:
    def test_two_regime_clear_separation(self) -> None:
        """Two regimes with clearly different means should produce F > 0."""
        idx = pd.date_range("2024-01-01", periods=200, freq="h")
        rng = np.random.default_rng(42)

        # First 100 bars: "BULL" regime with high values
        # Last 100 bars: "BEAR" regime with low values
        values = np.concatenate([
            rng.normal(10.0, 1.0, 100),
            rng.normal(0.0, 1.0, 100),
        ])
        series = pd.Series(values, index=idx, name="test_feature")
        labels = ["TREND_BULL_LOW_VOL"] * 100 + ["TREND_BEAR_LOW_VOL"] * 100
        regime_labels = _make_regime_labels(idx, labels)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(
            series, regime_labels, feature_name="test_sep", leakage_risk="none"
        )

        assert result.f_statistic > 0.0
        assert result.f_statistic > 100.0  # should be very high with clear separation
        assert result.registered is False
        assert "TREND_BULL_LOW_VOL" in result.regime_breakdown
        assert "TREND_BEAR_LOW_VOL" in result.regime_breakdown
        assert result.feature_name == "test_sep"
        assert result.leakage_risk == "none"

    def test_single_regime_returns_zero(self) -> None:
        """Only one regime class should return F=0.0."""
        idx = pd.date_range("2024-01-01", periods=50, freq="h")
        series = pd.Series(np.ones(50), index=idx)
        labels = ["RANGE_MEAN_REVERT"] * 50
        regime_labels = _make_regime_labels(idx, labels)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(series, regime_labels, feature_name="single")

        assert result.f_statistic == 0.0

    def test_empty_regime_labels_returns_zero(self) -> None:
        """Empty regime labels should return F=0.0."""
        idx = pd.date_range("2024-01-01", periods=50, freq="h")
        series = pd.Series(np.ones(50), index=idx)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(series, [], feature_name="empty")

        assert result.f_statistic == 0.0
        assert result.regime_breakdown == {}

    def test_nan_values_dropped_before_grouping(self) -> None:
        """NaN values in the series should be excluded from ANOVA groups."""
        idx = pd.date_range("2024-01-01", periods=100, freq="h")
        rng = np.random.default_rng(99)

        values = np.concatenate([
            rng.normal(10.0, 1.0, 50),
            rng.normal(0.0, 1.0, 50),
        ])
        # Inject some NaN
        values[0] = np.nan
        values[50] = np.nan
        series = pd.Series(values, index=idx)
        labels = ["BULL"] * 50 + ["BEAR"] * 50
        regime_labels = _make_regime_labels(idx, labels)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(series, regime_labels, feature_name="nan_test")

        # Should still compute F > 0 despite NaN
        assert result.f_statistic > 0.0

    def test_regime_with_one_obs_excluded(self) -> None:
        """A regime class with only 1 observation should be excluded from ANOVA."""
        idx = pd.date_range("2024-01-01", periods=51, freq="h")
        rng = np.random.default_rng(7)

        values = np.concatenate([
            rng.normal(10.0, 1.0, 50),
            [5.0],  # single observation for "CHOPPY"
        ])
        series = pd.Series(values, index=idx)
        labels = ["BULL"] * 50 + ["CHOPPY"]
        regime_labels = _make_regime_labels(idx, labels)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(series, regime_labels, feature_name="one_obs")

        # Only 1 valid group (BULL has 50 obs, CHOPPY has 1) -> F=0.0
        assert result.f_statistic == 0.0

    def test_leakage_risk_passthrough(self) -> None:
        """Leakage risk string should be passed through to result."""
        idx = pd.date_range("2024-01-01", periods=20, freq="h")
        series = pd.Series(np.ones(20), index=idx)
        regime_labels = _make_regime_labels(idx, ["A"] * 20)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(
            series, regime_labels, feature_name="leak", leakage_risk="high"
        )

        assert result.leakage_risk == "high"

    def test_three_regimes(self) -> None:
        """Three distinct regimes should compute correctly."""
        idx = pd.date_range("2024-01-01", periods=150, freq="h")
        rng = np.random.default_rng(123)

        values = np.concatenate([
            rng.normal(10.0, 1.0, 50),
            rng.normal(0.0, 1.0, 50),
            rng.normal(-10.0, 1.0, 50),
        ])
        series = pd.Series(values, index=idx)
        labels = ["BULL"] * 50 + ["RANGE"] * 50 + ["BEAR"] * 50
        regime_labels = _make_regime_labels(idx, labels)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(series, regime_labels, feature_name="three")

        assert result.f_statistic > 0.0
        assert len(result.regime_breakdown) == 3

    def test_regime_breakdown_contains_means(self) -> None:
        """regime_breakdown should map label to mean feature value."""
        idx = pd.date_range("2024-01-01", periods=6, freq="h")
        series = pd.Series([1.0, 2.0, 3.0, 10.0, 20.0, 30.0], index=idx)
        labels = ["A", "A", "A", "B", "B", "B"]
        regime_labels = _make_regime_labels(idx, labels)

        evaluator = FeatureEvaluator()
        result = evaluator.evaluate(series, regime_labels, feature_name="means")

        assert abs(result.regime_breakdown["A"] - 2.0) < 0.01
        assert abs(result.regime_breakdown["B"] - 20.0) < 0.01
