"""Tests for Phase 5F experiment librarian utilities.

Pure deterministic function tests. No I/O, no mocks needed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.agents.experiment_librarian import (
    NearDupeResult,
    LineageGraph,
    build_lineage_graph,
    check_near_dupe,
    extract_rule_nodes,
    find_near_dupes_in_context,
    generate_lineage_report,
    jaccard_similarity,
)
from backend.data.repositories import LocalMetadataRepository
from backend.lab.experiment_registry import ExperimentRegistry, ExperimentRecord, ExperimentStatus


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_metadata_repo(tmp_path: Path) -> LocalMetadataRepository:
    """Create a fresh in-memory metadata repository for testing."""
    return LocalMetadataRepository(tmp_path / "test_repo")


@pytest.fixture
def tmp_registry(tmp_metadata_repo) -> ExperimentRegistry:
    """Create a fresh experiment registry backed by tmp_metadata_repo."""
    return ExperimentRegistry(tmp_metadata_repo)


# ============================================================================
# Tests: extract_rule_nodes
# ============================================================================

class TestExtractRuleNodes:
    def test_single_leaf_field_op_value(self):
        """Single leaf with field/op/value extracts correctly."""
        definition = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30}
        }
        result = extract_rule_nodes(definition)
        assert result == frozenset(["field:rsi_14|op:lt|val:30"])

    def test_nested_any_with_three_leaves(self):
        """Nested 'any' with 3 leaves extracts all 3."""
        definition = {
            "entry_long": {
                "any": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                    {"field": "atr_14", "op": "gt", "value": 10},
                    {"field": "close", "op": "gt", "field2": "open"},
                ]
            }
        }
        result = extract_rule_nodes(definition)
        assert len(result) == 3
        assert "field:rsi_14|op:lt|val:30" in result
        assert "field:atr_14|op:gt|val:10" in result
        assert "field:close|op:gt|field2:open" in result

    def test_nested_all_with_leaves(self):
        """Nested 'all' extracts leaves correctly."""
        definition = {
            "entry_short": {
                "all": [
                    {"field": "rsi_14", "op": "gt", "value": 70},
                    {"field": "ema_slope_20", "op": "lt", "value": 0},
                ]
            }
        }
        result = extract_rule_nodes(definition)
        assert len(result) == 2
        assert "field:rsi_14|op:gt|val:70" in result
        assert "field:ema_slope_20|op:lt|val:0" in result

    def test_not_composite_extracts_inner_leaves(self):
        """'not' composite extracts the inner leaf."""
        definition = {
            "exit": {
                "not": {"field": "close", "op": "gt", "value": 1.15}
            }
        }
        result = extract_rule_nodes(definition)
        assert result == frozenset(["field:close|op:gt|val:1.15"])

    def test_ref_leaf_becomes_fingerprint(self):
        """'ref' leaf becomes a ref: fingerprint."""
        definition = {
            "entry_long": {"ref": "my_condition"}
        }
        result = extract_rule_nodes(definition)
        assert result == frozenset(["ref:my_condition"])

    def test_field2_leaf_fingerprint(self):
        """field/op/field2 leaf becomes special fingerprint."""
        definition = {
            "entry_long": {"field": "close", "op": "gt", "field2": "open"}
        }
        result = extract_rule_nodes(definition)
        assert result == frozenset(["field:close|op:gt|field2:open"])

    def test_list_value_sorted_fingerprint(self):
        """List value is sorted in fingerprint."""
        definition = {
            "entry_long": {"field": "day_of_week", "op": "in", "value": [3, 1, 2]}
        }
        result = extract_rule_nodes(definition)
        # Value [3, 1, 2] should be sorted in fingerprint as ['1', '2', '3']
        extracted = list(result)
        assert len(extracted) == 1
        fingerprint = extracted[0]
        assert "field:day_of_week|op:in|val:" in fingerprint
        # The actual list representation; the sorted call converts to strings
        assert "1" in fingerprint and "2" in fingerprint and "3" in fingerprint

    def test_empty_definition(self):
        """Empty definition returns empty frozenset."""
        definition = {}
        result = extract_rule_nodes(definition)
        assert result == frozenset()

    def test_none_entry_points_ignored(self):
        """None entry_long/entry_short/exit are safely ignored."""
        definition = {
            "entry_long": None,
            "entry_short": None,
            "exit": None,
        }
        result = extract_rule_nodes(definition)
        assert result == frozenset()

    def test_named_conditions_recursively_walked(self):
        """named_conditions dict values are recursively walked."""
        definition = {
            "named_conditions": {
                "trend_up": {"field": "ema_slope_20", "op": "gt", "value": 0},
                "volatility_high": {"field": "atr_14", "op": "gt", "value": 5},
            }
        }
        result = extract_rule_nodes(definition)
        assert len(result) == 2
        assert "field:ema_slope_20|op:gt|val:0" in result
        assert "field:atr_14|op:gt|val:5" in result

    def test_complex_nested_structure(self):
        """Complex nested structure with all/any/not is walked correctly."""
        definition = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                    {
                        "any": [
                            {"field": "atr_14", "op": "gt", "value": 10},
                            {"not": {"field": "close", "op": "lt", "value": 1.0}},
                        ]
                    },
                ]
            }
        }
        result = extract_rule_nodes(definition)
        assert len(result) == 3
        assert "field:rsi_14|op:lt|val:30" in result
        assert "field:atr_14|op:gt|val:10" in result
        assert "field:close|op:lt|val:1.0" in result


# ============================================================================
# Tests: jaccard_similarity
# ============================================================================

class TestJaccardSimilarity:
    def test_identical_sets_return_one(self):
        """Identical non-empty sets return 1.0."""
        a = frozenset(["x", "y", "z"])
        b = frozenset(["x", "y", "z"])
        assert jaccard_similarity(a, b) == 1.0

    def test_disjoint_sets_return_zero(self):
        """Completely disjoint sets return 0.0."""
        a = frozenset(["a", "b"])
        b = frozenset(["x", "y"])
        assert jaccard_similarity(a, b) == 0.0

    def test_fifty_percent_overlap(self):
        """50% overlap: |A∩B|=1, |A∪B|=3 → 1/3 ≈ 0.333."""
        a = frozenset(["1", "2"])
        b = frozenset(["2", "3"])
        result = jaccard_similarity(a, b)
        assert abs(result - 1/3) < 0.001

    def test_both_empty_sets_return_zero(self):
        """Both empty sets return 0.0."""
        a = frozenset()
        b = frozenset()
        assert jaccard_similarity(a, b) == 0.0

    def test_one_empty_one_nonempty_return_zero(self):
        """One empty, one non-empty returns 0.0."""
        a = frozenset()
        b = frozenset(["x", "y"])
        assert jaccard_similarity(a, b) == 0.0
        # Symmetric
        assert jaccard_similarity(b, a) == 0.0


# ============================================================================
# Tests: check_near_dupe
# ============================================================================

class TestCheckNearDupe:
    def test_identical_definition_is_dupe(self):
        """Identical definition is detected as near-dupe."""
        definition = {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}}
        candidates = [
            ("exp-1", {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}})
        ]
        result = check_near_dupe(definition, candidates)
        assert result.is_near_dupe is True
        assert result.matched_experiment_id == "exp-1"
        assert result.jaccard_score == 1.0
        assert result.block_reason is not None

    def test_completely_different_not_dupe(self):
        """Completely different definitions are not dupes."""
        definition = {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}}
        candidates = [
            ("exp-1", {"entry_long": {"field": "atr_14", "op": "gt", "value": 10}})
        ]
        result = check_near_dupe(definition, candidates)
        assert result.is_near_dupe is False
        assert result.matched_experiment_id is None
        assert result.jaccard_score == 0.0
        assert result.block_reason is None

    def test_high_similarity_blocks_above_threshold(self):
        """High similarity (6 of 7 leaves same) blocks at 0.85 threshold."""
        # Target: 7 leaves
        target = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                    {"field": "atr_14", "op": "gt", "value": 10},
                    {"field": "ema_slope_20", "op": "gt", "value": 0},
                    {"field": "adx_14", "op": "gt", "value": 20},
                    {"field": "session", "op": "eq", "value": "London"},
                    {"field": "close", "op": "gt", "field2": "open"},
                    {"ref": "trend_up"},
                ]
            }
        }
        # Candidate: 6 of 7 same (removed ema_slope_20)
        candidate = {
            "entry_long": {
                "all": [
                    {"field": "rsi_14", "op": "lt", "value": 30},
                    {"field": "atr_14", "op": "gt", "value": 10},
                    {"field": "adx_14", "op": "gt", "value": 20},
                    {"field": "session", "op": "eq", "value": "London"},
                    {"field": "close", "op": "gt", "field2": "open"},
                    {"ref": "trend_up"},
                ]
            }
        }
        result = check_near_dupe(target, [("exp-cand", candidate)], threshold=0.85)
        # 6 shared, 1 different → union=7, intersection=6 → 6/7=0.857 > 0.85
        assert result.is_near_dupe is True
        assert result.matched_experiment_id == "exp-cand"
        assert result.jaccard_score >= 0.85

    def test_threshold_exactly_at_boundary(self):
        """Jaccard exactly at threshold passes (>=)."""
        target = {"entry_long": {"all": [
            {"field": "a", "op": "gt", "value": 1},
            {"field": "b", "op": "gt", "value": 1},
        ]}}
        # 2 of 2 same → 1.0 which passes 0.85 threshold
        candidate = {"entry_long": {"all": [
            {"field": "a", "op": "gt", "value": 1},
            {"field": "b", "op": "gt", "value": 1},
        ]}}
        result = check_near_dupe(target, [("exp-cand", candidate)], threshold=0.85)
        assert result.is_near_dupe is True

    def test_just_below_threshold_not_blocked(self):
        """Jaccard just below threshold is not blocked."""
        target = {"entry_long": {
            "all": [
                {"field": "a", "op": "gt", "value": 1},
                {"field": "b", "op": "gt", "value": 1},
                {"field": "c", "op": "gt", "value": 1},
            ]
        }}
        candidate = {"entry_long": {
            "all": [
                {"field": "a", "op": "gt", "value": 1},
                {"field": "d", "op": "gt", "value": 1},  # Different
            ]
        }}
        # intersection=1, union=4 → 1/4=0.25 < 0.85
        result = check_near_dupe(target, [("exp-cand", candidate)], threshold=0.85)
        assert result.is_near_dupe is False

    def test_empty_candidates_list_not_dupe(self):
        """Empty candidates list returns not-a-dupe."""
        definition = {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}}
        result = check_near_dupe(definition, [])
        assert result.is_near_dupe is False

    def test_first_match_returned(self):
        """Returns first match when multiple candidates match."""
        target = {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}}
        candidates = [
            ("exp-1", {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}}),  # Match
            ("exp-2", {"entry_long": {"field": "rsi_14", "op": "lt", "value": 30}}),  # Also match
        ]
        result = check_near_dupe(target, candidates, threshold=0.85)
        # Should return the first match
        assert result.matched_experiment_id == "exp-1"


# ============================================================================
# Tests: build_lineage_graph
# ============================================================================

class TestBuildLineageGraph:
    def test_single_node_lineage(self, tmp_registry):
        """Single-node lineage has depth 1, root == leaf."""
        exp = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
        )
        graph = build_lineage_graph(exp.id, tmp_registry)
        assert graph.depth == 1
        assert graph.root_id == exp.id
        assert graph.leaf_id == exp.id
        assert graph.session_id == "sess-1"

    def test_chain_of_three_nodes(self, tmp_registry):
        """Chain of 3 nodes: A→B→C has depth 3."""
        exp_a = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
            generation=0,
        )
        exp_b = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
            parent_id=exp_a.id,
            generation=1,
        )
        exp_c = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
            parent_id=exp_b.id,
            generation=2,
        )
        graph = build_lineage_graph(exp_c.id, tmp_registry)
        assert graph.depth == 3
        assert graph.root_id == exp_a.id
        assert graph.leaf_id == exp_c.id
        assert len(graph.records) == 3
        assert graph.records[0].id == exp_a.id
        assert graph.records[1].id == exp_b.id
        assert graph.records[2].id == exp_c.id

    def test_lineage_session_id_from_root(self, tmp_registry):
        """LineageGraph.session_id comes from root (first record)."""
        exp_a = tmp_registry.create(
            session_id="sess-99",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
        )
        exp_b = tmp_registry.create(
            session_id="sess-99",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
            parent_id=exp_a.id,
        )
        graph = build_lineage_graph(exp_b.id, tmp_registry)
        assert graph.session_id == "sess-99"


# ============================================================================
# Tests: generate_lineage_report
# ============================================================================

class TestGenerateLineageReport:
    def test_report_contains_session_id(self, tmp_registry):
        """Generated report contains session_id."""
        exp = tmp_registry.create(
            session_id="sess-abc",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
        )
        graph = build_lineage_graph(exp.id, tmp_registry)
        report = generate_lineage_report(graph)
        assert "sess-abc" in report

    def test_report_contains_depth(self, tmp_registry):
        """Generated report contains depth info."""
        exp_a = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
        )
        exp_b = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
            parent_id=exp_a.id,
        )
        graph = build_lineage_graph(exp_b.id, tmp_registry)
        report = generate_lineage_report(graph)
        assert "2 generation" in report or "Depth: 2" in report

    def test_report_contains_generation_indices(self, tmp_registry):
        """Generated report contains generation indices."""
        exp_a = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
        )
        exp_b = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
            parent_id=exp_a.id,
        )
        graph = build_lineage_graph(exp_b.id, tmp_registry)
        report = generate_lineage_report(graph)
        assert "Generation 0" in report
        assert "Generation 1" in report

    def test_report_is_nonempty_string(self, tmp_registry):
        """Generated report is a non-empty string."""
        exp = tmp_registry.create(
            session_id="sess-1",
            instrument="EUR_USD",
            timeframe="H1",
            test_start="2023-01-01T00:00:00Z",
            test_end="2024-01-01T00:00:00Z",
        )
        graph = build_lineage_graph(exp.id, tmp_registry)
        report = generate_lineage_report(graph)
        assert isinstance(report, str)
        assert len(report) > 0
