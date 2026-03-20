"""Capability taxonomy and inspection for the strategy DSL.

Classifies fields and primitives into four categories so agents and tooling
can route missing capabilities to the correct subsystem rather than stalling.

CapabilityTaxonomy:
  MARKET_FEATURE   — derived from the feature pipeline (compute.py default_v1)
  STATE_MARKER     — injected by the engine from StrategyState / trade lifecycle
  NATIVE_PRIMITIVE — first-class definition_json top-level primitives
  SIGNAL_FIELD     — pre-joined materialized signal fields (signal_ids in backtest)
  UNKNOWN          — not recognized; requires agent investigation

Feature-run version awareness:
  Pass feature_run_version to inspect() / inspect_capability() to get version-gated
  availability. When the feature run predates a capability's minimum required version,
  the record is returned with available=False and a remediation hint.

  CALENDAR_FEATURE_MIN_VERSION  = "v1.1"  — day_of_week, hour_of_day, is_friday
  CYCLICAL_FEATURE_MIN_VERSION  = "v1.2"  — cyclical sin/cos encodings + week/month raw
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class CapabilityTaxonomy(str, Enum):
    MARKET_FEATURE = "market_feature"
    STATE_MARKER = "state_marker"
    NATIVE_PRIMITIVE = "native_primitive"
    SIGNAL_FIELD = "signal_field"
    UNKNOWN = "unknown"


# Minimum feature-run versions for capability groups
CALENDAR_FEATURE_MIN_VERSION = "v1.1"
CYCLICAL_FEATURE_MIN_VERSION = "v1.2"


def _parse_version(v: str) -> tuple[int, int]:
    """Parse 'v1.2' → (1, 2). Used for version comparisons."""
    parts = v.lstrip("v").split(".")
    return (int(parts[0]), int(parts[1]))


def _version_gte(a: str, b: str) -> bool:
    """Return True if version a >= version b."""
    return _parse_version(a) >= _parse_version(b)


# ---------------------------------------------------------------------------
# Static registries
# ---------------------------------------------------------------------------

# Fields produced by the feature pipeline (compute.py default_v1, v1.1+, v1.2+).
# Each entry may carry an optional "min_version" key.  When absent, the field
# is available in v1.0 (the base set).
_MARKET_FEATURES: dict[str, dict] = {
    "log_ret_1":    {"description": "1-bar log return", "available": True},
    "log_ret_5":    {"description": "5-bar log return", "available": True},
    "log_ret_20":   {"description": "20-bar log return", "available": True},
    "rvol_20":      {"description": "20-bar rolling volatility (annualised)", "available": True},
    "atr_14":       {"description": "Average True Range (14)", "available": True},
    "atr_pct_14":   {"description": "ATR as fraction of close", "available": True},
    "rsi_14":       {"description": "RSI (14)", "available": True},
    "ema_slope_20": {"description": "EMA-20 slope normalised by close", "available": True},
    "ema_slope_50": {"description": "EMA-50 slope normalised by close", "available": True},
    "adx_14":       {"description": "ADX (14)", "available": True},
    "breakout_20":  {"description": "Close vs 20-bar high/low range (0=low, 1=high)", "available": True},
    "session":      {"description": "Session: 0=Asia, 1=London, 2=NY, 3=London/NY overlap", "available": True},
    # v1.1 additions — calendar-derived raw fields
    "day_of_week":  {
        "description": "Day of week: 0=Monday … 6=Sunday (UTC timestamp-derived)",
        "available": True,
        "min_version": CALENDAR_FEATURE_MIN_VERSION,
    },
    "hour_of_day":  {
        "description": "UTC hour: 0–23 (timestamp-derived)",
        "available": True,
        "min_version": CALENDAR_FEATURE_MIN_VERSION,
    },
    "is_friday":    {
        "description": "1 if Friday, else 0 (timestamp-derived)",
        "available": True,
        "min_version": CALENDAR_FEATURE_MIN_VERSION,
    },
    # v1.2 additions — new raw calendar fields
    "minute_of_hour": {
        "description": "Minute of hour: 0–59 (UTC timestamp-derived)",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "week_of_year": {
        "description": "ISO week number: 1–53 (UTC timestamp-derived)",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "month_of_year": {
        "description": "Month of year: 1–12 (UTC timestamp-derived)",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    # v1.2 additions — cyclical sin/cos encodings (period documented in description)
    "minute_of_hour_sin": {
        "description": "sin(2π·minute_of_hour/60) — cyclical minute encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "minute_of_hour_cos": {
        "description": "cos(2π·minute_of_hour/60) — cyclical minute encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "hour_of_day_sin": {
        "description": "sin(2π·hour_of_day/24) — cyclical hour encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "hour_of_day_cos": {
        "description": "cos(2π·hour_of_day/24) — cyclical hour encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "day_of_week_sin": {
        "description": "sin(2π·day_of_week/7) — cyclical day-of-week encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "day_of_week_cos": {
        "description": "cos(2π·day_of_week/7) — cyclical day-of-week encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "week_of_year_sin": {
        "description": "sin(2π·(week_of_year-1)/52) — cyclical week-of-year encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "week_of_year_cos": {
        "description": "cos(2π·(week_of_year-1)/52) — cyclical week-of-year encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "month_of_year_sin": {
        "description": "sin(2π·(month_of_year-1)/12) — cyclical month-of-year encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
    "month_of_year_cos": {
        "description": "cos(2π·(month_of_year-1)/12) — cyclical month-of-year encoding",
        "available": True,
        "min_version": CYCLICAL_FEATURE_MIN_VERSION,
    },
}

# Fields injected from StrategyState / trade lifecycle by the backtest engine
_STATE_MARKERS: dict[str, dict] = {
    "bars_in_trade":    {"description": "Bars elapsed since trade entry; 0 when flat", "available": True},
    "minutes_in_trade": {"description": "Wall-clock minutes since trade entry; 0.0 when flat", "available": True},
    "days_in_trade":    {"description": "Calendar days since trade entry (minutes/1440); 0.0 when flat", "available": True},
}

# First-class definition_json top-level primitives (checked before the exit rule)
_NATIVE_PRIMITIVES: dict[str, dict] = {
    "max_holding_bars":    {"description": "Exit when bars_in_trade >= this value (positive int)", "available": True},
    "exit_before_weekend": {"description": "Exit on Friday bar at or after 20:00 UTC (bool)", "available": True},
    "cooldown_hours":      {"description": "Post-exit cooldown before re-entry (float, hours)", "available": True},
    "stop_atr_multiplier": {"description": "ATR multiplier for stop-loss placement (float)", "available": True},
    "take_profit_atr_multiplier": {"description": "ATR multiplier for take-profit placement (float)", "available": True},
}

# Well-known signal field names (not exhaustive — signals are dynamic)
_SIGNAL_FIELDS: dict[str, dict] = {
    "hmm_regime":               {"description": "HMM regime label (from HMM_REGIME signal)", "available": False},
    "automl_direction_prob":    {"description": "AutoML direction probability (AUTOML_DIRECTION_PROB signal)", "available": False},
    "automl_return_bucket":     {"description": "AutoML return bucket (AUTOML_RETURN_BUCKET signal)", "available": False},
    "risk_filter":              {"description": "Risk filter gate: 1=blocked, 0=allowed (RISK_FILTER signal)", "available": False},
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class CapabilityRecord:
    """Describes a single known capability (field or primitive)."""

    def __init__(
        self,
        name: str,
        taxonomy: CapabilityTaxonomy,
        description: str,
        available: bool,
        resolution_hint: str = "",
        requires_feature_run: bool = False,
    ) -> None:
        self.name = name
        self.taxonomy = taxonomy
        self.description = description
        self.available = available
        self.resolution_hint = resolution_hint
        self.requires_feature_run = requires_feature_run

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "taxonomy": self.taxonomy.value,
            "description": self.description,
            "available": self.available,
            "resolution_hint": self.resolution_hint,
            "requires_feature_run": self.requires_feature_run,
        }


class CapabilityInspector:
    """Classify a field name or primitive into the capability taxonomy.

    Used by agents to understand what a missing field is and how to resolve it,
    and by the API route /api/capabilities to serve capability discovery.

    Pass feature_run_version (e.g. "v1.0", "v1.1", "v1.2") to get version-gated
    availability for market features.  When omitted, the static registry value is
    used (i.e. the field is treated as available if it exists in the registry).
    """

    def inspect(
        self,
        field_name: str,
        feature_run_version: str | None = None,
    ) -> CapabilityRecord:
        """Return the CapabilityRecord for the given field name.

        If feature_run_version is provided, market-feature availability is gated
        against the field's minimum required version.  A clear remediation hint is
        returned when the run predates the required version.

        If the field is not in any static registry, returns UNKNOWN taxonomy.
        """
        if field_name in _MARKET_FEATURES:
            meta = _MARKET_FEATURES[field_name]
            min_ver = meta.get("min_version")

            # Determine availability considering the supplied feature-run version
            if feature_run_version is not None and min_ver is not None:
                available = _version_gte(feature_run_version, min_ver)
            else:
                available = meta["available"]

            if available:
                hint = (
                    f"Requires feature run at {min_ver}+. Recompute the feature pipeline "
                    f"to get a compatible feature_run_id."
                    if min_ver else
                    "Available in the base feature set (v1.0+)."
                )
            else:
                hint = (
                    f"This field requires feature pipeline version {min_ver} or later. "
                    f"Your feature run is {feature_run_version}. "
                    f"Recompute the feature pipeline and use the new feature_run_id."
                )

            return CapabilityRecord(
                name=field_name,
                taxonomy=CapabilityTaxonomy.MARKET_FEATURE,
                description=meta["description"],
                available=available,
                resolution_hint=hint,
                requires_feature_run=True,
            )

        if field_name in _STATE_MARKERS:
            meta = _STATE_MARKERS[field_name]
            return CapabilityRecord(
                name=field_name,
                taxonomy=CapabilityTaxonomy.STATE_MARKER,
                description=meta["description"],
                available=meta["available"],
                resolution_hint="Injected automatically by the backtest engine. Use directly in exit rules.",
                requires_feature_run=False,
            )

        if field_name in _NATIVE_PRIMITIVES:
            meta = _NATIVE_PRIMITIVES[field_name]
            return CapabilityRecord(
                name=field_name,
                taxonomy=CapabilityTaxonomy.NATIVE_PRIMITIVE,
                description=meta["description"],
                available=meta["available"],
                resolution_hint="Set as a top-level key in the strategy definition_json (not inside a rule node).",
                requires_feature_run=False,
            )

        if field_name in _SIGNAL_FIELDS:
            meta = _SIGNAL_FIELDS[field_name]
            return CapabilityRecord(
                name=field_name,
                taxonomy=CapabilityTaxonomy.SIGNAL_FIELD,
                description=meta["description"],
                available=meta["available"],
                resolution_hint="Materialize a signal and pass its signal_id in the backtest signal_ids list.",
                requires_feature_run=False,
            )

        return CapabilityRecord(
            name=field_name,
            taxonomy=CapabilityTaxonomy.UNKNOWN,
            description="Field not found in any static registry.",
            available=False,
            resolution_hint=(
                "Check if this is a discovered feature in the feature library, "
                "a materialized signal, or a typo. Use the feature discovery agent "
                "to propose and register new market features."
            ),
            requires_feature_run=False,
        )

    def list_all(self, taxonomy: CapabilityTaxonomy | None = None) -> list[CapabilityRecord]:
        """List all known capabilities, optionally filtered by taxonomy."""
        results: list[CapabilityRecord] = []

        def _add(registry: dict, tax: CapabilityTaxonomy, hint: str, requires_feature_run: bool = False) -> None:
            for name, meta in registry.items():
                results.append(CapabilityRecord(
                    name=name,
                    taxonomy=tax,
                    description=meta["description"],
                    available=meta["available"],
                    resolution_hint=hint,
                    requires_feature_run=requires_feature_run,
                ))

        _add(_MARKET_FEATURES, CapabilityTaxonomy.MARKET_FEATURE,
             "Recompute the feature pipeline (v1.1+/v1.2+) to include this field.",
             requires_feature_run=True)
        _add(_STATE_MARKERS, CapabilityTaxonomy.STATE_MARKER,
             "Injected automatically by the backtest engine.",
             requires_feature_run=False)
        _add(_NATIVE_PRIMITIVES, CapabilityTaxonomy.NATIVE_PRIMITIVE,
             "Set as a top-level key in definition_json.",
             requires_feature_run=False)
        _add(_SIGNAL_FIELDS, CapabilityTaxonomy.SIGNAL_FIELD,
             "Materialize a signal and pass its signal_id in the backtest signal_ids list.",
             requires_feature_run=False)

        if taxonomy is not None:
            results = [r for r in results if r.taxonomy == taxonomy]

        return results


# Module-level singleton
_inspector = CapabilityInspector()


def inspect_capability(
    field_name: str,
    feature_run_version: str | None = None,
) -> CapabilityRecord:
    """Convenience function: classify a single field name.

    Args:
        field_name: The field or capability name to inspect.
        feature_run_version: Optional feature-run version string (e.g. "v1.1").
            When provided, market-feature availability is gated against the field's
            minimum required version, and a remediation hint is returned for legacy runs.
    """
    return _inspector.inspect(field_name, feature_run_version=feature_run_version)


def list_capabilities(taxonomy: CapabilityTaxonomy | None = None) -> list[CapabilityRecord]:
    """Convenience function: list all known capabilities."""
    return _inspector.list_all(taxonomy)


def list_native_fields() -> list[str]:
    """Return all fields unconditionally available in the backtest bar context.

    These fields are present on every bar regardless of whether a feature_run_id
    is supplied. Includes native bar columns (OHLCV, metadata) and engine-injected
    trade lifecycle markers (bars_in_trade, minutes_in_trade, days_in_trade).
    """
    from backend.strategies.field_registry import ALWAYS_AVAILABLE_FIELDS
    return sorted(ALWAYS_AVAILABLE_FIELDS)
