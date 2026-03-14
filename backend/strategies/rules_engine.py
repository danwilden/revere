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

VALID_OPS = {"gt", "gte", "lt", "lte", "eq", "neq", "in"}


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
