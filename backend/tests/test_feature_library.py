"""Tests for backend.features.feature_library -- feature persistence and gating."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from backend.agents.tools.schemas import FeatureEvalResult, FeatureSpec
from backend.data.local_metadata import LocalMetadataRepository
from backend.features.feature_library import REGISTRATION_THRESHOLD, FeatureLibrary


def _make_spec(
    name: str = "test_feature",
    family: str = "momentum",
    leakage_risk: str = "none",
) -> FeatureSpec:
    return FeatureSpec(
        name=name,
        family=family,
        formula_description="test formula",
        lookback_bars=20,
        dependency_columns=["close"],
        transformation="rolling_mean",
        expected_intuition="test intuition",
        leakage_risk=leakage_risk,
        code="result = df['close'].rolling(20).mean().bfill()",
    )


def _make_eval(
    feature_name: str = "test_feature",
    f_statistic: float = 5.0,
    leakage_risk: str = "none",
) -> FeatureEvalResult:
    return FeatureEvalResult(
        feature_name=feature_name,
        f_statistic=f_statistic,
        regime_breakdown={"BULL": 1.0, "BEAR": -1.0},
        leakage_risk=leakage_risk,
        registered=False,
    )


@pytest.fixture
def library(tmp_path: Path) -> FeatureLibrary:
    repo = LocalMetadataRepository(tmp_path / "metadata")
    return FeatureLibrary(repo)


class TestRegistrationThreshold:
    def test_threshold_is_module_constant(self) -> None:
        assert REGISTRATION_THRESHOLD == 2.0


class TestRegister:
    def test_successful_registration(self, library: FeatureLibrary) -> None:
        spec = _make_spec()
        eval_result = _make_eval(f_statistic=5.0)

        result = library.register(spec, eval_result)

        assert result.registered is True
        assert result.f_statistic == 5.0

        # Verify persisted
        stored = library.get("test_feature")
        assert stored is not None
        assert stored["feature_name"] == "test_feature"
        assert stored["family"] == "momentum"
        assert stored["registered"] is True

    def test_leakage_high_blocked(self, library: FeatureLibrary) -> None:
        """leakage_risk='high' blocks registration regardless of F-statistic."""
        spec = _make_spec(leakage_risk="high")
        eval_result = _make_eval(f_statistic=100.0, leakage_risk="high")

        result = library.register(spec, eval_result)

        assert result.registered is False
        assert library.get("test_feature") is None

    def test_f_below_threshold_blocked(self, library: FeatureLibrary) -> None:
        """F-statistic below threshold should be blocked."""
        spec = _make_spec()
        eval_result = _make_eval(f_statistic=1.5)

        result = library.register(spec, eval_result)

        assert result.registered is False

    def test_f_at_threshold_blocked(self, library: FeatureLibrary) -> None:
        """F-statistic exactly at threshold (2.0) should be blocked (strict >)."""
        spec = _make_spec()
        eval_result = _make_eval(f_statistic=2.0)

        result = library.register(spec, eval_result)

        assert result.registered is False

    def test_f_just_above_threshold_registers(self, library: FeatureLibrary) -> None:
        """F-statistic = 2.1 should register."""
        spec = _make_spec()
        eval_result = _make_eval(f_statistic=2.1)

        result = library.register(spec, eval_result)

        assert result.registered is True

    def test_duplicate_blocked(self, library: FeatureLibrary) -> None:
        """Second registration of same feature_name should be blocked."""
        spec = _make_spec()
        eval_result = _make_eval(f_statistic=5.0)

        result1 = library.register(spec, eval_result)
        assert result1.registered is True

        result2 = library.register(spec, eval_result)
        assert result2.registered is False

    def test_leakage_low_allowed(self, library: FeatureLibrary) -> None:
        """leakage_risk='low' should be allowed if F is high enough."""
        spec = _make_spec(leakage_risk="low")
        eval_result = _make_eval(f_statistic=5.0, leakage_risk="low")

        result = library.register(spec, eval_result)

        assert result.registered is True

    def test_leakage_medium_allowed(self, library: FeatureLibrary) -> None:
        """leakage_risk='medium' should be allowed if F is high enough."""
        spec = _make_spec(leakage_risk="medium")
        eval_result = _make_eval(f_statistic=5.0, leakage_risk="medium")

        result = library.register(spec, eval_result)

        assert result.registered is True


class TestGet:
    def test_get_existing(self, library: FeatureLibrary) -> None:
        library.register(_make_spec(), _make_eval(f_statistic=5.0))
        stored = library.get("test_feature")
        assert stored is not None
        assert stored["f_statistic"] == 5.0

    def test_get_nonexistent(self, library: FeatureLibrary) -> None:
        assert library.get("nonexistent") is None


class TestListAll:
    def test_empty_library(self, library: FeatureLibrary) -> None:
        assert library.list_all() == []

    def test_sorted_by_f_descending(self, library: FeatureLibrary) -> None:
        library.register(
            _make_spec(name="low_f"), _make_eval(feature_name="low_f", f_statistic=3.0)
        )
        library.register(
            _make_spec(name="high_f"),
            _make_eval(feature_name="high_f", f_statistic=10.0),
        )
        library.register(
            _make_spec(name="mid_f"), _make_eval(feature_name="mid_f", f_statistic=5.0)
        )

        results = library.list_all()
        assert len(results) == 3
        assert results[0]["feature_name"] == "high_f"
        assert results[1]["feature_name"] == "mid_f"
        assert results[2]["feature_name"] == "low_f"


class TestQuery:
    def test_filter_by_family(self, library: FeatureLibrary) -> None:
        library.register(
            _make_spec(name="feat_mom", family="momentum"),
            _make_eval(feature_name="feat_mom", f_statistic=5.0),
        )
        library.register(
            _make_spec(name="feat_vol", family="volatility"),
            _make_eval(feature_name="feat_vol", f_statistic=5.0),
        )

        results = library.query(family="momentum")
        assert len(results) == 1
        assert results[0]["feature_name"] == "feat_mom"

    def test_filter_by_min_f_statistic(self, library: FeatureLibrary) -> None:
        library.register(
            _make_spec(name="low"), _make_eval(feature_name="low", f_statistic=3.0)
        )
        library.register(
            _make_spec(name="high"), _make_eval(feature_name="high", f_statistic=10.0)
        )

        results = library.query(min_f_statistic=5.0)
        assert len(results) == 1
        assert results[0]["feature_name"] == "high"

    def test_filter_by_leakage_risk(self, library: FeatureLibrary) -> None:
        library.register(
            _make_spec(name="safe", leakage_risk="none"),
            _make_eval(feature_name="safe", f_statistic=5.0, leakage_risk="none"),
        )
        library.register(
            _make_spec(name="risky", leakage_risk="low"),
            _make_eval(feature_name="risky", f_statistic=5.0, leakage_risk="low"),
        )

        results = library.query(leakage_risk="none")
        assert len(results) == 1
        assert results[0]["feature_name"] == "safe"

    def test_combined_filters(self, library: FeatureLibrary) -> None:
        library.register(
            _make_spec(name="a", family="momentum"),
            _make_eval(feature_name="a", f_statistic=3.0),
        )
        library.register(
            _make_spec(name="b", family="momentum"),
            _make_eval(feature_name="b", f_statistic=10.0),
        )
        library.register(
            _make_spec(name="c", family="volatility"),
            _make_eval(feature_name="c", f_statistic=10.0),
        )

        results = library.query(family="momentum", min_f_statistic=5.0)
        assert len(results) == 1
        assert results[0]["feature_name"] == "b"

    def test_no_filters_returns_all(self, library: FeatureLibrary) -> None:
        library.register(
            _make_spec(name="x"), _make_eval(feature_name="x", f_statistic=5.0)
        )
        library.register(
            _make_spec(name="y"), _make_eval(feature_name="y", f_statistic=3.0)
        )

        results = library.query()
        assert len(results) == 2
