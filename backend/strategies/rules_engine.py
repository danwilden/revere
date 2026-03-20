"""Rules DSL evaluator.

A rule node is a plain dict that can be one of:
  - Composite:  {"all": [node, ...]}, {"any": [node, ...]}, {"not": node}
  - Leaf:       {"field": str, "op": str, "value": scalar|list}
  - Field-to-field: {"field": str, "op": str, "field2": str}
  - Named ref:  {"ref": "condition_name"}

Supported ops: gt, gte, lt, lte, eq, neq, in
"""
from __future__ import annotations

from typing import Any

from backend.strategies.field_registry import ALWAYS_AVAILABLE_FIELDS

VALID_OPS = {"gt", "gte", "lt", "lte", "eq", "neq", "in"}

# All fields always available in the bar context (native bar cols + engine-injected state).
# validate_signal_fields() never flags these as unresolved.
# NOTE: day_of_week, hour_of_day, is_friday are NOT in this set — they are
# MARKET_FEATURE fields that require a feature_run_id.
STRATEGY_STATE_FIELDS: frozenset[str] = ALWAYS_AVAILABLE_FIELDS


def _resolve_value(context: dict, key: str) -> Any:
    """Look up a key in the context dict, raising ValueError if missing."""
    if key not in context:
        raise ValueError(f"Field '{key}' not found in context. Available: {sorted(context)}")
    return context[key]


def _apply_op(op: str, left: Any, right: Any) -> bool:
    """Apply a comparison op between two values. Raises ValueError on unknown op."""
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    if op == "eq":
        return left == right
    if op == "neq":
        return left != right
    if op == "in":
        return left in right
    raise ValueError(f"Unknown op '{op}'. Valid ops: {sorted(VALID_OPS)}")


def evaluate(
    node: dict,
    context: dict,
    named_conditions: dict[str, dict] | None = None,
) -> bool:
    """Recursively evaluate a rule node against context.

    Parameters
    ----------
    node:
        A rule node dict (see module docstring).
    context:
        Flat dict mapping field names to their current values. Typically the
        merged bar + features dict at the current timestep.
    named_conditions:
        Optional dict of condition_name -> rule_node for ``{"ref": ...}`` lookups.

    Returns
    -------
    bool
        True if the rule evaluates to True for the given context.

    Raises
    ------
    ValueError
        If a referenced field is missing from context, an unknown op is used,
        or a named ref cannot be resolved.
    """
    if named_conditions is None:
        named_conditions = {}

    # ------------------------------------------------------------------
    # Named reference
    # ------------------------------------------------------------------
    if "ref" in node:
        ref_name = node["ref"]
        if ref_name not in named_conditions:
            raise ValueError(
                f"Named condition '{ref_name}' not found. "
                f"Available: {sorted(named_conditions)}"
            )
        return evaluate(named_conditions[ref_name], context, named_conditions)

    # ------------------------------------------------------------------
    # Composite: all / any / not
    # ------------------------------------------------------------------
    if "all" in node:
        children = node["all"]
        if not isinstance(children, list):
            raise ValueError("'all' node must contain a list of child nodes")
        return all(evaluate(child, context, named_conditions) for child in children)

    if "any" in node:
        children = node["any"]
        if not isinstance(children, list):
            raise ValueError("'any' node must contain a list of child nodes")
        return any(evaluate(child, context, named_conditions) for child in children)

    if "not" in node:
        return not evaluate(node["not"], context, named_conditions)

    # ------------------------------------------------------------------
    # Leaf: field comparison
    # ------------------------------------------------------------------
    if "field" in node and "op" in node:
        op = node["op"]
        if op not in VALID_OPS:
            raise ValueError(f"Unknown op '{op}'. Valid ops: {sorted(VALID_OPS)}")

        left = _resolve_value(context, node["field"])

        # Field-to-field comparison
        if "field2" in node:
            right = _resolve_value(context, node["field2"])
        else:
            right = node.get("value")

        return _apply_op(op, left, right)

    raise ValueError(
        f"Unrecognised rule node structure: {node!r}. "
        "Expected keys: 'all', 'any', 'not', 'ref', or 'field'+'op'."
    )


# ---------------------------------------------------------------------------
# Static field validation (used at strategy validation time, not in hot path)
# ---------------------------------------------------------------------------

def _collect_fields(node: dict, unresolved: set[str], available: set[str]) -> None:
    """Recursively collect field references not in *available*."""
    if not isinstance(node, dict):
        return

    # Named refs — cannot statically validate, skip
    if "ref" in node:
        return

    # Composite: all / any
    if "all" in node:
        for child in node.get("all", []):
            _collect_fields(child, unresolved, available)
        return

    if "any" in node:
        for child in node.get("any", []):
            _collect_fields(child, unresolved, available)
        return

    if "not" in node:
        _collect_fields(node["not"], unresolved, available)
        return

    # Leaf node
    if "field" in node:
        field = node["field"]
        if field not in available:
            unresolved.add(field)
    if "field2" in node:
        field2 = node["field2"]
        if field2 not in available:
            unresolved.add(field2)


def validate_signal_fields(
    definition_json: dict,
    available_fields: set[str],
) -> list[str]:
    """Walk the rules DSL tree and return any field references not in *available_fields*.

    Fields in STRATEGY_STATE_FIELDS (bars_in_trade, minutes_in_trade, etc.) are
    always treated as available — they are injected by the backtest engine and
    are never flagged as unresolved.

    Used at strategy validation time (not in the hot backtest path).

    Parameters
    ----------
    definition_json:
        A rules-engine strategy definition dict (same shape as RulesStrategy input).
    available_fields:
        Set of field names that will be present in the bar context at runtime.

    Returns
    -------
    list[str]:
        Deduplicated list of field names referenced in the DSL but not found
        in *available_fields*.  Empty list means all references resolve.
    """
    unresolved: set[str] = set()

    # Walk all rule-bearing keys in the definition
    for key in ("entry_long", "entry_short", "exit"):
        node = definition_json.get(key)
        if node is not None:
            _collect_fields(node, unresolved, available_fields)

    # Walk named_conditions too
    named = definition_json.get("named_conditions", {})
    if isinstance(named, dict):
        for _name, node in named.items():
            if isinstance(node, dict):
                _collect_fields(node, unresolved, available_fields)

    # State fields are always available — never report them as unresolved
    unresolved -= STRATEGY_STATE_FIELDS

    return sorted(unresolved)
