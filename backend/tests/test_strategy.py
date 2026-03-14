"""Unit tests for Phase 3 strategy layer components.

Covers:
- StrategyState: cooldown, open/close position helpers
- RulesStrategy: entry/exit evaluation via rules engine
- CodeStrategy: load and delegate user code
- Sandbox: safe execution, timeout, exception handling
- Validation: rules and python strategy validators
- Strategy CRUD via LocalMetadataRepository
"""
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.strategies.state import StrategyState
from backend.strategies.rules_strategy import RulesStrategy
from backend.strategies.code_strategy import CodeStrategy
from backend.strategies.sandbox import run_sandboxed
from backend.strategies.validation import validate_rules_strategy, validate_python_strategy
from backend.data.repositories import LocalMetadataRepository
from backend.schemas.models import Strategy
from backend.schemas.enums import StrategyType


# ---------------------------------------------------------------------------
# StrategyState tests
# ---------------------------------------------------------------------------

def test_state_initial_is_flat():
    state = StrategyState()
    assert state.is_flat
    assert not state.is_long
    assert not state.is_short
    assert state.open_position is None


def test_state_open_trade():
    state = StrategyState()
    t = datetime(2024, 1, 15, 10, 0)
    state.open_trade("long", t, 1.0850, 10000, stop=1.0820, target=1.0910)
    assert state.is_long
    assert not state.is_flat
    assert state.open_position["entry_price"] == 1.0850
    assert state.open_position["stop"] == 1.0820
    assert state.open_position["target"] == 1.0910


def test_state_close_trade():
    state = StrategyState()
    t_open = datetime(2024, 1, 15, 10, 0)
    t_close = datetime(2024, 1, 15, 14, 0)
    state.open_trade("long", t_open, 1.0850, 10000)
    state.close_trade(t_close)
    assert state.is_flat
    assert state.last_exit_time == t_close


def test_state_close_trade_when_flat_raises():
    state = StrategyState()
    with pytest.raises(ValueError, match="No open position"):
        state.close_trade(datetime.utcnow())


def test_state_no_cooldown_when_zero():
    state = StrategyState(cooldown_hours=0)
    state.last_exit_time = datetime(2024, 1, 15, 10, 0)
    assert not state.in_cooldown(datetime(2024, 1, 15, 10, 30))


def test_state_in_cooldown_true():
    state = StrategyState(cooldown_hours=48)
    t_exit = datetime(2024, 1, 15, 10, 0)
    state.last_exit_time = t_exit
    # 24h after exit — still within 48h cooldown
    assert state.in_cooldown(t_exit + timedelta(hours=24))


def test_state_cooldown_expired():
    state = StrategyState(cooldown_hours=48)
    t_exit = datetime(2024, 1, 15, 10, 0)
    state.last_exit_time = t_exit
    # 49h after exit — cooldown over
    assert not state.in_cooldown(t_exit + timedelta(hours=49))


def test_state_no_cooldown_before_any_exit():
    state = StrategyState(cooldown_hours=48)
    # Never exited — should not be in cooldown
    assert not state.in_cooldown(datetime.utcnow())


def test_state_hours_since_exit_none_before_first_exit():
    state = StrategyState()
    assert state.hours_since_exit(datetime.utcnow()) is None


def test_state_hours_since_exit_value():
    state = StrategyState()
    t_exit = datetime(2024, 1, 15, 10, 0)
    state.last_exit_time = t_exit
    elapsed = state.hours_since_exit(t_exit + timedelta(hours=6))
    assert abs(elapsed - 6.0) < 0.01


# ---------------------------------------------------------------------------
# RulesStrategy tests
# ---------------------------------------------------------------------------

@pytest.fixture
def bar():
    return {
        "timestamp_utc": datetime(2024, 3, 1, 10, 0),
        "open": 1.0830,
        "high": 1.0870,
        "low": 1.0810,
        "close": 1.0855,
        "atr_14": 0.0020,
    }


@pytest.fixture
def features():
    return {
        "rsi_14": 28.0,
        "adx_14": 16.0,
        "regime": "RANGE_MEAN_REVERT",
        "ema_slope_20": -0.0001,
        "log_ret_1": -0.002,
    }


def test_rules_strategy_enter_long(bar, features):
    definition = {
        "entry_long": {"all": [
            {"field": "rsi_14", "op": "lt", "value": 35},
            {"field": "regime", "op": "eq", "value": "RANGE_MEAN_REVERT"},
        ]},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    strategy = RulesStrategy(definition)
    state = StrategyState()
    assert strategy.should_enter_long(bar, features, state) is True


def test_rules_strategy_no_enter_long_wrong_regime(bar, features):
    features["regime"] = "CHOPPY_NOISE"
    definition = {
        "entry_long": {"all": [
            {"field": "rsi_14", "op": "lt", "value": 35},
            {"field": "regime", "op": "eq", "value": "RANGE_MEAN_REVERT"},
        ]},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    strategy = RulesStrategy(definition)
    state = StrategyState()
    assert strategy.should_enter_long(bar, features, state) is False


def test_rules_strategy_enter_short_none_when_not_defined(bar, features):
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    strategy = RulesStrategy(definition)
    state = StrategyState()
    assert strategy.should_enter_short(bar, features, state) is False


def test_rules_strategy_exit(bar, features):
    features["rsi_14"] = 72.0
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    strategy = RulesStrategy(definition)
    state = StrategyState()
    position = {"side": "long"}
    assert strategy.should_exit(bar, features, position, state) is True


def test_rules_strategy_position_size_from_definition(bar, features):
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
        "position_size_units": 25000.0,
    }
    strategy = RulesStrategy(definition)
    size = strategy.position_size(bar, 100_000, {})
    assert size == 25000.0


def test_rules_strategy_stop_price_computed(bar, features):
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
        "stop_atr_multiplier": 2.0,
    }
    strategy = RulesStrategy(definition)
    stop = strategy.stop_price({"close": 1.0855, "atr_14": 0.002}, "long", {})
    assert stop == pytest.approx(1.0855 - 2.0 * 0.002)


# ---------------------------------------------------------------------------
# CodeStrategy tests
# ---------------------------------------------------------------------------

_SIMPLE_STRATEGY_CODE = """
from backend.strategies.base import BaseStrategy
from backend.strategies.state import StrategyState

class AlwaysLong(BaseStrategy):
    def should_enter_long(self, bar, features, state):
        return True
    def should_enter_short(self, bar, features, state):
        return False
    def should_exit(self, bar, features, position, state):
        return False
"""


def test_code_strategy_loads_and_enters_long(bar, features):
    strategy = CodeStrategy(_SIMPLE_STRATEGY_CODE)
    state = StrategyState()
    assert strategy.should_enter_long(bar, features, state) is True


def test_code_strategy_by_class_name(bar, features):
    strategy = CodeStrategy(_SIMPLE_STRATEGY_CODE, class_name="AlwaysLong")
    state = StrategyState()
    assert strategy.should_enter_long(bar, features, state) is True


def test_code_strategy_missing_class_raises():
    with pytest.raises(ValueError, match="NoSuchClass"):
        CodeStrategy(_SIMPLE_STRATEGY_CODE, class_name="NoSuchClass")


def test_code_strategy_invalid_code_raises():
    with pytest.raises(ValueError, match="Failed to load"):
        CodeStrategy("this is not valid python !!!")


# ---------------------------------------------------------------------------
# Sandbox tests
# ---------------------------------------------------------------------------

def test_sandbox_runs_safe_code():
    code = "output = input_data['x'] * 2"
    result = run_sandboxed(code, {"x": 21})
    assert result["success"] is True
    assert result["output"] == 42


def test_sandbox_captures_exception():
    code = "raise ValueError('intentional error')"
    result = run_sandboxed(code, {})
    assert result["success"] is False
    assert "intentional error" in result["error"]


def test_sandbox_timeout():
    code = "import time; time.sleep(60)"
    result = run_sandboxed(code, {}, timeout_secs=1.0)
    assert result["success"] is False
    assert "timed out" in result["error"].lower()


def test_sandbox_accesses_input_data():
    code = "output = {'keys': list(input_data.keys())}"
    result = run_sandboxed(code, {"a": 1, "b": 2})
    assert result["success"] is True
    assert sorted(result["output"]["keys"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_validate_rules_strategy_valid():
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    errors = validate_rules_strategy(definition)
    assert errors == []


def test_validate_rules_strategy_missing_entry_long():
    definition = {
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    errors = validate_rules_strategy(definition)
    assert any("entry_long" in e for e in errors)


def test_validate_rules_strategy_missing_exit():
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
    }
    errors = validate_rules_strategy(definition)
    assert any("exit" in e for e in errors)


def test_validate_rules_strategy_invalid_op():
    definition = {
        "entry_long": {"field": "rsi_14", "op": "BADOP", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    errors = validate_rules_strategy(definition)
    assert any("BADOP" in e for e in errors)


def test_validate_rules_strategy_invalid_ref():
    definition = {
        "entry_long": {"ref": "undefined_condition"},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    errors = validate_rules_strategy(definition)
    assert any("undefined_condition" in e for e in errors)


def test_validate_rules_strategy_named_conditions_work():
    definition = {
        "named_conditions": {
            "oversold": {"field": "rsi_14", "op": "lt", "value": 35},
        },
        "entry_long": {"ref": "oversold"},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
    }
    errors = validate_rules_strategy(definition)
    assert errors == []


def test_validate_rules_strategy_non_numeric_cooldown():
    definition = {
        "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
        "exit": {"field": "rsi_14", "op": "gt", "value": 60},
        "cooldown_hours": "48",  # should be a number
    }
    errors = validate_rules_strategy(definition)
    assert any("cooldown_hours" in e for e in errors)


def test_validate_python_strategy_valid():
    errors = validate_python_strategy({"code": "class Foo(BaseStrategy): pass"})
    assert errors == []


def test_validate_python_strategy_missing_code():
    errors = validate_python_strategy({})
    assert any("code" in e for e in errors)


def test_validate_python_strategy_empty_code():
    errors = validate_python_strategy({"code": "   "})
    assert any("non-empty" in e for e in errors)


def test_validate_python_strategy_bad_class_name():
    errors = validate_python_strategy({"code": "pass", "class_name": ""})
    assert any("class_name" in e for e in errors)


# ---------------------------------------------------------------------------
# Strategy CRUD via LocalMetadataRepository
# ---------------------------------------------------------------------------

def test_strategy_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = LocalMetadataRepository(tmpdir)

        strategy = Strategy(
            name="Test Mean Reversion",
            strategy_type=StrategyType.RULES_ENGINE,
            definition_json={
                "entry_long": {"field": "rsi_14", "op": "lt", "value": 35},
                "exit": {"field": "rsi_14", "op": "gt", "value": 60},
            },
        )
        repo.save_strategy(strategy.model_dump(mode="json"))

        fetched = repo.get_strategy(strategy.id)
        assert fetched is not None
        assert fetched["name"] == "Test Mean Reversion"
        assert fetched["strategy_type"] == "rules_engine"

        listed = repo.list_strategies()
        assert len(listed) == 1
        assert listed[0]["id"] == strategy.id


def test_strategy_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = LocalMetadataRepository(tmpdir)
        assert repo.get_strategy("nonexistent-id") is None
