"""Field classification constants for the strategy DSL.

Single source of truth for which fields are always available in the backtest
bar context versus which require a feature_run_id to be present.
"""
from __future__ import annotations

# Fields always present in raw bar dicts from DuckDB
NATIVE_BAR_FIELDS: frozenset[str] = frozenset({
    "open", "high", "low", "close", "volume",
    "instrument_id", "timestamp_utc", "timestamp",
    "source", "quality_flag", "timeframe", "derivation_version",
    "regime_label", "state_id",
})

# Fields injected by the backtest engine per-bar from StrategyState / trade lifecycle
ENGINE_STATE_FIELDS: frozenset[str] = frozenset({
    "bars_in_trade",
    "minutes_in_trade",
    "days_in_trade",
})

# Combined: always available in the backtest bar context regardless of feature_run_id
ALWAYS_AVAILABLE_FIELDS: frozenset[str] = NATIVE_BAR_FIELDS | ENGINE_STATE_FIELDS

# v1.0 base feature fields (require feature_run_id, available in v1.0+)
FEATURE_V1_0_FIELDS: frozenset[str] = frozenset({
    "log_ret_1", "log_ret_5", "log_ret_20", "rvol_20",
    "atr_14", "atr_pct_14", "rsi_14", "ema_slope_20",
    "ema_slope_50", "adx_14", "breakout_20", "session",
})

# v1.1+ calendar fields (require feature_run_id, feature version >= v1.1)
FEATURE_V1_1_FIELDS: frozenset[str] = frozenset({
    "day_of_week",
    "hour_of_day",
    "is_friday",
})

# v1.2+ cyclical fields (require feature_run_id, feature version >= v1.2)
FEATURE_V1_2_FIELDS: frozenset[str] = frozenset({
    "minute_of_hour", "week_of_year", "month_of_year",
    "minute_of_hour_sin", "minute_of_hour_cos",
    "hour_of_day_sin", "hour_of_day_cos",
    "day_of_week_sin", "day_of_week_cos",
    "week_of_year_sin", "week_of_year_cos",
    "month_of_year_sin", "month_of_year_cos",
})

# All fields that require a feature_run_id to be in the bar context
FEATURE_REQUIRED_FIELDS: frozenset[str] = (
    FEATURE_V1_0_FIELDS | FEATURE_V1_1_FIELDS | FEATURE_V1_2_FIELDS
)
