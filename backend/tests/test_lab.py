"""Tests for Phase 5B lab modules — experiment registry, evaluation, mutation.

All tests are deterministic with in-memory LocalMetadataRepository.
No real network calls, no random state dependency (mutation tests run 20 iterations).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.lab.evaluation import (
    ExperimentScore,
    compare_experiments,
    score_experiment,
)
from backend.lab.experiment_registry import (
    ExperimentRecord,
    ExperimentRegistry,
    ExperimentStatus,
)
from backend.lab.mutation import (
    inject_regime_filter,
    perturb_parameters,
    substitute_rule,
)
from backend.data.repositories import LocalMetadataRepository


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_metadata_repo(tmp_path: Path) -> LocalMetadataRepository:
    """Create a fresh LocalMetadataRepository for each test."""
    return LocalMetadataRepository(tmp_path / "metadata")


@pytest.fixture
def registry(tmp_metadata_repo: LocalMetadataRepository) -> ExperimentRegistry:
    """Create a fresh ExperimentRegistry backed by tmp repository."""
    return ExperimentRegistry(tmp_metadata_repo)


# ============================================================================
# ExperimentRegistry Tests
# ============================================================================

class TestExperimentRegistryCreate:
    """test_registry_create_returns_record"""
    def test_create_returns_record_with_correct_fields(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="test-session-001",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        assert isinstance(record, ExperimentRecord)
        assert record.id is not None and len(record.id) > 0
        assert record.status == ExperimentStatus.PENDING
        assert record.instrument == "EUR_USD"
        assert record.timeframe == "H4"
        assert record.session_id == "test-session-001"
        assert record.created_at is not None
        assert record.updated_at is not None


class TestExperimentRegistryRoundTrip:
    """test_registry_get_round_trip"""
    def test_create_then_get_returns_identical_record(self, registry: ExperimentRegistry):
        created = registry.create(
            session_id="test-session-002",
            instrument="GBP_USD",
            timeframe="H1",
            test_start="2023-06-01",
            test_end="2023-12-31",
            task="generate_seed",
            requested_by="test_user",
        )
        retrieved = registry.get(created.id)
        assert retrieved.id == created.id
        assert retrieved.instrument == created.instrument
        assert retrieved.timeframe == created.timeframe
        assert retrieved.session_id == created.session_id
        assert retrieved.task == created.task
        assert retrieved.requested_by == created.requested_by
        assert retrieved.status == created.status


class TestExperimentRegistryUpdateStatus:
    """test_registry_update_status_merges_kwargs"""
    def test_update_status_merges_kwargs_non_none(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="test-session-003",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        # First update: set hypothesis
        updated1 = registry.update_status(
            record.id,
            ExperimentStatus.RUNNING,
            hypothesis="Test hypothesis",
        )
        assert updated1.hypothesis == "Test hypothesis"
        assert updated1.status == ExperimentStatus.RUNNING

        # Second update: set strategy_id but leave hypothesis unchanged
        updated2 = registry.update_status(
            record.id,
            ExperimentStatus.RUNNING,
            strategy_id="strat-123",
            hypothesis=None,  # explicitly None
        )
        assert updated2.hypothesis == "Test hypothesis"  # Not overwritten
        assert updated2.strategy_id == "strat-123"

    def test_update_status_always_updates_updated_at(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="test-session-004",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        original_updated_at = record.updated_at

        # Wait a tick and update
        import time
        time.sleep(0.01)  # Ensure timestamp changes

        updated = registry.update_status(
            record.id,
            ExperimentStatus.RUNNING,
            hypothesis="new hypothesis",
        )
        assert updated.updated_at != original_updated_at
        assert updated.updated_at > original_updated_at


class TestExperimentRegistryList:
    """test_registry_list_recent_sorted_descending"""
    def test_list_recent_sorted_descending(self, registry: ExperimentRegistry):
        import time
        # Create 3 records with slight delays
        ids = []
        for i in range(3):
            record = registry.create(
                session_id=f"test-session-{i}",
                instrument="EUR_USD",
                timeframe="H4",
                test_start="2023-01-01",
                test_end="2024-01-01",
            )
            ids.append(record.id)
            time.sleep(0.01)  # Ensure created_at times differ

        listed = registry.list_recent(limit=10)
        assert len(listed) == 3
        # Most recent first (reverse order of creation)
        assert listed[0].id == ids[2]
        assert listed[1].id == ids[1]
        assert listed[2].id == ids[0]

    def test_list_recent_filters_by_instrument(self, registry: ExperimentRegistry):
        # Create EUR_USD and GBP_USD records
        eur = registry.create(
            session_id="eur-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        gbp = registry.create(
            session_id="gbp-session",
            instrument="GBP_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        # Create another EUR
        eur2 = registry.create(
            session_id="eur-session-2",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )

        eur_records = registry.list_recent(instrument="EUR_USD", limit=10)
        assert len(eur_records) == 2
        assert all(r.instrument == "EUR_USD" for r in eur_records)

        gbp_records = registry.list_recent(instrument="GBP_USD", limit=10)
        assert len(gbp_records) == 1
        assert gbp_records[0].instrument == "GBP_USD"

    def test_list_recent_filters_by_status(self, registry: ExperimentRegistry):
        # Create 2 PENDING records
        r1 = registry.create(
            session_id="session-1",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        r2 = registry.create(
            session_id="session-2",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        # Update r1 to RUNNING
        registry.update_status(r1.id, ExperimentStatus.RUNNING)

        running = registry.list_recent(status=ExperimentStatus.RUNNING, limit=10)
        assert len(running) == 1
        assert running[0].id == r1.id

        pending = registry.list_recent(status=ExperimentStatus.PENDING, limit=10)
        assert len(pending) == 1
        assert pending[0].id == r2.id


class TestExperimentRegistryLineage:
    """test_registry_get_lineage_returns_chain"""
    def test_get_lineage_returns_chain(self, registry: ExperimentRegistry):
        # Create parent
        parent = registry.create(
            session_id="parent-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
            generation=0,
        )
        # Create child
        child = registry.create(
            session_id="child-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
            parent_id=parent.id,
            generation=1,
        )
        # Create grandchild
        grandchild = registry.create(
            session_id="grandchild-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
            parent_id=child.id,
            generation=2,
        )

        lineage = registry.get_lineage(grandchild.id)
        assert len(lineage) == 3
        assert lineage[0].id == parent.id
        assert lineage[1].id == child.id
        assert lineage[2].id == grandchild.id

    def test_get_lineage_single_node(self, registry: ExperimentRegistry):
        # Create root with no parent
        root = registry.create(
            session_id="root-session",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        lineage = registry.get_lineage(root.id)
        assert len(lineage) == 1
        assert lineage[0].id == root.id


class TestExperimentRegistryErrors:
    """test_registry_get_raises_key_error_on_missing"""
    def test_get_raises_key_error_on_missing(self, registry: ExperimentRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.get("nonexistent-id-12345")


# ============================================================================
# Evaluation Tests
# ============================================================================

class TestScoreExperiment:
    """test_score_experiment_all_metrics_present"""
    def test_score_experiment_all_metrics_present(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="score-session-1",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        # Update with metrics
        registry.update_status(
            record.id,
            ExperimentStatus.SUCCEEDED,
            sharpe=1.0,
            max_drawdown_pct=-10.0,
            total_trades=50,
            win_rate=0.55,
        )
        updated = registry.get(record.id)

        score = score_experiment(updated)
        assert isinstance(score, ExperimentScore)
        assert score.experiment_id == record.id
        # Composite should be >= 0.68 with these strong metrics (0.40*0.5 + 0.30*0.8 + 0.15*1.0 + 0.15*0.667 = 0.69)
        assert score.composite_score >= 0.68
        assert score.sharpe == 1.0
        assert score.max_drawdown_pct == -10.0
        assert score.total_trades == 50
        assert score.win_rate == 0.55

    def test_score_experiment_all_none_metrics(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="score-session-2",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        # No metrics set — all None
        retrieved = registry.get(record.id)

        score = score_experiment(retrieved)
        assert score.composite_score == 0.0
        assert score.passed_minimum_gates is False
        assert len(score.gate_failures) > 0

    def test_score_experiment_gate_failure_trades(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="score-session-3",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        registry.update_status(
            record.id,
            ExperimentStatus.SUCCEEDED,
            sharpe=0.5,
            max_drawdown_pct=-10.0,
            total_trades=5,  # Below MIN_TRADE_COUNT (20)
            win_rate=0.50,
        )
        updated = registry.get(record.id)

        score = score_experiment(updated)
        assert score.passed_minimum_gates is False
        gate_failure_texts = " ".join(score.gate_failures)
        assert "total_trades" in gate_failure_texts

    def test_score_experiment_gate_failure_drawdown(self, registry: ExperimentRegistry):
        record = registry.create(
            session_id="score-session-4",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        registry.update_status(
            record.id,
            ExperimentStatus.SUCCEEDED,
            sharpe=0.5,
            max_drawdown_pct=-40.0,  # Worse than MAX_DRAWDOWN_PCT (-30.0)
            total_trades=25,
            win_rate=0.50,
        )
        updated = registry.get(record.id)

        score = score_experiment(updated)
        assert score.passed_minimum_gates is False
        gate_failure_texts = " ".join(score.gate_failures)
        assert "max_drawdown_pct" in gate_failure_texts


class TestCompareExperiments:
    """test_compare_experiments_sorted_descending"""
    def test_compare_experiments_sorted_descending(self, registry: ExperimentRegistry):
        # Create 3 records with different sharpe values
        r1 = registry.create(
            session_id="cmp-session-1",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        r2 = registry.create(
            session_id="cmp-session-2",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )
        r3 = registry.create(
            session_id="cmp-session-3",
            instrument="EUR_USD",
            timeframe="H4",
            test_start="2023-01-01",
            test_end="2024-01-01",
        )

        # Update with metrics
        registry.update_status(r1.id, ExperimentStatus.SUCCEEDED, sharpe=0.3, total_trades=25, max_drawdown_pct=-15.0, win_rate=0.45)
        registry.update_status(r2.id, ExperimentStatus.SUCCEEDED, sharpe=1.5, total_trades=40, max_drawdown_pct=-10.0, win_rate=0.55)
        registry.update_status(r3.id, ExperimentStatus.SUCCEEDED, sharpe=0.8, total_trades=30, max_drawdown_pct=-12.0, win_rate=0.50)

        records = [registry.get(r.id) for r in [r1, r2, r3]]
        scores = compare_experiments(records)

        assert len(scores) == 3
        # Best (highest composite score) should be r2
        assert scores[0].experiment_id == r2.id
        # Next should be r3
        assert scores[1].experiment_id == r3.id
        # Worst should be r1
        assert scores[2].experiment_id == r1.id


# ============================================================================
# Mutation Tests
# ============================================================================

class TestPerturbParameters:
    """test_perturb_parameters_* tests"""
    def test_perturb_parameters_differs_from_input(self):
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        perturbed = perturb_parameters(definition, magnitude=0.2)
        # At least one ATR multiplier should differ (with very high probability)
        assert (
            perturbed["stop_atr_multiplier"] != definition["stop_atr_multiplier"]
            or perturbed["take_profit_atr_multiplier"] != definition["take_profit_atr_multiplier"]
        )
        # Original should be unchanged
        assert definition["stop_atr_multiplier"] == 2.0
        assert definition["take_profit_atr_multiplier"] == 3.0

    def test_perturb_parameters_stop_less_than_target_invariant(self):
        """Run 20 iterations to ensure stop < take_profit always holds."""
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        for _ in range(20):
            perturbed = perturb_parameters(definition, magnitude=0.2)
            assert perturbed["stop_atr_multiplier"] < perturbed["take_profit_atr_multiplier"]

    def test_perturb_parameters_magnitude_clamped(self):
        """Magnitude -1.0 should be clamped to 0.01 without error."""
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        # Should not raise, magnitude is clamped internally
        perturbed = perturb_parameters(definition, magnitude=-1.0)
        assert perturbed is not None

    def test_perturb_parameters_position_size_unchanged(self):
        """position_size_units should always be 1000."""
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 500,  # Non-standard
        }
        for _ in range(10):
            perturbed = perturb_parameters(definition, magnitude=0.2)
            assert perturbed["position_size_units"] == 1000


class TestSubstituteRule:
    """test_substitute_rule_* tests"""
    def test_substitute_rule_replaces_correct_leaf(self):
        definition = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                    {"field": "adx_14", "op": "gt", "value": 20},
                ]
            },
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        new_rule = {"field": "ema_slope_20", "op": "gt", "value": 0.0}
        substituted = substitute_rule(definition, rule_index=0, new_rule=new_rule, target="entry_long")

        # First leaf should be replaced
        assert substituted["entry_long"]["all"][0] == new_rule
        # Second leaf should remain
        assert substituted["entry_long"]["all"][1]["field"] == "adx_14"
        # Original unchanged
        assert definition["entry_long"]["all"][0]["field"] == "rsi_14"

    def test_substitute_rule_raises_index_error(self):
        definition = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                ]
            },
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        new_rule = {"field": "ema_slope_20", "op": "gt", "value": 0.0}
        with pytest.raises(IndexError):
            substitute_rule(definition, rule_index=10, new_rule=new_rule, target="entry_long")

    def test_substitute_rule_raises_key_error(self):
        definition = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                ]
            },
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        new_rule = {"field": "ema_slope_20", "op": "gt", "value": 0.0}
        with pytest.raises(KeyError):
            substitute_rule(definition, rule_index=0, new_rule=new_rule, target="entry_short")


class TestInjectRegimeFilter:
    """test_inject_regime_filter_* tests"""
    def test_inject_regime_filter_wraps_non_all(self):
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        injected = inject_regime_filter(definition, "TREND_BULL_LOW_VOL", target="entry_long")

        # Should be wrapped in an "all" composite
        assert "all" in injected["entry_long"]
        assert len(injected["entry_long"]["all"]) == 2
        # First should be regime filter
        assert injected["entry_long"]["all"][0]["field"] == "regime_label"
        assert injected["entry_long"]["all"][0]["value"] == "TREND_BULL_LOW_VOL"
        # Second should be original
        assert injected["entry_long"]["all"][1]["field"] == "rsi_14"

    def test_inject_regime_filter_prepends_to_all(self):
        definition = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                    {"field": "adx_14", "op": "gt", "value": 20},
                ]
            },
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        injected = inject_regime_filter(definition, "TREND_BULL_HIGH_VOL", target="entry_long")

        # Should have 3 items now
        assert len(injected["entry_long"]["all"]) == 3
        # First should be regime filter
        assert injected["entry_long"]["all"][0]["field"] == "regime_label"
        assert injected["entry_long"]["all"][0]["value"] == "TREND_BULL_HIGH_VOL"
        # Rest should be original in order
        assert injected["entry_long"]["all"][1]["field"] == "rsi_14"
        assert injected["entry_long"]["all"][2]["field"] == "adx_14"

    def test_inject_regime_filter_replaces_existing(self):
        definition = {
            "entry_long": {
                "all": [
                    {"field": "regime_label", "op": "eq", "value": "CHOPPY_NOISE"},
                    {"field": "rsi_14", "op": "lt", "value": 30},
                ]
            },
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        injected = inject_regime_filter(definition, "TREND_BULL_LOW_VOL", target="entry_long")

        # Should still have 2 items (regime replaced, not duplicated)
        assert len(injected["entry_long"]["all"]) == 2
        # First should be the new regime filter
        assert injected["entry_long"]["all"][0]["field"] == "regime_label"
        assert injected["entry_long"]["all"][0]["value"] == "TREND_BULL_LOW_VOL"
        # Second should be original RSI
        assert injected["entry_long"]["all"][1]["field"] == "rsi_14"

    def test_inject_regime_filter_invalid_label(self):
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "position_size_units": 1000,
        }
        with pytest.raises(ValueError, match="Invalid regime_label"):
            inject_regime_filter(definition, "INVALID_LABEL", target="entry_long")
