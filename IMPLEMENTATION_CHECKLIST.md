# Implementation & Testing Checklist

**Phase:** 7 — Calendar Features, Holding Time Tracking, Strategy Primitives
**Total tasks:** 5 implementation + 47 tests + quality gates

---

## Implementation Tasks (Before Tests)

### Task 1: Add Calendar Features to compute.py
**File:** `backend/features/compute.py`

- [ ] Import `datetime` utilities if not present
- [ ] Add three new feature functions:
  - `day_of_week(timestamp_series: pd.Series) -> pd.Series` — 0=Mon, 4=Fri, 6=Sun
  - `is_friday(timestamp_series: pd.Series) -> pd.Series` — binary 0/1
  - `hour_of_day(timestamp_series: pd.Series) -> pd.Series` — 0-23
- [ ] Add to `compute_features(df)` return columns list
- [ ] Bump `FEATURE_CODE_VERSION = "v1.1"`
- [ ] Verify: No NaN values (test 1.5)
- [ ] Verify: Values match known dates (test 1.2)

### Task 2: Extend StrategyState with entry_bar_idx
**File:** `backend/strategies/state.py`

- [ ] Add field: `entry_bar_idx: int = -1`
- [ ] Modify `open_trade()` signature to accept `bar_idx: int` parameter
  - Or create separate method: `set_entry_bar_idx(bar_idx: int)`
  - Choose one approach, document it
- [ ] Modify `close_trade()` to reset entry_bar_idx to -1
- [ ] Modify `reset()` to reset entry_bar_idx to -1
- [ ] Verify: Initial value is -1, not None or 0 (test 2.1)
- [ ] Verify: Updated correctly across trade cycles (test 2.5)

### Task 3: Inject Lifecycle Markers in Engine
**File:** `backend/backtest/engine.py`

- [ ] In `run_backtest()`, before calling `strategy.on_bar()`:
  - Calculate: `bars_in_trade = 0 if state.is_flat else (bar_idx - state.entry_bar_idx)`
  - Calculate: `minutes_in_trade = 0.0 if state.is_flat else (ts - state.entry_time).total_seconds() / 60.0`
  - Inject into bar dict: `bar["bars_in_trade"] = bars_in_trade`
  - Inject into bar dict: `bar["minutes_in_trade"] = minutes_in_trade`
- [ ] Ensure injection happens **before** `strategy.on_bar()` call (line order matters)
- [ ] Verify: Captured in mock strategy tests (test 3.8)
- [ ] Verify: Increments correctly (test 3.3)

### Task 4: Add Primitives to RulesStrategy
**File:** `backend/strategies/rules_strategy.py`

- [ ] Add to `__init__()`: read max_holding_bars and exit_on_friday from definition
- [ ] Modify `should_exit()` to check primitives before evaluating rule:
  ```python
  def should_exit(self, bar, features, position, state):
      # 1. Check max_holding_bars
      max_bars = self._def.get("max_holding_bars")
      if max_bars and bar.get("bars_in_trade", 0) >= max_bars:
          # Set exit_reason before returning True
          return True  # or set action reason?

      # 2. Check exit_on_friday
      if self._def.get("exit_on_friday") and bar.get("day_of_week") == 4:
          return True

      # 3. Evaluate normal exit rule
      exit_node = self._def.get("exit")
      if exit_node is None:
          return False
      ctx = self._build_context(bar, features)
      return evaluate(exit_node, ctx, self._named)
  ```
- [ ] Ensure exit_reason is properly set (design: "max_holding_bars", "exit_on_friday", or "strategy_signal")
- [ ] Verify: Closes after N bars (test 4.1)
- [ ] Verify: Closes on Friday (test 4.4)

### Task 5: Update Validation
**File:** `backend/strategies/validation.py`

- [ ] Add constant: `KNOWN_STATE_FIELDS = {"bars_in_trade", "minutes_in_trade", "day_of_week", "hour_of_day"}`
- [ ] Update `validate_rules_strategy()` to:
  - Add validator for max_holding_bars:
    - If present, must be int
    - If int, must be > 0
    - Error: "max_holding_bars must be a positive integer, got {value}"
  - Add validator for exit_on_friday:
    - If present, must be bool
    - Error: "exit_on_friday must be a boolean (true/false), got {type}"
  - Update field validation to check KNOWN_STATE_FIELDS (don't flag as unresolved)
- [ ] Verify: Valid max_holding_bars passes (test 6.3)
- [ ] Verify: Invalid max_holding_bars fails (tests 6.4, 6.5, 6.6)
- [ ] Verify: Valid exit_on_friday passes (tests 6.7, 6.8)
- [ ] Verify: Invalid exit_on_friday fails (test 6.9)

---

## Test Implementation Tasks (47 Tests)

### Phase 1: Unit Tests (Fastest, Run First)

#### Category 1: Calendar Features (test_feature_compute.py, 6 tests)
- [ ] `test_calendar_features_day_of_week_present` — verify column exists
- [ ] `test_day_of_week_values_correct` — 2024-01-05=4 (Friday), 2024-01-01=0 (Monday)
- [ ] `test_is_friday_column_values` — binary 0/1 for Fri/non-Fri
- [ ] `test_hour_of_day_column_values` — hour_of_day matches timestamp.hour
- [ ] `test_calendar_features_no_nan` — no NaN in calendar columns
- [ ] `test_feature_code_version_v1_1` — version == "v1.1"

#### Category 2: StrategyState (test_strategy.py, 5 tests)
- [ ] `test_strategy_state_entry_bar_idx_init` — entry_bar_idx == -1
- [ ] `test_strategy_state_entry_bar_idx_on_open_trade` — set correctly
- [ ] `test_strategy_state_entry_bar_idx_on_close_trade` — cleared to -1
- [ ] `test_strategy_state_entry_bar_idx_on_reset` — cleared to -1
- [ ] `test_strategy_state_entry_bar_idx_multiple_trades` — cycles correctly

#### Category 5: Rules Engine (test_rules_engine.py, 6 tests)
- [ ] `test_rules_engine_bars_in_trade_gte` — leaf comparison works
- [ ] `test_rules_engine_bars_in_trade_in_composite` — works in all/any
- [ ] `test_rules_engine_day_of_week_eq` — equality check
- [ ] `test_rules_engine_day_of_week_neq` — inequality check
- [ ] `test_rules_engine_hour_of_day_in_session` — "in" operator
- [ ] `test_rules_engine_minutes_in_trade_lt` — float comparison

#### Category 6: Validation (test_strategy.py or test_validation.py, 10 tests)
- [ ] `test_validation_bars_in_trade_no_error` — allowed in rules
- [ ] `test_validation_minutes_in_trade_no_error` — allowed in rules
- [ ] `test_validation_max_holding_bars_valid` — 5 passes
- [ ] `test_validation_max_holding_bars_negative_fails` — -1 fails
- [ ] `test_validation_max_holding_bars_zero_fails` — 0 fails
- [ ] `test_validation_max_holding_bars_non_integer_fails` — 3.5 fails
- [ ] `test_validation_exit_on_friday_true_valid` — true passes
- [ ] `test_validation_exit_on_friday_false_valid` — false passes
- [ ] `test_validation_exit_on_friday_non_boolean_fails` — "yes" fails
- [ ] `test_validation_known_state_fields_recognized` — no unresolved errors

**Subtotal: 27 unit tests — should take ~30 minutes to implement**

### Phase 2: Integration Tests (Backtest Engine)

#### Category 3: Engine Lifecycle Markers (test_backtest.py, 8 tests)
- [ ] `test_engine_bars_in_trade_before_entry` — 0 before entry
- [ ] `test_engine_bars_in_trade_on_entry_bar` — 1 on entry bar (CRITICAL)
- [ ] `test_engine_bars_in_trade_increments` — 1,2,3,4... sequence
- [ ] `test_engine_bars_in_trade_after_exit` — resets to 0
- [ ] `test_engine_minutes_in_trade_before_entry` — 0.0 before entry
- [ ] `test_engine_minutes_in_trade_elapsed_time` — correct elapsed seconds/60
- [ ] `test_engine_minutes_in_trade_after_exit` — resets to 0.0
- [ ] `test_engine_lifecycle_markers_in_context` — in bar dict before on_bar()

#### Category 4: Strategy Primitives (test_backtest.py, 8 tests)
- [ ] `test_engine_max_holding_bars_force_exit` — closes after N bars (CRITICAL)
- [ ] `test_engine_max_holding_bars_one` — max_bars=1 closes immediately
- [ ] `test_engine_max_holding_bars_vs_rule_exit` — rule wins if earlier
- [ ] `test_engine_exit_on_friday_force_close` — Friday closes (CRITICAL)
- [ ] `test_engine_exit_on_friday_disabled` — False doesn't force exit
- [ ] `test_engine_exit_on_friday_vs_rule_exit` — rule wins if earlier
- [ ] `test_engine_max_holding_bars_and_exit_on_friday` — both trigger, documents precedence
- [ ] `test_engine_lifecycle_primitives_ignored_flat` — no effect when flat

#### Category 7: End-to-End (test_backtest.py or test_backtest_integration.py, 4 tests)
- [ ] `test_e2e_max_holding_bars_realistic` — RSI entry + max_bars exit
- [ ] `test_e2e_exit_on_friday_realistic` — Mon-Fri entry/exit
- [ ] `test_e2e_both_primitives` — both fields, precedence verified
- [ ] `test_e2e_day_of_week_in_rule` — calendar feature in entry rule

**Subtotal: 20 integration tests — should take ~45 minutes to implement**

---

## Quality Gates (Before Merge)

### Test Execution
- [ ] All 47 tests pass locally: `.venv/bin/python -m pytest backend/tests/ -v`
- [ ] No new deprecation warnings
- [ ] No test collection errors

### Code Quality
- [ ] FEATURE_CODE_VERSION bumped to v1.1
- [ ] No syntax errors in modified files
- [ ] type hints present in new functions (Python 3.10+)
- [ ] Docstrings on new public methods

### Regression Testing
- [ ] Existing 500+ tests pass: `.venv/bin/python -m pytest backend/tests/ -q`
- [ ] No breaking changes to StrategyState API (backward compat for existing code)
- [ ] No breaking changes to engine signature

### Critical Assertions (High-Risk Bugs)
- [ ] bars_in_trade = 1 on entry bar (test 3.2) ✓
- [ ] max_holding_bars actually closes trades (test 4.1) ✓
- [ ] entry_bar_idx = -1 when flat (test 2.1) ✓
- [ ] Calendar features have no NaN (test 1.5) ✓
- [ ] exit_on_friday closes on Friday (test 4.4) ✓
- [ ] Lifecycle markers injected before on_bar() (test 3.8) ✓
- [ ] Validation allows state fields (test 6.1) ✓
- [ ] FEATURE_CODE_VERSION == v1.1 (test 1.6) ✓
- [ ] day_of_week = 4 for Friday (test 1.2) ✓
- [ ] minutes_in_trade correct elapsed time (test 3.6) ✓

### Documentation
- [ ] TEST_PLAN_CALENDAR_FEATURES.md present (full specs)
- [ ] TEST_PLAN_SUMMARY.md present (quick reference)
- [ ] CRITICAL_ASSERTIONS.md present (high-risk tests)
- [ ] Edge cases documented in test docstrings

---

## Implementation Order (Recommended)

1. **Day 1 (2h):** Implement Tasks 1-2 (calendar features + entry_bar_idx)
2. **Day 1 (2h):** Implement unit tests (Category 1, 2, 5, 6) — 27 tests
3. **Day 2 (2h):** Implement Task 3 (engine lifecycle markers)
4. **Day 2 (2h):** Implement Category 3 tests (engine lifecycle) — 8 tests
5. **Day 3 (2h):** Implement Tasks 4-5 (primitives + validation)
6. **Day 3 (2h):** Implement Categories 4 & 7 tests (primitives + E2E) — 12 tests
7. **Day 4 (1h):** Quality gates, regression testing, documentation

**Total:** ~15 hours development + testing

---

## File Modifications Summary

| File | Changes | Complexity |
|------|---------|-----------|
| `compute.py` | +3 feature functions, +1 version bump | Low |
| `state.py` | +1 field, 3 method modifications | Low |
| `engine.py` | +2 lifecycle marker calculations, injected before on_bar() | Medium |
| `rules_strategy.py` | +max_holding_bars & exit_on_friday checks in should_exit() | Medium |
| `validation.py` | +KNOWN_STATE_FIELDS, +2 validators | Low |

**Total production code: ~50 lines new/modified**

---

## Rollback Plan (If Critical Issue Found)

1. Revert commit (git revert)
2. FEATURE_CODE_VERSION stays v1.1 (don't downgrade — would break feature run lookups)
3. Mark "calendar features disabled" in deployment notes
4. Feature runs v1.0 still work (no calendar columns, but RSI/ADX etc. valid)

---

## Sign-Off Checklist

**Developer:**
- [ ] All 47 tests passing locally
- [ ] No warnings or deprecations
- [ ] Code review requested

**Test Engineer (You):**
- [ ] Ran full test suite: .venv/bin/python -m pytest backend/tests/ -v
- [ ] Verified 10 critical assertions (CRITICAL_ASSERTIONS.md)
- [ ] Checked for silent failures (data leakage, NaN, off-by-one)
- [ ] Regression test: existing 500+ tests still pass
- [ ] Documented edge cases in test docstrings

**Merge:**
- [ ] All quality gates passed
- [ ] No regressions detected
- [ ] Ready for dev deployment

