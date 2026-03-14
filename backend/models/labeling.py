"""Semantic regime labeling.

Maps latent HMM state IDs to human-readable regime names based on
per-state statistics produced during training.

The mapping is stored as JSON metadata on the ModelRecord, not baked into
the model artifact. This allows relabeling without re-training.

Labeling heuristics (thresholds are approximate and tunable):
  - mean_return > threshold   → BULL, else BEAR
  - mean_adx > adx_trend_th  → TREND, else RANGE or CHOPPY
  - mean_volatility high/low  → HIGH_VOL / LOW_VOL suffix
  - CHOPPY: low ADX + low directional persistence
"""
from __future__ import annotations

from typing import Any

# Semantic label constants (stable strings used in strategy rules)
TREND_BULL_LOW_VOL = "TREND_BULL_LOW_VOL"
TREND_BULL_HIGH_VOL = "TREND_BULL_HIGH_VOL"
TREND_BEAR_LOW_VOL = "TREND_BEAR_LOW_VOL"
TREND_BEAR_HIGH_VOL = "TREND_BEAR_HIGH_VOL"
RANGE_MEAN_REVERT = "RANGE_MEAN_REVERT"
CHOPPY_SIGNAL = "CHOPPY_SIGNAL"
CHOPPY_NOISE = "CHOPPY_NOISE"

ALL_LABELS = [
    TREND_BULL_LOW_VOL,
    TREND_BULL_HIGH_VOL,
    TREND_BEAR_LOW_VOL,
    TREND_BEAR_HIGH_VOL,
    RANGE_MEAN_REVERT,
    CHOPPY_SIGNAL,
    CHOPPY_NOISE,
]

# Default thresholds (tunable via parameters)
_DEFAULT_THRESHOLDS = {
    "adx_trend": 22.0,          # ADX above = trending, below = ranging/choppy
    "vol_high_pct": 0.60,       # top 60th percentile volatility = HIGH_VOL
    "directional_persistence_choppy": 0.52,  # below = CHOPPY
}


def auto_label_states(
    state_stats: list[dict[str, Any]],
    thresholds: dict[str, float] | None = None,
) -> dict[str, str]:
    """Automatically assign semantic labels to latent states.

    Args:
        state_stats: list of per-state dicts from hmm_regime._compute_state_stats()
        thresholds: override default thresholds

    Returns:
        dict mapping str(state_id) -> semantic label string.
        If num states > len(ALL_LABELS), extra states are labelled state_N.
    """
    th = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}

    # Filter states with data
    valid = [s for s in state_stats if s.get("n_bars", 0) > 0]
    if not valid:
        return {}

    # Compute thresholds from data distribution
    vols = [s.get("mean_volatility", 0.0) for s in valid]
    vol_median = _median(vols)

    adx_trend_th = th["adx_trend"]
    dir_choppy_th = th["directional_persistence_choppy"]

    used_labels: set[str] = set()
    label_map: dict[str, str] = {}

    for stat in sorted(state_stats, key=lambda s: s["state_id"]):
        sid = str(stat["state_id"])
        n = stat.get("n_bars", 0)
        if n == 0:
            label_map[sid] = f"state_{stat['state_id']}_empty"
            continue

        mean_ret = stat.get("mean_return", 0.0)
        mean_adx = stat.get("mean_adx", 0.0)
        mean_vol = stat.get("mean_volatility", 0.0)
        dir_pers = stat.get("directional_persistence", 0.5)

        is_bull = mean_ret >= 0
        is_trending = mean_adx >= adx_trend_th
        is_high_vol = mean_vol >= vol_median
        is_choppy = (not is_trending) and (dir_pers < dir_choppy_th)

        if is_choppy:
            # Choppy: distinguish signal vs pure noise by directional persistence
            candidates = [CHOPPY_SIGNAL, CHOPPY_NOISE]
        elif is_trending:
            if is_bull:
                candidates = [TREND_BULL_LOW_VOL, TREND_BULL_HIGH_VOL] if not is_high_vol else [TREND_BULL_HIGH_VOL, TREND_BULL_LOW_VOL]
            else:
                candidates = [TREND_BEAR_LOW_VOL, TREND_BEAR_HIGH_VOL] if not is_high_vol else [TREND_BEAR_HIGH_VOL, TREND_BEAR_LOW_VOL]
        else:
            # Non-trending, non-choppy = range/mean-reversion
            candidates = [RANGE_MEAN_REVERT, CHOPPY_SIGNAL]

        # Pick first unused candidate
        label = _pick_unused(candidates, used_labels)
        used_labels.add(label)
        label_map[sid] = label

    return label_map


def apply_label_map(
    label_map: dict[str, str],
    model_id: str,
    metadata_repo: Any,
) -> None:
    """Persist the label map to the model record and update regime_label strings."""
    metadata_repo.update_model(model_id, {"label_map_json": label_map})


def describe_label_map(
    label_map: dict[str, str],
    state_stats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a human-readable summary combining labels with per-state statistics."""
    stat_by_id = {str(s["state_id"]): s for s in state_stats}
    rows = []
    for sid, label in sorted(label_map.items(), key=lambda x: int(x[0])):
        stat = stat_by_id.get(sid, {})
        rows.append({
            "state_id": int(sid),
            "label": label,
            "n_bars": stat.get("n_bars", 0),
            "mean_return": stat.get("mean_return"),
            "mean_volatility": stat.get("mean_volatility"),
            "mean_adx": stat.get("mean_adx"),
            "directional_persistence": stat.get("directional_persistence"),
        })
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _pick_unused(candidates: list[str], used: set[str]) -> str:
    for c in candidates:
        if c not in used:
            return c
    # All preferred labels taken — return first candidate with suffix
    base = candidates[0]
    i = 2
    while f"{base}_{i}" in used:
        i += 1
    return f"{base}_{i}"
