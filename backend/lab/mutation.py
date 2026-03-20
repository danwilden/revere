"""Strategy definition mutation utilities for Phase 5B.

All functions return new dicts — input definitions are never mutated in place.
"""
from __future__ import annotations

import copy
import random
from typing import Any


# ---------------------------------------------------------------------------
# Parameter perturbation
# ---------------------------------------------------------------------------

def perturb_parameters(
    definition: dict[str, Any],
    magnitude: float = 0.2,
) -> dict[str, Any]:
    """Return a copy of *definition* with ATR multipliers randomly perturbed.

    Args:
        definition: Rules-engine strategy definition dict.
        magnitude:  Fractional ±perturbation applied to each multiplier.
                    Clamped to [0.01, 1.0].

    Returns:
        New dict with perturbed stop_atr_multiplier and take_profit_atr_multiplier.
        position_size_units is always set to 1000.

    Invariant:
        stop_atr_multiplier < take_profit_atr_multiplier — values are swapped
        if this would be violated after perturbation.
    """
    magnitude = max(0.01, min(1.0, magnitude))
    result = copy.deepcopy(definition)

    stop_val: float = float(result.get("stop_atr_multiplier", 2.0))
    tp_val: float = float(result.get("take_profit_atr_multiplier", 3.0))

    stop_dir = random.choice([1, -1])
    tp_dir = random.choice([1, -1])

    stop_val = stop_val + stop_dir * magnitude * stop_val
    tp_val = tp_val + tp_dir * magnitude * tp_val

    # Clamp to valid ranges
    stop_val = max(0.5, min(6.0, stop_val))
    tp_val = max(0.5, min(8.0, tp_val))

    # Enforce stop < take_profit invariant
    if stop_val >= tp_val:
        stop_val, tp_val = tp_val, stop_val
    # If still equal after swap (only possible at boundaries), nudge
    if stop_val >= tp_val:
        tp_val = min(8.0, stop_val + 0.1)

    result["stop_atr_multiplier"] = stop_val
    result["take_profit_atr_multiplier"] = tp_val
    result["position_size_units"] = 1000

    return result


# ---------------------------------------------------------------------------
# Rule substitution
# ---------------------------------------------------------------------------

def _collect_leaves(node: Any, leaves: list[tuple[list[Any], int]]) -> None:
    """Depth-first traversal — append (parent_list, index) for each leaf."""
    if not isinstance(node, dict):
        return
    if "field" in node or "ref" in node:
        # Leaf — parent is responsible for inserting via (container, idx)
        # We handle this by collecting at the parent level below
        return
    if "all" in node:
        for i, child in enumerate(node["all"]):
            if isinstance(child, dict) and ("field" in child or "ref" in child):
                leaves.append((node["all"], i))
            else:
                _collect_leaves(child, leaves)
    elif "any" in node:
        for i, child in enumerate(node["any"]):
            if isinstance(child, dict) and ("field" in child or "ref" in child):
                leaves.append((node["any"], i))
            else:
                _collect_leaves(child, leaves)
    elif "not" in node:
        child = node["not"]
        if isinstance(child, dict) and ("field" in child or "ref" in child):
            # Can't replace inside a "not" without wrapping — treat as leaf ref
            leaves.append((None, -1))  # sentinel: not replaceable this way
        else:
            _collect_leaves(child, leaves)


def _collect_leaf_positions(root: Any) -> list[tuple[list[Any], int]]:
    """Return (list_container, index) pairs for all leaf nodes under root.

    A leaf is a dict with a "field" or "ref" key.  "not" nodes wrapping a
    single leaf are included as a sentinel (None, -1) to maintain index
    parity — but substitution skips those positions to avoid corrupting the
    "not" structure.  In practice rule trees are nearly always "all"/"any"
    composites and this edge case is rare.
    """
    if isinstance(root, dict) and ("field" in root or "ref" in root):
        # The root itself is a leaf — represented by a sentinel meaning
        # "the caller must replace the root value directly"
        return [(None, -1)]
    leaves: list[tuple[list[Any], int]] = []
    _collect_leaves(root, leaves)
    return leaves


def substitute_rule(
    definition: dict[str, Any],
    rule_index: int,
    new_rule: dict[str, Any],
    target: str = "entry_long",
) -> dict[str, Any]:
    """Replace a leaf node in the target rule tree by zero-based DFS index.

    Args:
        definition:  Rules-engine strategy definition dict.
        rule_index:  Zero-based index into the depth-first leaf traversal.
        new_rule:    Replacement leaf node dict.
        target:      Top-level key to modify ("entry_long", "entry_short", "exit").

    Returns:
        New dict with the leaf at *rule_index* replaced by *new_rule*.

    Raises:
        KeyError:   If *target* is not present in *definition*.
        IndexError: If *rule_index* is out of range.
    """
    if target not in definition:
        raise KeyError(f"Target '{target}' not found in strategy definition")

    result = copy.deepcopy(definition)
    root = result[target]
    leaves = _collect_leaf_positions(root)

    # Filter out sentinel entries (non-replaceable positions)
    replaceable = [(lst, idx) for (lst, idx) in leaves if lst is not None]

    if rule_index >= len(replaceable):
        raise IndexError(
            f"rule_index {rule_index} out of range; "
            f"target '{target}' has {len(replaceable)} replaceable leaves"
        )

    container, idx = replaceable[rule_index]
    container[idx] = new_rule
    return result


# ---------------------------------------------------------------------------
# Regime filter injection
# ---------------------------------------------------------------------------

def inject_regime_filter(
    definition: dict[str, Any],
    regime_label: str,
    target: str = "entry_long",
) -> dict[str, Any]:
    """Wrap (or prepend to) the target rule tree with a regime_label filter.

    Behaviour:
    - Validates *regime_label* against ALL_LABELS (raises ValueError if invalid).
    - If the target tree is an "all" composite, prepends the regime leaf to its
      list (or replaces an existing regime_label leaf in that list).
    - Otherwise wraps the original tree: {"all": [regime_leaf, <original>]}.
    - If target is not present in definition, raises KeyError.

    Returns a new dict; the input is never mutated.
    """
    from backend.models.labeling import ALL_LABELS

    if regime_label not in ALL_LABELS:
        raise ValueError(
            f"Invalid regime_label '{regime_label}'. "
            f"Must be one of: {ALL_LABELS}"
        )

    if target not in definition:
        raise KeyError(f"Target '{target}' not found in strategy definition")

    result = copy.deepcopy(definition)
    original = result[target]

    regime_leaf: dict[str, Any] = {
        "field": "regime_label",
        "op": "eq",
        "value": regime_label,
    }

    def _is_regime_leaf(node: Any) -> bool:
        return (
            isinstance(node, dict)
            and node.get("field") == "regime_label"
        )

    if isinstance(original, dict) and "all" in original:
        # Existing "all" composite — remove any prior regime leaf then prepend
        filtered = [n for n in original["all"] if not _is_regime_leaf(n)]
        original["all"] = [regime_leaf] + filtered
        result[target] = original
    else:
        # Wrap original tree
        result[target] = {"all": [regime_leaf, original]}

    return result
