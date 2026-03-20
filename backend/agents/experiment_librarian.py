"""Experiment librarian — pure Python utilities for Phase 5F.

No LLM calls, no I/O side effects (except where a caller explicitly passes a
registry).  All functions are deterministic and testable without mocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.lab.experiment_registry import ExperimentRecord, ExperimentRegistry, ExperimentStatus


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LineageGraph:
    records: list  # list[ExperimentRecord], sorted generation 0 → N
    session_id: str
    root_id: str
    leaf_id: str

    @property
    def depth(self) -> int:
        return len(self.records)

    def get_by_id(self, experiment_id: str) -> ExperimentRecord | None:
        for r in self.records:
            if r.id == experiment_id:
                return r
        return None


@dataclass
class NearDupeResult:
    is_near_dupe: bool
    matched_experiment_id: str | None
    jaccard_score: float
    block_reason: str | None  # None when is_near_dupe=False


# ---------------------------------------------------------------------------
# Function 1: build_lineage_graph
# ---------------------------------------------------------------------------

def build_lineage_graph(experiment_id: str, registry: Any) -> LineageGraph:
    """Walk the parent chain and return a LineageGraph rooted at the seed.

    Parameters
    ----------
    experiment_id:
        The leaf (most recent) experiment ID to start from.
    registry:
        An ExperimentRegistry instance.

    Returns
    -------
    LineageGraph
        Records in generation order (root first, leaf last).
    """
    lineage_list: list[ExperimentRecord] = registry.get_lineage(experiment_id)
    return LineageGraph(
        records=lineage_list,
        session_id=lineage_list[0].session_id,
        root_id=lineage_list[0].id,
        leaf_id=experiment_id,
    )


# ---------------------------------------------------------------------------
# Function 2: extract_rule_nodes
# ---------------------------------------------------------------------------

def _collect_leaf_fingerprints(node: Any, out: set) -> None:
    """Depth-first traversal — build fingerprint strings for every leaf node.

    Composite nodes (all, any, not) are structural scaffolding and are NOT
    fingerprinted themselves.  Only the three leaf variants are recorded:
    - field/op/value  leaf
    - field/op/field2 leaf (field-to-field comparison)
    - ref             leaf (named condition reference — treated as atomic)
    """
    if not isinstance(node, dict):
        return

    # Named reference — atomic leaf
    if "ref" in node:
        out.add(f"ref:{node['ref']}")
        return

    # Composite: all
    if "all" in node:
        for child in node.get("all", []):
            _collect_leaf_fingerprints(child, out)
        return

    # Composite: any
    if "any" in node:
        for child in node.get("any", []):
            _collect_leaf_fingerprints(child, out)
        return

    # Composite: not
    if "not" in node:
        _collect_leaf_fingerprints(node["not"], out)
        return

    # Leaf: field comparison
    if "field" in node and "op" in node:
        field = node["field"]
        op = node["op"]
        if "field2" in node:
            out.add(f"field:{field}|op:{op}|field2:{node['field2']}")
        else:
            value = node.get("value")
            if isinstance(value, list):
                val_str = sorted(str(v) for v in value)
            else:
                val_str = value
            out.add(f"field:{field}|op:{op}|val:{val_str}")


def extract_rule_nodes(definition_json: dict) -> frozenset:
    """Extract canonical fingerprint strings for every leaf condition node.

    Walks entry_long, entry_short, exit, and all named_conditions values.

    Parameters
    ----------
    definition_json:
        A rules-engine strategy definition dict.

    Returns
    -------
    frozenset[str]
        Fingerprint strings for every leaf node.  Empty definition → frozenset().
    """
    out: set[str] = set()

    for key in ("entry_long", "entry_short", "exit"):
        node = definition_json.get(key)
        if node is not None:
            _collect_leaf_fingerprints(node, out)

    named = definition_json.get("named_conditions", {})
    if isinstance(named, dict):
        for _name, node in named.items():
            if node is not None:
                _collect_leaf_fingerprints(node, out)

    return frozenset(out)


# ---------------------------------------------------------------------------
# Function 3: jaccard_similarity
# ---------------------------------------------------------------------------

def jaccard_similarity(a: frozenset, b: frozenset) -> float:
    """Standard Jaccard similarity between two frozensets.

    Returns 0.0 if both empty.  Returns 1.0 if both identical and non-empty.
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union


# ---------------------------------------------------------------------------
# Function 4: check_near_dupe
# ---------------------------------------------------------------------------

def check_near_dupe(
    target_definition_json: dict,
    candidates: list,  # list[tuple[str, dict]] — (experiment_id, definition_json)
    threshold: float = 0.85,
) -> NearDupeResult:
    """Check whether *target* is a near-duplicate of any candidate.

    Computes Jaccard similarity between target and each candidate in order.
    Returns the FIRST match at or above *threshold*.

    Parameters
    ----------
    target_definition_json:
        The strategy definition to test.
    candidates:
        List of (experiment_id, definition_json) pairs to compare against.
    threshold:
        Jaccard score at or above which two strategies are considered near-dupes.

    Returns
    -------
    NearDupeResult
        is_near_dupe=True with matched_experiment_id and jaccard_score when a
        match is found, otherwise is_near_dupe=False with score 0.0.
    """
    target_fp = extract_rule_nodes(target_definition_json)

    for exp_id, definition_json in candidates:
        candidate_fp = extract_rule_nodes(definition_json)
        score = jaccard_similarity(target_fp, candidate_fp)
        if score >= threshold:
            return NearDupeResult(
                is_near_dupe=True,
                matched_experiment_id=exp_id,
                jaccard_score=score,
                block_reason=(
                    f"Near-duplicate of experiment {exp_id} "
                    f"(Jaccard={score:.3f} >= threshold={threshold})"
                ),
            )

    return NearDupeResult(
        is_near_dupe=False,
        matched_experiment_id=None,
        jaccard_score=0.0,
        block_reason=None,
    )


# ---------------------------------------------------------------------------
# Function 5: find_near_dupes_in_context
# ---------------------------------------------------------------------------

def find_near_dupes_in_context(
    target_experiment_id: str,
    target_definition_json: dict,
    registry: Any,       # ExperimentRegistry
    metadata_repo: Any,  # MetadataRepository — used for get_strategy() only
    instrument: str,
    threshold: float = 0.85,
) -> NearDupeResult:
    """Check for near-dupes against lineage members and VALIDATED experiments.

    Build pool:
    1. Lineage members of the target that have a strategy_id (excluding target).
    2. VALIDATED experiments for *instrument* (up to 200, excluding target).

    For each pool member, load the strategy definition via metadata_repo and
    compare using check_near_dupe.

    Parameters
    ----------
    target_experiment_id:
        The experiment being checked for duplication.
    target_definition_json:
        The strategy definition of the target experiment.
    registry:
        ExperimentRegistry instance.
    metadata_repo:
        MetadataRepository used to call get_strategy().
    instrument:
        Instrument to filter VALIDATED experiments by.
    threshold:
        Jaccard similarity threshold.

    Returns
    -------
    NearDupeResult
    """
    # 1. Get lineage chain
    lineage: list[ExperimentRecord] = registry.get_lineage(target_experiment_id)

    # Collect (exp_id, strategy_id) pairs from lineage, excluding the target
    pool_items: list[tuple[str, str]] = []  # (experiment_id, strategy_id)
    seen_ids: set[str] = set()

    for record in lineage:
        if record.id == target_experiment_id:
            continue
        if record.strategy_id is not None and record.id not in seen_ids:
            pool_items.append((record.id, record.strategy_id))
            seen_ids.add(record.id)

    # 2. Collect VALIDATED experiments for this instrument
    validated: list[ExperimentRecord] = registry.list_recent(
        limit=200,
        instrument=instrument,
        status=ExperimentStatus.VALIDATED,
    )
    for record in validated:
        if record.id == target_experiment_id:
            continue
        if record.strategy_id is not None and record.id not in seen_ids:
            pool_items.append((record.id, record.strategy_id))
            seen_ids.add(record.id)

    # 3. Load strategy definitions and build candidates list
    candidates: list[tuple[str, dict]] = []
    for exp_id, strategy_id in pool_items:
        strategy_record = metadata_repo.get_strategy(strategy_id)
        if strategy_record is None:
            continue
        definition = strategy_record.get("definition_json")
        if definition is None:
            continue
        candidates.append((exp_id, definition))

    return check_near_dupe(target_definition_json, candidates, threshold)


# ---------------------------------------------------------------------------
# Function 6: generate_lineage_report
# ---------------------------------------------------------------------------

def generate_lineage_report(graph: LineageGraph) -> str:
    """Render a human-readable lineage report string.

    Pure string formatting — no I/O.

    Parameters
    ----------
    graph:
        A LineageGraph returned by build_lineage_graph().

    Returns
    -------
    str
        Multi-line formatted report.
    """
    lines: list[str] = [
        f"Lineage Report — Session: {graph.session_id}",
        f"Depth: {graph.depth} generation(s)",
        f"Root: {graph.root_id}",
    ]

    for i, record in enumerate(graph.records):
        short_id = f"{record.id[:8]}..."
        status = record.status.value if hasattr(record.status, "value") else str(record.status)

        score = record.score if record.score is not None else "—"
        sharpe = record.sharpe if record.sharpe is not None else "—"
        drawdown = (
            f"{record.max_drawdown_pct}%"
            if record.max_drawdown_pct is not None
            else "—"
        )
        total_trades = record.total_trades if record.total_trades is not None else "—"
        failure = record.failure_taxonomy if record.failure_taxonomy is not None else "—"

        lines.append("")
        lines.append(f"Generation {i} [{short_id}]:")
        lines.append(f"  Status: {status}")
        lines.append(
            f"  Score: {score}  Sharpe: {sharpe}  Drawdown: {drawdown}  Trades: {total_trades}"
        )
        lines.append(f"  Failure: {failure}")

    return "\n".join(lines)
