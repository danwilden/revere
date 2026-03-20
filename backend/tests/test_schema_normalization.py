"""Tests for OHLC false-positive warnings and deprecated key detection."""
from __future__ import annotations

import pytest

from backend.agents.strategy_researcher import _validate_candidate_definition
from backend.strategies.validation import validate_rules_strategy


class TestOHLCWarnings:
    """_validate_candidate_definition must not warn about OHLC-only strategies."""

    def test_ohlc_only_no_feature_warnings(self):
        """OHLC strategy with no feature_run_id produces zero feature-field warnings."""
        defn = {
            "entry_long": {"field": "close", "op": "gt", "field2": "open"},
            "exit": {"field": "bars_in_trade", "op": "gte", "value": 5},
            "position_size_units": 1000,
        }
        warnings = _validate_candidate_definition(defn, feature_run_id=None)
        feature_warnings = [w for w in warnings if "Feature fields used" in w]
        assert len(feature_warnings) == 0, f"Unexpected feature warnings: {feature_warnings}"

    def test_engine_state_fields_no_feature_warnings(self):
        """bars_in_trade, days_in_trade are native — no warnings."""
        defn = {
            "entry_long": {"field": "close", "op": "gt", "value": 0},
            "exit": {"all": [
                {"field": "days_in_trade", "op": "gte", "value": 2.0},
                {"field": "bars_in_trade", "op": "gte", "value": 10},
            ]},
            "position_size_units": 1000,
        }
        warnings = _validate_candidate_definition(defn, feature_run_id=None)
        feature_warnings = [w for w in warnings if "Feature fields used" in w]
        assert len(feature_warnings) == 0, f"Unexpected feature warnings: {feature_warnings}"

    def test_hour_of_day_without_feature_run_emits_warning(self):
        """Strategy using hour_of_day without feature_run_id should emit a warning."""
        defn = {
            "entry_long": {"field": "hour_of_day", "op": "gte", "value": 8},
            "exit": {"field": "close", "op": "lt", "value": 0},
            "position_size_units": 1000,
        }
        warnings = _validate_candidate_definition(defn, feature_run_id=None)
        feature_warnings = [w for w in warnings if "Feature fields used" in w]
        assert len(feature_warnings) >= 1

    def test_day_of_week_without_feature_run_emits_warning(self):
        """day_of_week is a feature field — warns when feature_run_id is absent."""
        defn = {
            "entry_long": {"field": "day_of_week", "op": "eq", "value": 0},
            "exit": {"field": "close", "op": "lt", "value": 0},
            "position_size_units": 1000,
        }
        warnings = _validate_candidate_definition(defn, feature_run_id=None)
        assert any("day_of_week" in w for w in warnings)

    def test_feature_fields_with_feature_run_no_warning(self):
        """Feature fields are fine when feature_run_id is provided."""
        defn = {
            "entry_long": {"field": "hour_of_day", "op": "gte", "value": 8},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
            "position_size_units": 1000,
        }
        warnings = _validate_candidate_definition(defn, feature_run_id="feat-run-123")
        feature_warnings = [w for w in warnings if "Feature fields used" in w]
        assert len(feature_warnings) == 0

    def test_rsi_without_feature_run_emits_warning(self):
        """Classic indicator field rsi_14 also warns without feature_run_id."""
        defn = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
            "position_size_units": 1000,
        }
        warnings = _validate_candidate_definition(defn, feature_run_id=None)
        feature_warnings = [w for w in warnings if "Feature fields used" in w]
        assert len(feature_warnings) >= 1


class TestDeprecatedKeys:
    """validate_rules_strategy must reject renamed/deprecated keys with helpful errors."""

    def _base(self) -> dict:
        return {
            "entry_long": {"field": "close", "op": "gt", "value": 0},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }

    def test_take_profit_multiplier_rejected(self):
        """take_profit_multiplier (missing _atr_) is rejected with a suggestion."""
        defn = {**self._base(), "take_profit_multiplier": 2.0}
        errors = validate_rules_strategy(defn)
        assert any("take_profit_multiplier" in e for e in errors), \
            f"Expected error about take_profit_multiplier, got: {errors}"
        assert any("take_profit_atr_multiplier" in e for e in errors), \
            f"Expected suggestion of take_profit_atr_multiplier, got: {errors}"

    def test_take_profit_atr_multiplier_accepted(self):
        """take_profit_atr_multiplier (correct name) is valid."""
        defn = {**self._base(), "take_profit_atr_multiplier": 2.5}
        assert validate_rules_strategy(defn) == []

    def test_both_keys_present_both_flagged(self):
        """Having both the deprecated and correct key both gets a deprecated-key error."""
        defn = {**self._base(), "take_profit_multiplier": 2.0, "take_profit_atr_multiplier": 3.0}
        errors = validate_rules_strategy(defn)
        assert any("take_profit_multiplier" in e for e in errors)

    def test_stop_atr_multiplier_still_valid(self):
        """stop_atr_multiplier has the correct name — no error."""
        defn = {**self._base(), "stop_atr_multiplier": 1.5}
        assert validate_rules_strategy(defn) == []

    def test_valid_strategy_no_errors(self):
        """A standard valid strategy with all correct keys passes cleanly."""
        defn = {
            "entry_long": {"field": "close", "op": "gt", "value": 0},
            "exit": {"field": "bars_in_trade", "op": "gte", "value": 10},
            "stop_atr_multiplier": 2.0,
            "take_profit_atr_multiplier": 3.0,
            "cooldown_hours": 2.0,
            "position_size_units": 1000,
        }
        assert validate_rules_strategy(defn) == []
