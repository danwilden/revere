# Design Review Summary: Phase 7 Test Plan

**Date:** 2026-03-16
**Prepared by:** Test & Verification Engineer (Claude)
**Status:** DESIGN PHASE COMPLETE — Ready for Implementation

---

## Executive Summary

**47 deterministic tests** designed to validate five interconnected features:

1. **Calendar Features** (day_of_week, is_friday, hour_of_day)
2. **Trade Holding Time Tracking** (entry_bar_idx in StrategyState)
3. **Engine Lifecycle Markers** (bars_in_trade, minutes_in_trade injection)
4. **Strategy Primitives** (max_holding_bars, exit_on_friday)
5. **Validation & DSL Support** (KNOWN_STATE_FIELDS, new validators)

**Test count breakdown:**
- 27 unit tests (fast, no engine)
- 20 integration tests (engine involved)
- **Zero production code yet** — design only

**Estimated implementation time:** 15 hours total

---

## Key Design Decisions

### Decision 1: bars_in_trade = 1 on Entry Bar (Not 0)

**Rationale:** Simpler semantics. "How many bars has this trade been open?" = "1 on entry bar, 2 on next bar, etc."

**Implication:** max_holding_bars=5 means hold 5 bars total (entry through 5 bars later)

**Test coverage:** Critical test 3.2 validates this explicitly (bars_in_trade must be 1, not 0)

**Risk if wrong:** Off-by-one errors propagate to all holding-period logic and max_holding_bars enforcement

---

### Decision 2: entry_bar_idx = -1 When Flat (Never None or 0)

**Rationale:** Sentinel value prevents accidental leakage into calculations. -1 is unambiguous.

**Implication:** bars_in_trade = 0 when flat (because bar_idx - (-1) would give wrong result)

**Test coverage:** Test 2.1 validates initialization; Tests 3.1-3.3 depend on this

**Risk if wrong:** TypeError or incorrect bars_in_trade calculation in positioned trades

---

### Decision 3: Lifecycle Markers Injected BEFORE on_bar()

**Rationale:** Strategy rules can directly reference bars_in_trade and minutes_in_trade without additional computation.

**Implication:** bars_in_trade, minutes_in_trade, day_of_week, hour_of_day all available in rule context

**Test coverage:** Test 3.8 validates markers are in bar dict before on_bar() call

**Risk if wrong:** Rules fail with "field not found" error when evaluating conditions

---

### Decision 4: Primitives Check Before Rule Evaluation

**Rationale:** max_holding_bars and exit_on_friday are force-exits, not negotiable by strategy rules.

**Implementation:** In RulesStrategy.should_exit(), check primitives first, then evaluate exit rule

**Implication:** If both max_holding_bars and exit_on_friday fire on same bar, one exit_reason is recorded

**Test coverage:** Tests 4.1-4.7 validate individual primitives and precedence scenarios

**Risk if wrong:** Primitives silently ignored; max_holding_bars never closes trades

---

### Decision 5: Calendar Features Are Timestamp-Derived (No NaN)

**Rationale:** Unlike indicators (RSI, ATR), calendar features don't need lookback window.

**Implication:** All bars have valid day_of_week, is_friday, hour_of_day (no NaN ramp-up)

**Test coverage:** Test 1.5 validates no NaN; Test 1.2 validates specific dates

**Risk if wrong:** NaN in rules context breaks evaluations silently or crashes with TypeError

---

## Architecture Diagram (Text)

```
BacktestEngine.run_backtest():
├─ for each bar:
│  ├─ [1] Stop/target check → close if hit
│  ├─ [2] Calculate lifecycle markers:
│  │  ├─ bars_in_trade = bar_idx - state.entry_bar_idx if positioned else 0
│  │  └─ minutes_in_trade = (ts - entry_ts).seconds/60 if positioned else 0.0
│  ├─ [3] Inject into bar: bar["bars_in_trade"], bar["minutes_in_trade"]
│  ├─ [4] Call strategy.on_bar(bar, features, state, ...)
│  │  └─ RulesStrategy.on_bar() calls:
│  │     └─ should_exit(bar, features, position, state):
│  │        ├─ Check: max_holding_bars triggered? → return True (reason="max_holding_bars")
│  │        ├─ Check: exit_on_friday && day_of_week==4? → return True (reason="exit_on_friday")
│  │        └─ Evaluate: exit rule node in rule context
│  ├─ [5] Execute action: entry, exit, or hold
│  └─ [6] Update state.entry_bar_idx on entry, -1 on exit
```

---

## Critical Tests (Must Pass)

These 10 tests catch the most likely bugs. **Run these first:**

| # | Test | File | Category | Why Critical |
|---|------|------|----------|-------------|
| 1 | bars_in_trade_on_entry_bar | test_backtest.py | 3.2 | Off-by-one error in holding time |
| 2 | max_holding_bars_force_exit | test_backtest.py | 4.1 | Feature doesn't work if not checked |
| 3 | entry_bar_idx_init | test_strategy.py | 2.1 | Prevents -1 leakage in calculations |
| 4 | calendar_features_no_nan | test_feature_compute.py | 1.5 | NaN breaks rules silently |
| 5 | exit_on_friday_force_close | test_backtest.py | 4.4 | Weekend risk management |
| 6 | lifecycle_markers_in_context | test_backtest.py | 3.8 | Rules can't reference undefined fields |
| 7 | validation_bars_in_trade_no_error | test_validation.py | 6.1 | Valid strategies rejected |
| 8 | feature_code_version_v1_1 | test_feature_compute.py | 1.6 | Stale features used if not bumped |
| 9 | day_of_week_values_correct | test_feature_compute.py | 1.2 | Wrong day triggers exit_on_friday |
| 10 | minutes_in_trade_elapsed_time | test_backtest.py | 3.6 | Holding-time rules misfire |

---

## Fixture Strategy (Reusable)

### Bar Generators (Deterministic, No RNG)

**Calendar-aware bars:**
```python
_flat_bars_with_calendar(n=10, start_day_of_week=0)
# Returns: Monday, Tuesday, ... spanning known week days
# Used by: exit_on_friday tests, day_of_week validation
```

**Precise timestamps:**
```python
_bars_with_precise_timestamps(hours=[10.0, 10.5, 11.0, 11.25])
# Returns: Bars at exact minute intervals (10:00, 10:30, 11:00, 11:15)
# Used by: minutes_in_trade elapsed time tests
```

**Fabricated features:**
```python
_bars_with_rsi_values(rsi_sequence=[70, 28, 35, 42, ...])
# Returns: Bars with RSI column pre-computed
# Used by: Realistic scenario tests (E2E)
```

### Strategy Generators (Deterministic)

**Deterministic entry/exit:**
```python
_enter_on_bar_n_strategy(n=1, exit_bar=3)
# BaseStrategy: enters bar 1, exits bar 3
# Used by: bars_in_trade increment tests
```

**Primitives strategies:**
```python
_max_holding_bars_strategy(max_bars=5)
_exit_on_friday_strategy(exit_on_friday=True)
# RulesStrategy with primitives fields
# Used by: max_holding_bars, exit_on_friday tests
```

---

## Edge Cases & Validation Rules

### Validation Strict Rules

**max_holding_bars:**
- Type: int (not float, not string)
- Range: 1–1000 (positive, non-zero)
- Fails: max_holding_bars=0, max_holding_bars=-5, max_holding_bars=3.5

**exit_on_friday:**
- Type: bool (true or false, not "yes", not 1)
- Fails: exit_on_friday="yes", exit_on_friday=1, exit_on_friday=null

**State fields in rules (must not flag as errors):**
- bars_in_trade, minutes_in_trade, day_of_week, hour_of_day
- All must be in KNOWN_STATE_FIELDS constant
- Validation must NOT raise "unresolved field" error

### Off-by-One Risks (Explicitly Tested)

1. **bars_in_trade = 1 at entry, not 0** → Test 3.2
2. **day_of_week = 4 for Friday, not 5** → Test 1.2
3. **max_holding_bars=5 means 5 bars, not 6** → Test 4.1
4. **entry_bar_idx = -1 when flat, not 0 or None** → Test 2.1

---

## Files to Create / Modify

### Production Code (5 files, ~50 lines)
- `backend/features/compute.py` — +3 feature functions, +1 version bump
- `backend/strategies/state.py` — +1 field, 3 method mods
- `backend/backtest/engine.py` — +2 calculations, injected before on_bar()
- `backend/strategies/rules_strategy.py` — +primitives check in should_exit()
- `backend/strategies/validation.py` — +KNOWN_STATE_FIELDS, +2 validators

### Test Code (5+ test files, 47 tests)
- `test_feature_compute.py` — +6 tests
- `test_strategy.py` — +5 tests
- `test_backtest.py` — +16 tests
- `test_rules_engine.py` — +6 tests
- `test_validation.py` (or extend test_strategy.py) — +10 tests
- `test_backtest_integration.py` (or add to test_backtest.py) — +4 tests

### Documentation (These Plans)
- `TEST_PLAN_CALENDAR_FEATURES.md` — 28 KB, full specs for all 47 tests
- `TEST_PLAN_SUMMARY.md` — 8 KB, quick reference
- `CRITICAL_ASSERTIONS.md` — 8 KB, 10 highest-risk tests
- `IMPLEMENTATION_CHECKLIST.md` — 11 KB, task checklist

---

## Quality Assurance Strategy

### Phase 1: Unit Tests (Fastest)
1. Calendar features (test_feature_compute.py) — 6 tests
2. StrategyState (test_strategy.py) — 5 tests
3. Rules engine (test_rules_engine.py) — 6 tests
4. Validation (test_validation.py) — 10 tests

**Expected:** All pass in <15 min. No engine involved.

### Phase 2: Integration Tests (Slower)
1. Engine lifecycle markers (test_backtest.py) — 8 tests
2. Strategy primitives (test_backtest.py) — 8 tests
3. End-to-end scenarios (test_backtest.py) — 4 tests

**Expected:** All pass in <30 min. Requires backtester orchestration.

### Phase 3: Regression Testing
- Full suite: `.venv/bin/python -m pytest backend/tests/ -q`
- Expected: 500+ existing tests still pass (zero regressions)

### Phase 4: Critical Assertions
- Run 10 highest-risk tests in isolation
- All must pass (documented in CRITICAL_ASSERTIONS.md)

---

## Success Criteria (Before Merge)

✅ **All 47 tests pass** deterministically (no flaky tests)
✅ **No regressions** in existing 500+ tests
✅ **FEATURE_CODE_VERSION = "v1.1"** (version bumped)
✅ **Zero NaN** in calendar columns
✅ **bars_in_trade = 1 on entry bar** (off-by-one correct)
✅ **Lifecycle markers in bar dict** before on_bar()
✅ **Validation allows state fields** (bars_in_trade in rules)
✅ **max_holding_bars actually closes trades** (not just parsed)
✅ **exit_on_friday closes on Friday** (not random day)
✅ **minutes_in_trade calculates elapsed time** correctly
✅ **Documentation complete** (all 4 plan docs present)

---

## Estimated Timeline

| Phase | Task | Hours | Status |
|-------|------|-------|--------|
| Design | Test plan creation | 4 | ✓ DONE |
| Impl | Calendar features (Task 1) | 2 | Pending |
| Impl | entry_bar_idx (Task 2) | 1 | Pending |
| Tests | Unit tests (Cat 1,2,5,6) | 4 | Pending |
| Impl | Engine markers (Task 3) | 2 | Pending |
| Tests | Engine tests (Cat 3) | 3 | Pending |
| Impl | Primitives (Task 4) | 2 | Pending |
| Impl | Validation (Task 5) | 1 | Pending |
| Tests | Primitives tests (Cat 4) | 3 | Pending |
| Tests | E2E tests (Cat 7) | 2 | Pending |
| QA | Regression + sign-off | 1 | Pending |
| **Total** | | **15 hours** | |

---

## Dependencies & Constraints

**Implementation order:**
1. Tasks 1-2 first (calendar features + entry_bar_idx)
2. Then Category 1,2,5,6 unit tests
3. Then Task 3 (engine markers)
4. Then Category 3 tests
5. Then Tasks 4-5 (primitives + validation)
6. Then Categories 4,7 tests

**Constraints:**
- No breaking changes to existing StrategyState API (backward compat)
- No changes to engine signature (params dict only)
- No file I/O in tests (mocks only)
- All timestamps must be UTC (tzinfo=timezone.utc)

---

## Known Risks & Mitigation

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|-----------|
| Off-by-one in bars_in_trade | HIGH | CRITICAL | Test 3.2 explicitly validates |
| Primitives not checked in should_exit() | HIGH | CRITICAL | Test 4.1 validates trades close |
| NaN in calendar features | MEDIUM | HIGH | Test 1.5 validates no NaN |
| FEATURE_CODE_VERSION forgotten | MEDIUM | HIGH | Test 1.6 catches if omitted |
| Validation allows invalid primitives | MEDIUM | MEDIUM | Tests 6.3-6.9 validate strictly |
| Lifecycle markers not injected | LOW | HIGH | Test 3.8 validates in context |

**Mitigation strategy:** Run CRITICAL_ASSERTIONS.md tests first (10 tests, 5 min). If all pass, implementation is sound.

---

## Document Reference

| Document | Size | Purpose |
|----------|------|---------|
| TEST_PLAN_CALENDAR_FEATURES.md | 28 KB | Full specification for all 47 tests with edge cases |
| TEST_PLAN_SUMMARY.md | 8 KB | Quick reference table, fixture patterns, validation rules |
| CRITICAL_ASSERTIONS.md | 8 KB | 10 highest-risk tests, why they matter, impact if fail |
| IMPLEMENTATION_CHECKLIST.md | 11 KB | Step-by-step tasks, test file organization, quality gates |
| This document | 4 KB | Executive summary and architecture overview |

**Start here:** Read this document (5 min). Then read TEST_PLAN_SUMMARY.md (5 min). Then CRITICAL_ASSERTIONS.md (5 min). Then open TEST_PLAN_CALENDAR_FEATURES.md for detailed specs during implementation.

---

## Next Steps

1. **Review this design** (ask clarifying questions if any)
2. **Approve critical decisions** (bars_in_trade=1 on entry bar, entry_bar_idx=-1, etc.)
3. **Begin implementation** using IMPLEMENTATION_CHECKLIST.md
4. **Run unit tests first** (Category 1,2,5,6) for fast feedback
5. **Run critical assertions** (CRITICAL_ASSERTIONS.md) to catch high-risk bugs
6. **Run full test suite** for regression verification
7. **Sign off** when all 47 tests pass + no regressions

**Estimated review time:** 30 min (this doc + summary + critical assertions)

---

**Test Plan Status:** ✅ DESIGN COMPLETE — Ready for Development Approval

