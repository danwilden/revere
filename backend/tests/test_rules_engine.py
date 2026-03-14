"""Comprehensive unit tests for the rules DSL evaluator (rules_engine.py).

Covers:
- all / any / not composites
- Leaf comparisons: gt, gte, lt, lte, eq, neq, in
- Field-to-field comparisons
- Named condition refs
- Error cases: missing field, invalid op, bad ref
- Nested combinations
"""
import pytest

from backend.strategies.rules_engine import evaluate, VALID_OPS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    """A baseline feature context dict for tests."""
    return {
        "rsi_14": 28.5,
        "adx_14": 18.0,
        "close": 1.0850,
        "open": 1.0840,
        "atr_14": 0.0015,
        "session": 1,
        "regime": "RANGE_MEAN_REVERT",
        "ema_slope_20": -0.0002,
        "log_ret_1": -0.001,
        "rvol_20": 0.0008,
    }


# ---------------------------------------------------------------------------
# Simple leaf comparisons
# ---------------------------------------------------------------------------

def test_gt_true(ctx):
    node = {"field": "rsi_14", "op": "gt", "value": 20}
    assert evaluate(node, ctx) is True


def test_gt_false(ctx):
    node = {"field": "rsi_14", "op": "gt", "value": 50}
    assert evaluate(node, ctx) is False


def test_gte_equal(ctx):
    node = {"field": "rsi_14", "op": "gte", "value": 28.5}
    assert evaluate(node, ctx) is True


def test_lt_true(ctx):
    node = {"field": "adx_14", "op": "lt", "value": 25}
    assert evaluate(node, ctx) is True


def test_lt_false(ctx):
    node = {"field": "adx_14", "op": "lt", "value": 10}
    assert evaluate(node, ctx) is False


def test_lte_equal(ctx):
    node = {"field": "adx_14", "op": "lte", "value": 18.0}
    assert evaluate(node, ctx) is True


def test_eq_true(ctx):
    node = {"field": "regime", "op": "eq", "value": "RANGE_MEAN_REVERT"}
    assert evaluate(node, ctx) is True


def test_eq_false(ctx):
    node = {"field": "regime", "op": "eq", "value": "TREND_BULL_LOW_VOL"}
    assert evaluate(node, ctx) is False


def test_neq_true(ctx):
    node = {"field": "regime", "op": "neq", "value": "CHOPPY_NOISE"}
    assert evaluate(node, ctx) is True


def test_neq_false(ctx):
    node = {"field": "regime", "op": "neq", "value": "RANGE_MEAN_REVERT"}
    assert evaluate(node, ctx) is False


def test_in_true(ctx):
    node = {"field": "regime", "op": "in", "value": ["RANGE_MEAN_REVERT", "CHOPPY_SIGNAL"]}
    assert evaluate(node, ctx) is True


def test_in_false(ctx):
    node = {"field": "regime", "op": "in", "value": ["TREND_BULL_LOW_VOL", "CHOPPY_NOISE"]}
    assert evaluate(node, ctx) is False


# ---------------------------------------------------------------------------
# Field-to-field comparisons
# ---------------------------------------------------------------------------

def test_field_to_field_close_gt_open(ctx):
    # close > open  => 1.0850 > 1.0840 => True
    node = {"field": "close", "op": "gt", "field2": "open"}
    assert evaluate(node, ctx) is True


def test_field_to_field_open_gt_close_false(ctx):
    node = {"field": "open", "op": "gt", "field2": "close"}
    assert evaluate(node, ctx) is False


def test_field_to_field_eq(ctx):
    ctx_copy = dict(ctx)
    ctx_copy["close"] = ctx_copy["open"]
    node = {"field": "close", "op": "eq", "field2": "open"}
    assert evaluate(node, ctx_copy) is True


# ---------------------------------------------------------------------------
# Composite: all
# ---------------------------------------------------------------------------

def test_all_all_true(ctx):
    node = {"all": [
        {"field": "rsi_14", "op": "lt", "value": 35},
        {"field": "adx_14", "op": "lt", "value": 25},
    ]}
    assert evaluate(node, ctx) is True


def test_all_one_false(ctx):
    node = {"all": [
        {"field": "rsi_14", "op": "lt", "value": 35},
        {"field": "adx_14", "op": "gt", "value": 50},  # False
    ]}
    assert evaluate(node, ctx) is False


def test_all_empty_is_true(ctx):
    # vacuous truth: all([]) == True
    node = {"all": []}
    assert evaluate(node, ctx) is True


# ---------------------------------------------------------------------------
# Composite: any
# ---------------------------------------------------------------------------

def test_any_one_true(ctx):
    node = {"any": [
        {"field": "adx_14", "op": "gt", "value": 50},  # False
        {"field": "rsi_14", "op": "lt", "value": 35},  # True
    ]}
    assert evaluate(node, ctx) is True


def test_any_all_false(ctx):
    node = {"any": [
        {"field": "adx_14", "op": "gt", "value": 50},
        {"field": "rsi_14", "op": "gt", "value": 80},
    ]}
    assert evaluate(node, ctx) is False


def test_any_empty_is_false(ctx):
    # vacuous: any([]) == False
    node = {"any": []}
    assert evaluate(node, ctx) is False


# ---------------------------------------------------------------------------
# Composite: not
# ---------------------------------------------------------------------------

def test_not_true(ctx):
    node = {"not": {"field": "adx_14", "op": "gt", "value": 50}}
    assert evaluate(node, ctx) is True


def test_not_false(ctx):
    node = {"not": {"field": "rsi_14", "op": "lt", "value": 35}}
    assert evaluate(node, ctx) is False


# ---------------------------------------------------------------------------
# Named conditions
# ---------------------------------------------------------------------------

def test_named_condition_resolves(ctx):
    named = {
        "oversold": {"field": "rsi_14", "op": "lt", "value": 35},
    }
    node = {"ref": "oversold"}
    assert evaluate(node, ctx, named) is True


def test_named_condition_nested_in_all(ctx):
    named = {
        "low_adx": {"field": "adx_14", "op": "lt", "value": 25},
    }
    node = {"all": [
        {"ref": "low_adx"},
        {"field": "regime", "op": "eq", "value": "RANGE_MEAN_REVERT"},
    ]}
    assert evaluate(node, ctx, named) is True


def test_named_condition_unknown_ref(ctx):
    node = {"ref": "nonexistent"}
    with pytest.raises(ValueError, match="nonexistent"):
        evaluate(node, ctx, {})


# ---------------------------------------------------------------------------
# Deeply nested combinations
# ---------------------------------------------------------------------------

def test_nested_all_any_not(ctx):
    node = {"all": [
        {"any": [
            {"field": "rsi_14", "op": "lt", "value": 35},
            {"field": "adx_14", "op": "gt", "value": 40},
        ]},
        {"not": {"field": "regime", "op": "eq", "value": "CHOPPY_NOISE"}},
    ]}
    assert evaluate(node, ctx) is True


def test_deeply_nested_false(ctx):
    node = {"all": [
        {"any": [
            {"field": "rsi_14", "op": "gt", "value": 80},   # False
            {"field": "adx_14", "op": "gt", "value": 50},   # False
        ]},
        {"field": "regime", "op": "eq", "value": "RANGE_MEAN_REVERT"},
    ]}
    assert evaluate(node, ctx) is False


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_missing_field_raises(ctx):
    node = {"field": "nonexistent_field", "op": "gt", "value": 0}
    with pytest.raises(ValueError, match="nonexistent_field"):
        evaluate(node, ctx)


def test_invalid_op_raises(ctx):
    node = {"field": "rsi_14", "op": "INVALID_OP", "value": 30}
    with pytest.raises(ValueError, match="INVALID_OP"):
        evaluate(node, ctx)


def test_unrecognised_node_structure_raises(ctx):
    node = {"unknown_key": "something"}
    with pytest.raises(ValueError, match="Unrecognised rule node"):
        evaluate(node, ctx)


def test_all_must_be_list(ctx):
    node = {"all": {"field": "rsi_14", "op": "lt", "value": 50}}  # dict not list
    with pytest.raises(ValueError, match="list"):
        evaluate(node, ctx)


# ---------------------------------------------------------------------------
# VALID_OPS completeness
# ---------------------------------------------------------------------------

def test_valid_ops_set():
    assert {"gt", "gte", "lt", "lte", "eq", "neq", "in"} == VALID_OPS
