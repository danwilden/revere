# Test Plan Summary: Calendar Features, Holding Time Tracking, Strategy Primitives

**Date:** 2026-03-16
**Total Tests Planned:** 47 deterministic, fixture-driven tests
**Scope:** Calendar features (compute.py) + entry_bar_idx tracking (state.py) + lifecycle markers (engine.py) + max_holding_bars / exit_on_friday (rules_strategy.py)

---

## Quick Reference: Test Breakdown

| Category | Count | File(s) | What it validates |
|----------|-------|---------|-------------------|
| 1. Calendar Features | 6 | test_feature_compute.py | day_of_week, is_friday, hour_of_day columns; v1.1 version bump; no NaN |
| 2. StrategyState | 5 | test_strategy.py | entry_bar_idx init (-1), set on open_trade(), clear on close_trade() / reset() |
| 3. Engine Lifecycle Markers | 8 | test_backtest.py | bars_in_trade (0→1→2...), minutes_in_trade (float elapsed), injected before on_bar() |
| 4. Strategy Primitives | 8 | test_backtest.py | max_holding_bars force-exit, exit_on_friday force-exit, precedence vs. rules |
| 5. Rules DSL | 6 | test_rules_engine.py | bars_in_trade/minutes_in_trade/day_of_week/hour_of_day in leaf & composite rules |
| 6. Validation | 10 | test_strategy.py or test_validation.py | KNOWN_STATE_FIELDS, max_holding_bars must be positive int, exit_on_friday must be bool |
| 7. End-to-End | 4 | test_backtest.py / test_backtest_integration.py | Realistic scenarios: RSI entry + max_holding_bars, Friday exit, both primitives, calendar in rules |

---

## Critical Design Decisions & Edge Cases

### 1. bars_in_trade = 1 on Entry Bar (Not 0)

**Test:** `test_engine_bars_in_trade_on_entry_bar` (Category 3.2)

- Entry on bar 5 → bars_in_trade = 1 (counts current bar)
- max_holding_bars=5 means hold 5 bars total: entry bar + 4 more
- **Off-by-one risk:** Test explicitly validates bars_in_trade==1 at entry

### 2. entry_bar_idx = -1 When Flat (Never None)

**Tests:** Category 2 (all tests), plus Category 3.1 (bars_in_trade calculation)

- Flat state: entry_bar_idx = -1
- Positioned: entry_bar_idx = bar index of entry
- Why: Prevents -1 leaking into rules like `bars_in_trade = bar_idx - (-1) = bar_idx + 1`
- reset() must clear to -1 (not None)

### 3. day_of_week = 4 for Friday (ISO Convention)

**Test:** `test_day_of_week_values_correct` (Category 1.2)

- Monday = 0, Friday = 4, Sunday = 6 (Python datetime.weekday())
- Ensure consistent with compute.py implementation
- Test includes 2024-01-05 (known Friday)

### 4. minutes_in_trade is Float, Not Int

**Test:** `test_engine_minutes_in_trade_elapsed_time` (Category 3.6)

- (current_ts - entry_ts).total_seconds() / 60.0
- Use 0.01 tolerance for comparisons (30.0 should equal 30, not fail)

### 5. Calendar Features Have No NaN (Timestamp-Derived)

**Test:** `test_calendar_features_no_nan` (Category 1.5)

- Unlike rolling indicators (RSI, ATR), calendar features don't need lookback ramp-up
- All bars get values; no NaN gaps at start of dataset
- FEATURE_CODE_VERSION bumped to "v1.1" to invalidate old runs

### 6. max_holding_bars and exit_on_friday Check Before Rule Evaluation

**Tests:** Category 4.1-4.7

- Engine flow: (1) stop/target check, (2) on_bar() which calls should_exit(), (3) execute action
- should_exit() implementation: **first check primitives** (max_holding_bars, exit_on_friday), **then evaluate exit rule**
- If max_holding_bars triggers → exit_reason = "max_holding_bars"
- If exit_on_friday triggers → exit_reason = "exit_on_friday"
- Both fields are optional; absence means not checked

### 7. Lifecycle Markers Injected Before on_bar() Call

**Test:** `test_engine_lifecycle_markers_in_context` (Category 3.8)

- Engine calculates bars_in_trade = bar_idx - state.entry_bar_idx (when positioned, else 0)
- Engine calculates minutes_in_trade = (ts - state.entry_time).total_seconds() / 60 (when positioned, else 0.0)
- Engine updates bar dict: `bar["bars_in_trade"] = ...`, `bar["minutes_in_trade"] = ...`
- Engine calls strategy.on_bar(bar=bar, ...)
- Strategy can reference these in rules

---

## Fixture Patterns (Reusable)

### Bar Generators

```python
# Spans known days of week (Mon=0, Fri=4, Sun=6)
_flat_bars_with_calendar(n=10, start_day_of_week=0)

# Precise minute intervals for minutes_in_trade testing
_bars_with_precise_timestamps(hours=[10.0, 10.5, 11.0, 11.25])

# Fabricated RSI for realistic entry/exit testing
_bars_with_rsi_values(rsi_sequence=[70, 28, 35, 42, ...])

# Standard fixture with _bar_idx column
_bars_with_bar_idx(n=20)
```

### Strategy Generators

```python
# Deterministic entry/exit for precise holding period tests
_enter_on_bar_n_strategy(n=1, exit_bar=3)

# RulesStrategy definition with max_holding_bars
_max_holding_bars_strategy(max_bars=5)

# RulesStrategy definition with exit_on_friday
_exit_on_friday_strategy(exit_on_friday=True)

# Realistic scenario: RSI-based entry + max_holding_bars
_rsi_entry_max_holding_strategy(max_bars=5)
```

### Context Fixtures for Rules Engine Tests

```python
# Baseline context dict with all features + state fields
@pytest.fixture
def state_context():
    return {
        "bars_in_trade": 3,
        "minutes_in_trade": 45.5,
        "day_of_week": 2,  # Wednesday
        "hour_of_day": 14,
        "rsi_14": 28.5,
        "adx_14": 18.0,
        # ... other features
    }
```

---

## Validation Rules (Category 6)

### max_holding_bars
- Required type: int
- Valid range: 1–1000 (positive, non-zero)
- Fails: max_holding_bars=0, max_holding_bars=-5, max_holding_bars=3.5, max_holding_bars="5"

### exit_on_friday
- Required type: bool
- Valid values: True, False
- Fails: exit_on_friday="yes", exit_on_friday=1, exit_on_friday=null

### State Fields in Rules (bars_in_trade, minutes_in_trade, day_of_week, hour_of_day)
- Add to KNOWN_STATE_FIELDS constant
- Validation must NOT flag these as "unresolved field" errors
- Works in all contexts: leaf comparisons, composites, named refs

---

## Execution Sequence (Recommended)

1. **Unit tests first** (Categories 1, 2, 5, 6)
   - Fast, no engine involvement
   - Validates individual components

2. **Integration tests** (Categories 3, 4, 7)
   - Engine + strategy orchestration
   - Realistic fixtures with multiple bars

3. **Full suite** (.venv/bin/python -m pytest backend/tests/)
   - Ensure no regressions in existing 500+ tests
   - All 47 new tests should pass

---

## Files Modified (Implementation Reference)

### No changes needed yet (test plan only):
- `/Users/danwilden/Developer/Medallion/backend/features/compute.py` — add calendar features, bump v1.1
- `/Users/danwilden/Developer/Medallion/backend/strategies/state.py` — add entry_bar_idx field
- `/Users/danwilden/Developer/Medallion/backend/backtest/engine.py` — inject lifecycle markers
- `/Users/danwilden/Developer/Medallion/backend/strategies/rules_strategy.py` — add primitives check
- `/Users/danwilden/Developer/Medallion/backend/strategies/validation.py` — add KNOWN_STATE_FIELDS + validators

### Test files to create/extend:
- `/Users/danwilden/Developer/Medallion/backend/tests/test_feature_compute.py` (+ 6 tests)
- `/Users/danwilden/Developer/Medallion/backend/tests/test_strategy.py` (+ 5 tests)
- `/Users/danwilden/Developer/Medallion/backend/tests/test_backtest.py` (+ 16 tests)
- `/Users/danwilden/Developer/Medallion/backend/tests/test_rules_engine.py` (+ 6 tests)
- `/Users/danwilden/Developer/Medallion/backend/tests/test_validation.py` or extend test_strategy.py (+ 10 tests)
- `/Users/danwilden/Developer/Medallion/backend/tests/test_backtest_integration.py` (+ 4 tests, or add to test_backtest.py)

---

## Success Criteria

✓ All 47 tests pass deterministically
✓ No flaky tests (frozen/deterministic fixtures only)
✓ No regressions in existing 500+ tests
✓ Feature code version bumped (v1.0 → v1.1)
✓ Edge cases documented in test names & docstrings
✓ No NaN in calendar features
✓ Lifecycle markers injected before on_bar() (verified via mock capture)
✓ Validation errors raised for invalid primitives (negative int, non-bool)
✓ bars_in_trade=1 on entry bar (off-by-one validated explicitly)

---

## Full Test Specification Document

See: `/Users/danwilden/Developer/Medallion/TEST_PLAN_CALENDAR_FEATURES.md`

Contains detailed specifications for all 47 tests with:
- Test function names
- Exact assertions
- Fixture requirements
- Edge case notes
- Implementation hints

