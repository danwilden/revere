"""Tests for validate_field_availability() — field availability gate."""
from __future__ import annotations

import pytest

from backend.strategies.validation import validate_field_availability


class TestValidateFieldAvailabilityNativeOnly:
    def test_ohlc_strategy_no_errors(self):
        """OHLC-only strategy has no field availability errors."""
        defn = {
            "entry_long": {"field": "close", "op": "gt", "field2": "open"},
            "exit": {"field": "bars_in_trade", "op": "gte", "value": 5},
        }
        assert validate_field_availability(defn) == []

    def test_ohlc_with_none_feature_run_no_errors(self):
        """Explicit feature_run_id=None still produces no errors for OHLC fields."""
        defn = {
            "entry_long": {"field": "close", "op": "gt", "field2": "open"},
            "exit": {"field": "high", "op": "lt", "value": 999},
        }
        assert validate_field_availability(defn, feature_run_id=None) == []

    def test_engine_state_fields_no_errors(self):
        """bars_in_trade, minutes_in_trade, days_in_trade are always available."""
        defn = {
            "entry_long": {"field": "close", "op": "gt", "value": 0},
            "exit": {"all": [
                {"field": "bars_in_trade", "op": "gte", "value": 5},
                {"field": "days_in_trade", "op": "gte", "value": 1.0},
            ]},
        }
        assert validate_field_availability(defn) == []

    def test_volume_no_errors(self):
        defn = {
            "entry_long": {"field": "volume", "op": "gt", "value": 1000},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }
        assert validate_field_availability(defn) == []


class TestValidateFieldAvailabilityFeatureFields:
    def test_hour_of_day_without_feature_run_returns_error(self):
        defn = {
            "entry_long": {"field": "hour_of_day", "op": "gte", "value": 8},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }
        errors = validate_field_availability(defn, feature_run_id=None)
        assert len(errors) >= 1
        assert any("hour_of_day" in e for e in errors)
        assert any("requires a feature_run_id" in e for e in errors)

    def test_day_of_week_without_feature_run_returns_error(self):
        defn = {
            "entry_long": {"field": "day_of_week", "op": "eq", "value": 0},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }
        errors = validate_field_availability(defn)
        assert any("day_of_week" in e for e in errors)

    def test_is_friday_without_feature_run_returns_error(self):
        defn = {
            "entry_long": {"field": "close", "op": "gt", "value": 0},
            "exit": {"field": "is_friday", "op": "eq", "value": 1},
        }
        errors = validate_field_availability(defn)
        assert any("is_friday" in e for e in errors)

    def test_rsi_14_without_feature_run_returns_error(self):
        defn = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
        }
        errors = validate_field_availability(defn)
        assert any("rsi_14" in e for e in errors)

    def test_error_message_mentions_native_fields(self):
        """Error messages must guide users toward native alternatives."""
        defn = {
            "entry_long": {"field": "day_of_week", "op": "eq", "value": 0},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }
        errors = validate_field_availability(defn)
        combined = " ".join(errors)
        # Should list some native fields as alternatives
        assert "close" in combined or "open" in combined or "bars_in_trade" in combined

    def test_multiple_feature_fields_all_reported(self):
        """All feature fields without feature_run are reported."""
        defn = {
            "entry_long": {"all": [
                {"field": "hour_of_day", "op": "gte", "value": 8},
                {"field": "day_of_week", "op": "lt", "value": 5},
            ]},
            "exit": {"field": "is_friday", "op": "eq", "value": 1},
        }
        errors = validate_field_availability(defn)
        error_text = " ".join(errors)
        assert "hour_of_day" in error_text
        assert "day_of_week" in error_text
        assert "is_friday" in error_text


class TestValidateFieldAvailabilityWithFeatureRun:
    def test_hour_of_day_with_feature_run_passes(self):
        defn = {
            "entry_long": {"field": "hour_of_day", "op": "gte", "value": 8},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }
        assert validate_field_availability(defn, feature_run_id="feat-run-abc123") == []

    def test_rsi_with_feature_run_passes(self):
        defn = {
            "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
            "exit": {"field": "rsi_14", "op": "gt", "value": 70},
        }
        assert validate_field_availability(defn, feature_run_id="feat-run-abc123") == []

    def test_all_v1_0_features_pass_with_feature_run(self):
        """All base feature fields pass when feature_run_id is supplied."""
        fields = [
            "log_ret_1", "log_ret_5", "log_ret_20", "rvol_20",
            "atr_14", "atr_pct_14", "rsi_14", "ema_slope_20",
            "ema_slope_50", "adx_14", "breakout_20",
        ]
        for field in fields:
            defn = {
                "entry_long": {"field": field, "op": "gt", "value": 0},
                "exit": {"field": "close", "op": "lt", "value": 0},
            }
            errors = validate_field_availability(defn, feature_run_id="feat-run-1")
            assert errors == [], f"Expected no errors for {field} with feature_run_id"

    def test_calendar_fields_pass_with_feature_run(self):
        defn = {
            "entry_long": {"all": [
                {"field": "hour_of_day", "op": "gte", "value": 8},
                {"field": "day_of_week", "op": "lt", "value": 5},
            ]},
            "exit": {"field": "is_friday", "op": "eq", "value": 1},
        }
        assert validate_field_availability(defn, feature_run_id="feat-run-1") == []

    def test_unknown_field_not_reported_as_feature_error(self):
        """Completely unknown fields are not reported by validate_field_availability.

        validate_field_availability only reports KNOWN feature fields without
        a feature run — unknown fields are caught by validate_rules_strategy.
        """
        defn = {
            "entry_long": {"field": "totally_unknown_xyz", "op": "gt", "value": 0},
            "exit": {"field": "close", "op": "lt", "value": 0},
        }
        # Unknown fields are not in FEATURE_REQUIRED_FIELDS so not reported here
        errors = validate_field_availability(defn)
        assert not any("totally_unknown_xyz" in e for e in errors)
