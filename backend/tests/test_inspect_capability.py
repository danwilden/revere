"""Tests for requires_feature_run on CapabilityRecord and list_native_fields()."""
from __future__ import annotations

from backend.strategies.capabilities import inspect_capability, list_native_fields
from backend.strategies.capabilities import CapabilityTaxonomy


class TestRequiresFeatureRun:
    """requires_feature_run must be True for MARKET_FEATURE, False for everything else."""

    def test_hour_of_day_requires_feature_run(self):
        rec = inspect_capability("hour_of_day")
        assert rec.requires_feature_run is True

    def test_day_of_week_requires_feature_run(self):
        rec = inspect_capability("day_of_week")
        assert rec.requires_feature_run is True

    def test_is_friday_requires_feature_run(self):
        rec = inspect_capability("is_friday")
        assert rec.requires_feature_run is True

    def test_rsi_14_requires_feature_run(self):
        rec = inspect_capability("rsi_14")
        assert rec.requires_feature_run is True

    def test_adx_14_requires_feature_run(self):
        rec = inspect_capability("adx_14")
        assert rec.requires_feature_run is True

    def test_cyclical_field_requires_feature_run(self):
        rec = inspect_capability("hour_of_day_sin")
        assert rec.requires_feature_run is True

    def test_bars_in_trade_does_not_require_feature_run(self):
        rec = inspect_capability("bars_in_trade")
        assert rec.taxonomy == CapabilityTaxonomy.STATE_MARKER
        assert rec.requires_feature_run is False

    def test_days_in_trade_does_not_require_feature_run(self):
        rec = inspect_capability("days_in_trade")
        assert rec.requires_feature_run is False

    def test_minutes_in_trade_does_not_require_feature_run(self):
        rec = inspect_capability("minutes_in_trade")
        assert rec.requires_feature_run is False

    def test_native_primitive_does_not_require_feature_run(self):
        rec = inspect_capability("max_holding_bars")
        assert rec.taxonomy == CapabilityTaxonomy.NATIVE_PRIMITIVE
        assert rec.requires_feature_run is False

    def test_signal_field_does_not_require_feature_run(self):
        rec = inspect_capability("hmm_regime")
        assert rec.taxonomy == CapabilityTaxonomy.SIGNAL_FIELD
        assert rec.requires_feature_run is False

    def test_unknown_field_does_not_require_feature_run(self):
        """UNKNOWN fields return requires_feature_run=False (we can't classify them)."""
        rec = inspect_capability("completely_unknown_field_xyz")
        assert rec.taxonomy == CapabilityTaxonomy.UNKNOWN
        assert rec.requires_feature_run is False

    def test_ohlc_unknown_does_not_require_feature_run(self):
        """close/open are not in the catalog (UNKNOWN), so requires_feature_run=False.

        Use list_native_fields() to confirm close is natively available.
        """
        rec = inspect_capability("close")
        # close is not in any static capability registry — returns UNKNOWN
        assert rec.requires_feature_run is False


class TestListNativeFields:
    def test_contains_ohlcv(self):
        native = list_native_fields()
        for field in ("open", "high", "low", "close", "volume"):
            assert field in native, f"{field} missing from list_native_fields()"

    def test_contains_engine_state(self):
        native = list_native_fields()
        for field in ("bars_in_trade", "minutes_in_trade", "days_in_trade"):
            assert field in native, f"{field} missing from list_native_fields()"

    def test_contains_bar_metadata(self):
        native = list_native_fields()
        assert "instrument_id" in native
        assert "timestamp_utc" in native

    def test_excludes_feature_fields(self):
        native = list_native_fields()
        for field in ("day_of_week", "hour_of_day", "is_friday",
                      "rsi_14", "atr_14", "adx_14", "rvol_20",
                      "log_ret_1", "ema_slope_20", "breakout_20",
                      "minute_of_hour_sin", "hour_of_day_cos"):
            assert field not in native, f"Feature field {field} should not be in list_native_fields()"

    def test_returns_sorted_list(self):
        native = list_native_fields()
        assert native == sorted(native)

    def test_no_duplicates(self):
        native = list_native_fields()
        assert len(native) == len(set(native))

    def test_confirm_close_natively_available(self):
        """close is natively available even though inspect_capability returns UNKNOWN for it."""
        native = list_native_fields()
        assert "close" in native

    def test_minimum_field_count(self):
        """Should have at least the core OHLCV + 3 engine state + some metadata fields."""
        native = list_native_fields()
        assert len(native) >= 8


class TestRequiresFeatureRunInToDict:
    def test_to_dict_includes_requires_feature_run(self):
        rec = inspect_capability("rsi_14")
        d = rec.to_dict()
        assert "requires_feature_run" in d
        assert d["requires_feature_run"] is True

    def test_to_dict_state_marker_false(self):
        rec = inspect_capability("bars_in_trade")
        d = rec.to_dict()
        assert d["requires_feature_run"] is False
