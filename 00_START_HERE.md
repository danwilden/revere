# Phase 7 Test Plan — START HERE

**Date:** 2026-03-16
**Status:** Design phase complete. Ready for development.
**Scope:** 47 deterministic tests for calendar features, holding time tracking, and strategy primitives.

---

## What You're Looking At

This folder contains the **complete test design** for Phase 7 of the Medallion trading platform. No code has been written yet — only the test plan and specifications.

**5 related files describe this work:**

1. **00_START_HERE.md** ← You are here
2. **DESIGN_REVIEW_SUMMARY.md** — Executive overview (10 min read)
3. **TEST_PLAN_SUMMARY.md** — Quick reference (10 min read)
4. **CRITICAL_ASSERTIONS.md** — Top 10 highest-risk tests (5 min read)
5. **IMPLEMENTATION_CHECKLIST.md** — Step-by-step tasks (use during coding)
6. **TEST_PLAN_CALENDAR_FEATURES.md** — Full specs for all 47 tests (reference)

---

## The 47-Second Summary

**Adding 5 interconnected features to the backtester:**

1. **Calendar features** — day_of_week, is_friday, hour_of_day columns
2. **Holding time tracking** — entry_bar_idx in StrategyState
3. **Lifecycle markers** — bars_in_trade, minutes_in_trade injected before on_bar()
4. **Strategy primitives** — max_holding_bars and exit_on_friday force-exits
5. **Validation** — KNOWN_STATE_FIELDS so rules can reference these fields

**47 tests validate** these features don't have data leakage, off-by-one bugs, or silent failures.

**Estimated effort:** 15 hours (8h implementation + 5h testing + 2h QA)

---

## Quick Navigation

### For Decision Makers
1. Read: **DESIGN_REVIEW_SUMMARY.md** (10 min)
2. Review: Top 10 critical tests in **CRITICAL_ASSERTIONS.md**
3. Approve: Key design decisions (bars_in_trade=1 on entry bar, etc.)

### For Developers
1. Read: **TEST_PLAN_SUMMARY.md** (10 min) — understand fixtures, validation rules
2. Read: **IMPLEMENTATION_CHECKLIST.md** (20 min) — step-by-step tasks
3. Refer: **TEST_PLAN_CALENDAR_FEATURES.md** (detailed specs during coding)

### For QA/Test Engineers
1. Read: **CRITICAL_ASSERTIONS.md** (5 min) — highest-risk tests
2. Understand: Edge cases and off-by-one risks
3. Use: **IMPLEMENTATION_CHECKLIST.md** quality gates section

---

## The Features (Plain English)

### Feature 1: Calendar Features
**What:** Add day_of_week (0=Mon, 4=Fri), is_friday (0/1), hour_of_day (0-23) to features
**Where:** backend/features/compute.py
**Why:** Rules need to filter by day/hour (e.g., "don't trade Friday afternoons")
**Risk:** NaN values in early bars, timezone confusion
**Tests:** 6 tests validate values, version bump, no NaN

### Feature 2: Entry Bar Index
**What:** Track which bar a trade was entered on (for holding time measurement)
**Where:** backend/strategies/state.py
**How:** Add entry_bar_idx field (-1 when flat)
**Why:** Foundation for bars_in_trade calculation in engine
**Risk:** Off-by-one errors in holding period
**Tests:** 5 tests validate init, set, clear, reset

### Feature 3: Lifecycle Markers
**What:** Inject bars_in_trade and minutes_in_trade into each bar before strategy sees it
**Where:** backend/backtest/engine.py
**Why:** Strategy rules need to reference "how long has this trade been open?"
**Risk:** Markers not in context, wrong calculation, injected after on_bar()
**Tests:** 8 tests validate injection timing, incrementing, accuracy

### Feature 4: Holding Limits & Friday Exit
**What:** Add max_holding_bars (int) and exit_on_friday (bool) to RulesStrategy
**Where:** backend/strategies/rules_strategy.py
**Why:** Force-exit rules that can't be overridden (risk management)
**Risk:** Primitives parsed but not checked, precedence unclear
**Tests:** 8 tests validate both trigger, precedence vs. rules, both together

### Feature 5: Validation & DSL
**What:** Teach validator that bars_in_trade/minutes_in_trade/day_of_week/hour_of_day are valid fields
**Where:** backend/strategies/validation.py
**Why:** Rules using these fields shouldn't fail validation
**Risk:** Valid strategies rejected, invalid primitives not caught
**Tests:** 10 tests validate happy path and error cases

---

## The Test Plan Structure

```
47 Tests
├─ 27 Unit Tests (no engine) [Fast, run first]
│  ├─ Category 1: Calendar Features [6 tests]
│  ├─ Category 2: StrategyState [5 tests]
│  ├─ Category 5: Rules Engine [6 tests]
│  └─ Category 6: Validation [10 tests]
│
└─ 20 Integration Tests (with engine) [Slower]
   ├─ Category 3: Lifecycle Markers [8 tests]
   ├─ Category 4: Primitives [8 tests]
   └─ Category 7: End-to-End Scenarios [4 tests]
```

**No code in any of these tests** — pure design and specification.

---

## Critical Edge Cases (Read This!)

**These 10 bugs are most likely to slip through:**

1. **bars_in_trade = 0 on entry bar** (should be 1)
   → Symptom: Trades hold N+1 bars instead of N
   → Severity: CRITICAL — corrupts all max_holding_bars logic

2. **max_holding_bars never triggers exit**
   → Symptom: Trades hold indefinitely; no force-close
   → Severity: CRITICAL — feature doesn't work

3. **entry_bar_idx not cleared to -1**
   → Symptom: bars_in_trade calculation wrong after exit
   → Severity: CRITICAL — all engine tests fail

4. **NaN in calendar features**
   → Symptom: Rules silently fail on first N bars
   → Severity: HIGH — silent failure

5. **exit_on_friday closes wrong day**
   → Symptom: Friday position held to Monday; regulatory risk
   → Severity: HIGH — weekend gap exposure

6. **Lifecycle markers not injected before on_bar()**
   → Symptom: ValueError: field not found in rules
   → Severity: HIGH — feature unusable

7. **Validation allows state fields but shouldn't**
   → Symptom: Valid strategies fail validation
   → Severity: MEDIUM — UX broken

8. **FEATURE_CODE_VERSION not bumped to v1.1**
   → Symptom: Old feature runs (without calendar cols) used in new backtests
   → Severity: HIGH — wrong backtest results

9. **day_of_week = 5 for Friday** (should be 4)
   → Symptom: exit_on_friday rule never fires
   → Severity: HIGH — feature broken

10. **minutes_in_trade calculated as bar_count instead of elapsed time**
    → Symptom: Holding-time rules misfire
    → Severity: MEDIUM — wrong exit logic

**See CRITICAL_ASSERTIONS.md for tests that catch all 10.**

---

## Key Design Decisions

### Decision 1: bars_in_trade = 1 on Entry Bar
**Why?** Intuitive semantics. "How many bars has this trade been open?" = "1 on entry bar"
**Impact:** max_holding_bars=5 means hold 5 bars (entry through 5 bars later)
**Validated by:** Test `test_engine_bars_in_trade_on_entry_bar`

### Decision 2: entry_bar_idx = -1 When Flat
**Why?** Sentinel value prevents accidental leakage (if -1 somehow got into calculations)
**Impact:** Clean signal: positioned (>= 0) vs. flat (-1)
**Validated by:** Test `test_strategy_state_entry_bar_idx_init`

### Decision 3: Lifecycle Markers Injected BEFORE on_bar()
**Why?** Strategy rules can reference bars_in_trade directly (no extra computation needed)
**Impact:** bars_in_trade, minutes_in_trade available in all rule contexts
**Validated by:** Test `test_engine_lifecycle_markers_in_context`

### Decision 4: Primitives Check Before Rule Evaluation
**Why?** max_holding_bars and exit_on_friday are hard stops (risk management)
**Impact:** Rules can't override primitives (feature intent: force exit after N bars)
**Validated by:** Tests `test_engine_max_holding_bars_force_exit`, `test_engine_exit_on_friday_force_close`

### Decision 5: Calendar Features Are Timestamp-Derived (No NaN)
**Why?** No lookback window needed (unlike RSI, ATR)
**Impact:** All bars have valid day_of_week, is_friday, hour_of_day (no ramp-up)
**Validated by:** Test `test_calendar_features_no_nan`

---

## Files to Modify (Implementation Reference)

**Production code (5 files, ~50 lines):**
- `backend/features/compute.py` — +3 feature functions, +1 version bump
- `backend/strategies/state.py` — +1 field, 3 method modifications
- `backend/backtest/engine.py` — +2 calculations, injected before on_bar()
- `backend/strategies/rules_strategy.py` — +primitives check in should_exit()
- `backend/strategies/validation.py` — +KNOWN_STATE_FIELDS, +2 validators

**Test files (5+ files, ~800 lines):**
- `backend/tests/test_feature_compute.py` — +6 tests
- `backend/tests/test_strategy.py` — +5 tests
- `backend/tests/test_backtest.py` — +16 tests
- `backend/tests/test_rules_engine.py` — +6 tests
- `backend/tests/test_validation.py` or extend test_strategy.py — +10 tests
- `backend/tests/test_backtest_integration.py` — +4 tests

---

## Next Steps (In Order)

### Step 1: Review (30 min)
1. Read **DESIGN_REVIEW_SUMMARY.md** (executive overview)
2. Review **CRITICAL_ASSERTIONS.md** (top 10 tests)
3. Skim **TEST_PLAN_SUMMARY.md** (fixture patterns)

### Step 2: Approve (5 min)
- Confirm key design decisions (bars_in_trade=1 on entry, entry_bar_idx=-1, etc.)
- Confirm scope (47 tests across 5 features)
- Confirm timeline (15 hours total)

### Step 3: Implement (8 hours)
- Use **IMPLEMENTATION_CHECKLIST.md** as your task list
- Implement Tasks 1-5 in order (features before tests)
- Implement unit tests (Categories 1,2,5,6) for fast feedback

### Step 4: Test (5 hours)
- Implement integration tests (Categories 3,4,7)
- Run full test suite
- Verify zero regressions

### Step 5: Sign Off (2 hours)
- Run quality gates (IMPLEMENTATION_CHECKLIST.md section)
- Run critical assertions (CRITICAL_ASSERTIONS.md)
- Verify 500+ existing tests still pass
- Merge when all gates pass

---

## Success Criteria

Before merging, verify:

- [ ] All 47 tests pass deterministically
- [ ] Zero regressions in existing 500+ tests
- [ ] FEATURE_CODE_VERSION = "v1.1"
- [ ] No NaN in calendar features
- [ ] bars_in_trade = 1 on entry bar (validated by test 3.2)
- [ ] max_holding_bars closes trades (validated by test 4.1)
- [ ] exit_on_friday closes on Friday (validated by test 4.4)
- [ ] Lifecycle markers in bar dict before on_bar() (validated by test 3.8)
- [ ] Validation allows state fields (validated by test 6.1)
- [ ] minutes_in_trade calculated correctly (validated by test 3.6)

---

## Where to Find Things

**This folder contains:**
```
00_START_HERE.md (you are here)
DESIGN_REVIEW_SUMMARY.md (executive summary, architecture, timeline)
TEST_PLAN_SUMMARY.md (quick reference, fixtures, validation rules)
TEST_PLAN_CALENDAR_FEATURES.md (full specification for all 47 tests)
CRITICAL_ASSERTIONS.md (10 highest-risk tests and their importance)
IMPLEMENTATION_CHECKLIST.md (step-by-step tasks and quality gates)
```

**Memory system (persistent across sessions):**
```
.claude/agent-memory/test-verification-engineer/
├─ MEMORY.md (index of all memories)
├─ phase6_chat_tests.md (previous Phase 6 chat system tests)
└─ phase7_calendar_holding_time_plan.md (this work summarized)
```

---

## Questions?

If anything is unclear:

1. **On test design:** See **TEST_PLAN_CALENDAR_FEATURES.md** (detailed specs)
2. **On implementation:** See **IMPLEMENTATION_CHECKLIST.md** (step-by-step)
3. **On edge cases:** See **CRITICAL_ASSERTIONS.md** (risks explained)
4. **On overall strategy:** See **DESIGN_REVIEW_SUMMARY.md** (architecture)

---

## The Big Picture

**Why this work matters:**

- **Calendar features** enable day/hour-based filtering (no Friday trades, etc.)
- **Holding time tracking** enables position management (force exit after N bars)
- **Lifecycle markers** enable sophisticated rules (exit when bars_in_trade >= 5)
- **Strategy primitives** enforce risk limits (can't be overridden by rules)
- **Validation** prevents silent failures (rules can reference these fields safely)

**Result:** Backtester becomes more expressive and risk-aware.

---

**Status:** Design complete. Ready for development.  
**Next step:** Begin implementation using IMPLEMENTATION_CHECKLIST.md

