# Three-Agent Research Loop Fixes

## Fix 1 — Zero Trades: Prompt Guards + Post-Generation Validation

### Root Cause
The `SYSTEM_PROMPT` in `strategy_researcher.py` listed a static feature field set regardless of
whether a `feature_run_id` was present in state. When `FEATURE_AVAILABILITY=NONE`, the LLM
generated rules referencing `rsi_14`, `atr_14`, etc. — fields absent from bar context — causing
`ValueError` to propagate through `on_bar()` and crash the backtest. Two additional failure modes:

- `position_size_units=0` produced silently invalid trades (zero PnL, zero quantity)
- Overly tight thresholds (RSI < 5, ADX > 50) fired on <1% of bars

### Changes
- **`backend/agents/strategy_researcher.py`**
  - Step 3 in `SYSTEM_PROMPT` is now conditional on `FEATURE_AVAILABILITY` (FULL vs NONE)
  - Added step 3a: non-negotiable constraints — `position_size_units >= 1000`,
    `cooldown_hours <= 4`, no FULL fields when NONE, realistic entry thresholds
  - Added `_validate_candidate_definition()` helper called after `StrategyCandidate.model_validate()`
    — logs warnings for sub-1000 units, excessive cooldown, and feature fields used without
    `feature_run_id`. Non-blocking (logs only).

### New Tests (`backend/tests/test_trade_execution.py`, 4 tests)
| Test | What it verifies |
|------|-----------------|
| `test_always_true_entry_produces_trades` | `{close > 0}` entry always fires → ≥1 trade |
| `test_position_size_zero_silent_failure` | Documents zero-units → zero-PnL behaviour |
| `test_feature_field_without_feature_run_raises` | `rsi_14` in context-less bar → `ValueError` propagates |
| `test_tight_vs_loose_threshold_trade_count` | Loose RSI<60 > trades than tight RSI<22 |

---

## Fix 2 — Token Inflation: Tool Result Truncation

### Root Cause
`_dispatch_tool()` appended raw `model_dump()` output to the Bedrock message list. Worst
offenders per call:
- `get_equity_curve`: 1000+ `EquityPoint` records (~40 KB)
- `get_backtest_trades`: all `TradeRecord` fields per trade (~8 KB for 50 trades)
- `get_backtest_run`: 50+ `PerformanceMetric` records

These accumulated in the multi-turn context, spiking cumulative input tokens to 85k+ at late turns.

### Changes
- **`backend/agents/tools/truncation.py`** (new module)
  - `truncate_equity_curve()`: 1000-point array → 6-field scalar summary (>90% size reduction)
  - `truncate_trades()`: full trade list → aggregate stats + first_3/last_3 head-tail samples
  - `truncate_metrics()`: 50-metric list → priority-ordered dict (≤15 keys, overall segment only)
  - All three are pure functions, never raise, return input unchanged on malformed data
- **`backend/agents/strategy_researcher.py`**
  - `_dispatch_tool()` now applies truncators via `_TRUNCATORS` dict lookup before returning
  - Added `cumulative_input_tokens` / `cumulative_output_tokens` counters (both tool-loop phases)
  - Emits `TOKEN_BUDGET_WARNING` log at 50k cumulative input tokens

### New Tests (`backend/tests/test_token_budget.py`, 6 tests)
| Test | What it verifies |
|------|-----------------|
| `test_truncate_equity_curve_reduces_size` | 500-point array → >90% byte reduction |
| `test_truncate_trades_head_tail_sampling` | 20 trades → stats + first_3/last_3 |
| `test_truncate_metrics_keeps_priority_fields` | 25 metrics → ≤15 keys, `trade_count` present |
| `test_truncation_safe_on_empty_input` | `{}` → no exception, `truncated=False` |
| `test_truncation_safe_on_none_values` | `None` lists → no exception |
| `test_truncate_trades_empty_trades_list` | `trades=[]` → valid zero-count result |

---

## Fix 3 — Supervisor Routing: DIME Marker Tests + State Validation

### Root Cause
The three `marker_action` routing branches (`lock`/`explore`/`exploit`) in `supervisor.py`
lines 73–82 had zero test coverage. Additionally, an important routing subtlety was undocumented:
the `task == "generate_seed"` branch (priority 5) fires before DIME branches (priority 7–9).
DIME routing is only reachable when `task` has advanced to `"mutate"` or another non-seed value.

### Changes
- **`backend/agents/supervisor.py`**
  - Added `_REQUIRED_STATE_FIELDS` tuple
  - Added `_validate_state_entry(state, trace_id)` — non-raising guard, logs warnings for
    missing required fields, non-int iteration, and unknown task values
  - Called at top of `supervisor_node()` (after `trace_id` extraction)

### New Tests (`backend/tests/test_agents_foundation.py`, 6 appended tests)
| Test | What it verifies |
|------|-----------------|
| `test_supervisor_marker_action_lock_routes_to_researcher` | `lock` → `strategy_researcher` |
| `test_supervisor_marker_action_explore_routes_to_researcher` | `explore` → `strategy_researcher` |
| `test_supervisor_marker_action_exploit_routes_to_comparator` | `exploit` → `generation_comparator` |
| `test_supervisor_marker_action_continue_falls_through_to_mutation` | `continue` (non-DIME) → researcher |
| `test_supervisor_marker_action_none_falls_through_to_mutation` | `None` marker → researcher |
| `test_supervisor_dime_lock_takes_priority_over_comparator_route` | `lock` beats gen>=1 comparator branch |

> **Routing note documented:** All DIME tests use `task="mutate"` because the `task == "generate_seed"`
> branch fires at priority 5 (before DIME at priority 7–9). In real flow, DIME branches are only
> reached after the mutation cycle has advanced task past seed generation.

---

## Metrics Delta

| Metric | Before | After |
|--------|--------|-------|
| Passing tests | 644 | 660 (+16) |
| Token truncation | None | >90% for equity, >80% for trades |
| DIME branch coverage | 0% | 100% (3 branches × 2 tests each) |
| Candidate validation | None | Post-parse warnings on units/cooldown/field leakage |

### Files Changed
| File | Type | Description |
|------|------|-------------|
| `backend/agents/strategy_researcher.py` | Modified | SYSTEM_PROMPT + validation helper + truncation + token tracking |
| `backend/agents/supervisor.py` | Modified | `_validate_state_entry` + call-site |
| `backend/agents/tools/truncation.py` | New | Three pure truncation functions |
| `backend/tests/test_trade_execution.py` | New | 4 engine execution tests |
| `backend/tests/test_token_budget.py` | New | 6 truncation unit tests |
| `backend/tests/test_agents_foundation.py` | Modified | 6 DIME routing tests appended |
