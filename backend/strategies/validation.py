"""Strategy definition validators.

validate_rules_strategy(definition_json) -> list[str]
    Validates a rules-engine strategy definition dict.
    Returns a list of error strings; empty list means valid.

validate_python_strategy(definition_json) -> list[str]
    Validates a code-first strategy definition dict.
    Returns a list of error strings; empty list means valid.
"""
from __future__ import annotations

from typing import Any

from backend.strategies.rules_engine import VALID_OPS

# Required top-level keys for a rules strategy
_RULES_REQUIRED = {"entry_long", "exit"}


def _validate_rule_node(node: Any, named_conditions: dict, path: str) -> list[str]:
    """Recursively validate a rule node, collecting all errors."""
    errors: list[str] = []

    if not isinstance(node, dict):
        errors.append(f"{path}: rule node must be a dict, got {type(node).__name__}")
        return errors

    if "ref" in node:
        ref_name = node["ref"]
        if not isinstance(ref_name, str) or not ref_name:
            errors.append(f"{path}: 'ref' value must be a non-empty string")
        elif ref_name not in named_conditions:
            errors.append(
                f"{path}: ref '{ref_name}' is not defined in named_conditions"
            )
        return errors

    if "all" in node:
        children = node["all"]
        if not isinstance(children, list):
            errors.append(f"{path}.all: must be a list")
        else:
            for i, child in enumerate(children):
                errors.extend(
                    _validate_rule_node(child, named_conditions, f"{path}.all[{i}]")
                )
        return errors

    if "any" in node:
        children = node["any"]
        if not isinstance(children, list):
            errors.append(f"{path}.any: must be a list")
        else:
            for i, child in enumerate(children):
                errors.extend(
                    _validate_rule_node(child, named_conditions, f"{path}.any[{i}]")
                )
        return errors

    if "not" in node:
        errors.extend(_validate_rule_node(node["not"], named_conditions, f"{path}.not"))
        return errors

    # Leaf node
    if "field" not in node:
        errors.append(f"{path}: leaf node missing 'field' key")
        return errors

    if "op" not in node:
        errors.append(f"{path}: leaf node missing 'op' key")
        return errors

    op = node["op"]
    if op not in VALID_OPS:
        errors.append(
            f"{path}: unknown op '{op}'. Valid ops: {sorted(VALID_OPS)}"
        )

    if "field2" not in node and "value" not in node:
        errors.append(f"{path}: leaf node must have either 'value' or 'field2'")

    return errors


def validate_rules_strategy(definition_json: dict) -> list[str]:
    """Validate a rules-engine strategy definition.

    Required keys: entry_long (rule node), exit (rule node).
    Optional keys: entry_short (rule node or null), stop_atr_multiplier,
                   take_profit_atr_multiplier, cooldown_hours,
                   position_size_units, named_conditions.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    if not isinstance(definition_json, dict):
        return ["definition_json must be a dict"]

    # Named conditions — validate first so refs can be resolved
    named_conditions: dict = {}
    if "named_conditions" in definition_json:
        nc = definition_json["named_conditions"]
        if not isinstance(nc, dict):
            errors.append("named_conditions must be a dict")
        else:
            named_conditions = nc
            for cname, cnode in nc.items():
                errors.extend(
                    _validate_rule_node(cnode, nc, f"named_conditions.{cname}")
                )

    # Required rule nodes
    for key in _RULES_REQUIRED:
        if key not in definition_json:
            errors.append(f"Missing required field: '{key}'")
        else:
            errors.extend(
                _validate_rule_node(definition_json[key], named_conditions, key)
            )

    # Optional entry_short
    if "entry_short" in definition_json and definition_json["entry_short"] is not None:
        errors.extend(
            _validate_rule_node(
                definition_json["entry_short"], named_conditions, "entry_short"
            )
        )

    # Numeric optional fields
    for numeric_key in (
        "stop_atr_multiplier",
        "take_profit_atr_multiplier",
        "cooldown_hours",
        "position_size_units",
    ):
        if numeric_key in definition_json:
            val = definition_json[numeric_key]
            if not isinstance(val, (int, float)):
                errors.append(f"'{numeric_key}' must be a number, got {type(val).__name__}")

    return errors


def validate_python_strategy(definition_json: dict) -> list[str]:
    """Validate a code-first Python strategy definition.

    Required keys: code (non-empty string).
    Optional keys: class_name (string).

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    if not isinstance(definition_json, dict):
        return ["definition_json must be a dict"]

    if "code" not in definition_json:
        errors.append("Missing required field: 'code'")
    else:
        code = definition_json["code"]
        if not isinstance(code, str):
            errors.append("'code' must be a string")
        elif not code.strip():
            errors.append("'code' must be a non-empty string")

    if "class_name" in definition_json:
        class_name = definition_json["class_name"]
        if not isinstance(class_name, str) or not class_name.strip():
            errors.append("'class_name' must be a non-empty string if provided")

    return errors
