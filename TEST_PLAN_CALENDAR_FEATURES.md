# Test Plan: Calendar Features, Trade Holding Time, and Native Strategy Primitives

**Status:** Design Phase (no code implementation yet)
**Last Updated:** 2026-03-16
**Scope:** Feature computation enhancements + engine lifecycle tracking + strategy primitives

---

## Overview

This test plan validates five interconnected features:

1. **Calendar features in compute.py** — day_of_week, is_friday, hour_of_day columns
2. **StrategyState lifecycle tracking** — entry_bar_idx for precise holding period measurement
3. **Engine injection of lifecycle markers** — bars_in_trade, minutes_in_trade injected before on_bar()
4. **RulesStrategy native primitives** — max_holding_bars and exit_on_friday as first-class fields
5. **Validation and DSL support** — ensure new fields are recognized and validated correctly

---

## Test Infrastructure

**Test framework:** pytest
**Run command:** `.venv/bin/python -m pytest backend/tests/`

### Test files to create/extend:

1. **test_feature_compute.py** (add calendar feature tests)
2. **test_backtest.py** (add engine lifecycle and holding-time tests)
3. **test_rules_engine.py** (add state field evaluation tests)
4. **test_strategy.py** (add StrategyState entry_bar_idx tests)
5. **test_validation.py** or existing validation tests (add max_holding_bars / exit_on_friday validation)

---

## Test Categories

### Category 1: Calendar Feature Computation (test_feature_compute.py additions)

#### Assertion: Calendar columns are computed correctly without leakage

**Test 1.1: day_of_week column exists and is present**
- **Name:** `test_calendar_features_day_of_week_present`
- **Verifies:** `day_of_week` is in the computed feature columns
- **Fixture needed:** Standard bars from 2024-01-01 (Monday) to 2024-01-07 (Sunday)
- **Assertion:** Feature array includes `day_of_week` column with dtype int
- **Edge case:** Ensure it's not a float with NaN values (should be deterministic from timestamp)

**Test 1.2: day_of_week has correct values for known dates**
- **Name:** `test_day_of_week_values_correct`
- **Verifies:**
  - 2024-01-05 (Friday) → day_of_week = 4
  - 2024-01-01 (Monday) → day_of_week = 0
  - 2024-01-07 (Sunday) → day_of_week = 6
- **Fixture needed:** Bars with fabricated timestamps for known weekdays
- **How to construct:** Create 7 bars (one per day of week), all with hour=0, minute=0
- **Assertion:** Retrieved feature at each timestamp matches expected day_of_week value

**Test 1.3: is_friday column is binary and correct**
- **Name:** `test_is_friday_column_values`
- **Verifies:**
  - Friday bars (day_of_week == 4) → is_friday = 1
  - Non-Friday bars → is_friday = 0
- **Fixture needed:** Mixed bars including Fridays and non-Fridays
- **Assertion:** is_friday is either 0 or 1 (never NaN, never other values)
- **Edge case:** Weekend (Saturday/Sunday) should still produce 0, not NaN

**Test 1.4: hour_of_day matches timestamp.hour**
- **Name:** `test_hour_of_day_column_values`
- **Verifies:**
  - Bar at 2024-01-01 10:00 UTC → hour_of_day = 10
  - Bar at 2024-01-01 00:00 UTC → hour_of_day = 0
  - Bar at 2024-01-01 23:00 UTC → hour_of_day = 23
- **Fixture needed:** Bars with various hours (0, 6, 12, 18, 23)
- **Assertion:** Retrieved hour_of_day matches datetime.hour for each timestamp
- **Edge case:** Ensure UTC interpretation (no timezone-aware conversions that might drift)

**Test 1.5: No NaN in calendar columns (deterministic derivation)**
- **Name:** `test_calendar_features_no_nan`
- **Verifies:** Calendar features never produce NaN
- **Fixture needed:** Any standard bar set (20+ bars)
- **Assertion:**
  - `day_of_week.isna().any() == False`
  - `is_friday.isna().any() == False`
  - `hour_of_day.isna().any() == False`
- **Why:** These are timestamp-derived; no lookback needed; unlike indicators that require ramp-up

**Test 1.6: FEATURE_CODE_VERSION bumped to v1.1**
- **Name:** `test_feature_code_version_v1_1`
- **Verifies:** `FEATURE_CODE_VERSION == "v1.1"` in compute.py
- **Assertion:** Direct import and comparison
- **Why:** Bumping version invalidates old feature runs; regression test ensures version wasn't forgotten

---

### Category 2: StrategyState entry_bar_idx Tracking (test_strategy.py additions)

#### Assertion: entry_bar_idx is correctly set, read, and reset

**Test 2.1: entry_bar_idx initializes to -1**
- **Name:** `test_strategy_state_entry_bar_idx_init`
- **Verifies:** New StrategyState() has entry_bar_idx == -1
- **Assertion:** `state.entry_bar_idx == -1`
- **No fixture:** Just instantiate and check

**Test 2.2: entry_bar_idx set on open_trade()**
- **Name:** `test_strategy_state_entry_bar_idx_on_open_trade`
- **Verifies:** Caller passes bar_idx when calling open_trade(); state records it
- **Fixture needed:** Instantiated StrategyState
- **How to call:** `state.open_trade(side="long", entry_time=ts, entry_price=1.1, quantity=10000, bar_idx=5)`
  - **Wait:** Check the actual open_trade() signature — it may not accept bar_idx yet
  - **If missing:** This is part of the implementation; test assumes it will be added
- **Assertion:** After `open_trade(..., bar_idx=5)`, `state.entry_bar_idx == 5`

**Test 2.3: entry_bar_idx cleared on close_trade()**
- **Name:** `test_strategy_state_entry_bar_idx_on_close_trade`
- **Verifies:** Calling close_trade() resets entry_bar_idx to -1
- **Fixture needed:** StrategyState with an open position (entry_bar_idx=5)
- **Steps:**
  1. Open trade with entry_bar_idx=5
  2. Call close_trade(exit_time=ts)
  3. Assert entry_bar_idx == -1
- **Assertion:** `state.entry_bar_idx == -1` after close_trade()

**Test 2.4: entry_bar_idx cleared on reset()**
- **Name:** `test_strategy_state_entry_bar_idx_on_reset`
- **Verifies:** reset() clears entry_bar_idx
- **Fixture needed:** StrategyState with entry_bar_idx=5 (positioned)
- **Steps:**
  1. Set entry_bar_idx=5
  2. Call reset()
  3. Assert entry_bar_idx == -1
- **Assertion:** `state.entry_bar_idx == -1` after reset()

**Test 2.5: entry_bar_idx tracks multiple trades**
- **Name:** `test_strategy_state_entry_bar_idx_multiple_trades`
- **Verifies:** entry_bar_idx can be updated across trade cycles
- **Fixture needed:** StrategyState
- **Steps:**
  1. Open trade 1 with bar_idx=2
  2. Assert entry_bar_idx == 2
  3. Close trade 1
  4. Assert entry_bar_idx == -1
  5. Open trade 2 with bar_idx=10
  6. Assert entry_bar_idx == 10
  7. Close trade 2
  8. Assert entry_bar_idx == -1
- **Assertion:** entry_bar_idx updates correctly across cycles

---

### Category 3: Engine Lifecycle Marker Injection (test_backtest.py additions)

#### Assertion: Engine injects bars_in_trade and minutes_in_trade before on_bar() call

**Test 3.1: bars_in_trade = 0 before any trade**
- **Name:** `test_engine_bars_in_trade_before_entry`
- **Verifies:** On bars before entry, bars_in_trade is 0 in the context passed to on_bar()
- **Fixture needed:**
  - Strategy that enters on bar 3, exits on bar 6
  - 6 bars total, starting 2024-01-01 00:00
- **Implementation detail:** Create a mock strategy that captures the bar dict passed to on_bar()
- **Assertion:** For bars 0-2 (before entry), captured bar has `bars_in_trade == 0`

**Test 3.2: bars_in_trade = 1 on entry bar**
- **Name:** `test_engine_bars_in_trade_on_entry_bar`
- **Verifies:** On the bar of entry, bars_in_trade = 1
- **Fixture needed:** Same as 3.1
- **Assertion:** For bar 3 (entry bar), captured bar has `bars_in_trade == 1`
- **Critical edge case:** bars_in_trade counts the current bar as the 1st holding bar

**Test 3.3: bars_in_trade increments with each bar while positioned**
- **Name:** `test_engine_bars_in_trade_increments`
- **Verifies:**
  - Entry at bar 2 (bars_in_trade = 1)
  - Bar 3: bars_in_trade = 2
  - Bar 4: bars_in_trade = 3
  - Bar 5: bars_in_trade = 4 (then exit)
- **Fixture needed:**
  - Strategy enters on bar 2, exits on bar 5
  - 6+ bars total
- **Implementation:** Capture bars_in_trade at each step
- **Assertion:** Sequence is [0, 0, 1, 2, 3, 4, ...]
- **Critical edge case:** Off-by-one errors — ensure entry bar is 1, not 0

**Test 3.4: bars_in_trade resets to 0 after exit**
- **Name:** `test_engine_bars_in_trade_after_exit`
- **Verifies:** After closing a position, bars_in_trade returns to 0
- **Fixture needed:** Trade entered at bar 2, exited at bar 4, bars 5-6 flat
- **Assertion:** Bar 5 and 6 have bars_in_trade == 0

**Test 3.5: minutes_in_trade = 0.0 before any trade**
- **Name:** `test_engine_minutes_in_trade_before_entry`
- **Verifies:** Before entry, minutes_in_trade is 0.0
- **Fixture needed:** Bars at hourly intervals
- **Assertion:** For bars before entry, minutes_in_trade == 0.0

**Test 3.6: minutes_in_trade reflects elapsed time from entry**
- **Name:** `test_engine_minutes_in_trade_elapsed_time`
- **Verifies:**
  - Entry at 2024-01-01 10:00 UTC
  - Bar at 10:30 UTC: minutes_in_trade = 30.0
  - Bar at 11:00 UTC: minutes_in_trade = 60.0
  - Bar at 11:15 UTC: minutes_in_trade = 75.0
- **Fixture needed:**
  - Bars at 10:00, 10:30, 11:00, 11:15 (hourly not required)
  - Strategy enters at 10:30
- **Implementation:** Use fabricated timestamps at precise intervals
- **Assertion:** minutes_in_trade matches expected elapsed minutes (within 0.01 tolerance for float precision)

**Test 3.7: minutes_in_trade is 0.0 after exit**
- **Name:** `test_engine_minutes_in_trade_after_exit`
- **Verifies:** After exiting, minutes_in_trade reverts to 0.0
- **Fixture needed:** Trade cycle with flat bars after exit
- **Assertion:** Post-exit bars have minutes_in_trade == 0.0

**Test 3.8: Lifecycle markers accessible in rules context**
- **Name:** `test_engine_lifecycle_markers_in_context`
- **Verifies:** bars_in_trade and minutes_in_trade are available in the context dict when rules are evaluated
- **Fixture needed:** RulesStrategy with exit rule referencing bars_in_trade
- **Implementation:**
  - Strategy: enter on bar 1, exit when bars_in_trade >= 3
  - Verify trade closes exactly on bar 3 (entry=1, 2, 3 then exit)
- **Assertion:** Trade duration is 3 bars as expected

---

### Category 4: RulesStrategy Native Primitives (test_backtest.py + test_strategy.py additions)

#### Assertion: max_holding_bars and exit_on_friday force exit before rule evaluation

**Test 4.1: max_holding_bars terminates trade after N bars**
- **Name:** `test_engine_max_holding_bars_force_exit`
- **Verifies:** Strategy with max_holding_bars=3 closes trade after 3 bars
- **Fixture needed:**
  - Strategy definition: `{"entry_long": ..., "exit": {"field": "close", "op": "gt", "value": 999}, "max_holding_bars": 3}`
  - 10 bars total
  - Entry condition: bar_idx == 1
  - Normal exit condition: never true (close never > 999)
  - Expected: Trade enters bar 1, holds bars 1-2-3, then closes at bar 3 due to max_holding_bars
- **Assertion:**
  - Trade count == 1
  - Trade.holding_period == 3
  - Trade.exit_reason == "max_holding_bars"
- **Critical edge case:** bars_in_trade=1 at entry, so max_holding_bars=3 means holding for exactly 3 bars

**Test 4.2: max_holding_bars=1 exits immediately (tightly tested)**
- **Name:** `test_engine_max_holding_bars_one`
- **Verifies:** max_holding_bars=1 closes on the same bar as entry (entry bar only)
- **Fixture needed:** Same as 4.1 but max_holding_bars=1
- **Expected:** Trade.holding_period == 1
- **Assertion:** Trade closes with holding_period == 1
- **Why:** Catches off-by-one in >= vs > comparison

**Test 4.3: max_holding_bars with normal rule-based exit (rule wins if earlier)**
- **Name:** `test_engine_max_holding_bars_vs_rule_exit`
- **Verifies:** If normal exit rule fires before max_holding_bars, rule exit takes precedence
- **Fixture needed:**
  - max_holding_bars=5
  - exit rule: bar_idx >= 3
  - Entry on bar 1
  - Expected: Trade exits on bar 3 (rule fires first), exit_reason = "strategy_signal" (not "max_holding_bars")
- **Assertion:**
  - Trade.holding_period == 3
  - Trade.exit_reason != "max_holding_bars"

**Test 4.4: exit_on_friday closes trade on Friday**
- **Name:** `test_engine_exit_on_friday_force_close`
- **Verifies:** Strategy with exit_on_friday=True closes any open trade on a Friday bar
- **Fixture needed:**
  - Strategy definition: `{"entry_long": ..., "exit": {"field": "close", "op": "gt", "value": 999}, "exit_on_friday": true}`
  - Bars spanning a Friday:
    - 2024-01-08 (Monday) — entry bar
    - 2024-01-09 (Tuesday)
    - 2024-01-10 (Wednesday)
    - 2024-01-11 (Thursday)
    - 2024-01-12 (Friday) — should close
    - 2024-01-15 (Monday) — should be flat
  - Entry condition: bar_idx == 0 (Monday)
  - Normal exit: never true (close never > 999)
  - Expected: Trade closes on Friday (bar 4)
- **Assertion:**
  - Trade count == 1
  - Trade.exit_time == Friday's timestamp
  - Trade.exit_reason == "exit_on_friday"

**Test 4.5: exit_on_friday=False doesn't force Friday exit**
- **Name:** `test_engine_exit_on_friday_disabled`
- **Verifies:** With exit_on_friday=False (or absent), Friday bars don't force exit
- **Fixture needed:** Same as 4.4 but exit_on_friday=False or omitted
- **Expected:** Trade continues past Friday (does not close on Friday)
- **Assertion:** Trade.exit_time is NOT a Friday, or trade never closes

**Test 4.6: exit_on_friday respects rule-based exit (rule wins if earlier)**
- **Name:** `test_engine_exit_on_friday_vs_rule_exit`
- **Verifies:** If rule exit fires before Friday, rule takes precedence
- **Fixture needed:**
  - exit_on_friday=True
  - exit rule: bar_idx >= 2
  - Bars: Mon(0), Tue(1), Wed(2) — rule fires Wed
  - Expected: Trade exits Wed, exit_reason != "exit_on_friday"
- **Assertion:**
  - Trade.exit_time == Wednesday's timestamp
  - Trade.exit_reason == "strategy_signal" (or "rule_exit", depending on naming convention)

**Test 4.7: Both max_holding_bars and exit_on_friday; max wins**
- **Name:** `test_engine_max_holding_bars_and_exit_on_friday`
- **Verifies:** When both fire on the same bar, both are set but one exit_reason is recorded
- **Fixture needed:**
  - max_holding_bars=5
  - exit_on_friday=True
  - Enter Monday (bar 0)
  - Friday (bar 4) is also the 5th bar (0-4 = 5 bars)
  - Expected: Trade closes on Friday
  - exit_reason: ?? (design decision — could be "max_holding_bars" if checked first, or "exit_on_friday" if checked in that order)
- **Assertion:** Trade closes with one exit_reason (test documents the precedence)

**Test 4.8: max_holding_bars/exit_on_friday ignored when flat**
- **Name:** `test_engine_lifecycle_primitives_ignored_flat`
- **Verifies:** Primitives only apply to positioned trades
- **Fixture needed:**
  - Strategy with max_holding_bars=2, exit_on_friday=True
  - 10 flat bars (never enter)
  - Expected: All bars are flat; no trades
- **Assertion:** Trade count == 0

---

### Category 5: Rules Engine State Field Support (test_rules_engine.py additions)

#### Assertion: bars_in_trade, minutes_in_trade, day_of_week evaluate correctly

**Test 5.1: bars_in_trade in leaf comparison**
- **Name:** `test_rules_engine_bars_in_trade_gte`
- **Verifies:** Rule `{"field": "bars_in_trade", "op": "gte", "value": 5}` evaluates correctly
- **Fixture needed:**
  - Context dict: `{"bars_in_trade": 5, ...other fields...}`
  - Rule: `{"field": "bars_in_trade", "op": "gte", "value": 5}`
- **Assertion:** `evaluate(rule, context) == True`

**Test 5.2: bars_in_trade in composite rule**
- **Name:** `test_rules_engine_bars_in_trade_in_composite`
- **Verifies:** bars_in_trade works in all/any/not composites
- **Fixture needed:** Context with bars_in_trade=3
- **Rules:**
  - `{"all": [{"field": "bars_in_trade", "op": "gte", "value": 3}, {"field": "rsi_14", "op": "lt", "value": 50}]}`
- **Assertion:** Composite evaluates correctly based on both conditions

**Test 5.3: day_of_week in rule evaluation**
- **Name:** `test_rules_engine_day_of_week_eq`
- **Verifies:** Rule `{"field": "day_of_week", "op": "eq", "value": 4}` detects Friday
- **Fixture needed:** Context with day_of_week=4 (Friday)
- **Assertion:** `evaluate(rule, context) == True`

**Test 5.4: day_of_week neq for non-Friday**
- **Name:** `test_rules_engine_day_of_week_neq`
- **Verifies:** `{"field": "day_of_week", "op": "neq", "value": 4}` is True for Mon-Thu, Sat-Sun
- **Fixture needed:** Context with day_of_week=0 (Monday)
- **Assertion:** evaluate returns True

**Test 5.5: hour_of_day in rule (e.g., session filter)**
- **Name:** `test_rules_engine_hour_of_day_in_session`
- **Verifies:** Rule `{"field": "hour_of_day", "op": "in", "value": [8, 9, 10]}` filters by hour
- **Fixture needed:** Context with hour_of_day=9
- **Assertion:** evaluate returns True; with hour_of_day=14, returns False

**Test 5.6: minutes_in_trade comparison**
- **Name:** `test_rules_engine_minutes_in_trade_lt`
- **Verifies:** `{"field": "minutes_in_trade", "op": "lt", "value": 120.0}` for holding < 2 hours
- **Fixture needed:** Context with minutes_in_trade=45.0
- **Assertion:** evaluate returns True

---

### Category 6: Validation Updates (test_validation.py or test_strategy.py additions)

#### Assertion: New fields are recognized, validated, and don't cause spurious errors

**Test 6.1: bars_in_trade in exit rule passes validation**
- **Name:** `test_validation_bars_in_trade_no_error`
- **Verifies:** Exit rule using bars_in_trade does NOT raise unresolved field error
- **Fixture needed:** Strategy definition with exit rule `{"field": "bars_in_trade", "op": "gte", "value": 5}`
- **Assertion:** `validate_rules_strategy(definition_json)` returns empty errors list

**Test 6.2: minutes_in_trade in exit rule passes validation**
- **Name:** `test_validation_minutes_in_trade_no_error`
- **Verifies:** exit rule with minutes_in_trade is valid
- **Fixture needed:** Strategy definition with exit `{"field": "minutes_in_trade", "op": "gte", "value": 60.0}`
- **Assertion:** No validation errors

**Test 6.3: max_holding_bars valid integer passes validation**
- **Name:** `test_validation_max_holding_bars_valid`
- **Verifies:** Definition with `"max_holding_bars": 5` is valid
- **Fixture needed:** Strategy with max_holding_bars in top-level definition
- **Assertion:** validate_rules_strategy returns no errors

**Test 6.4: max_holding_bars negative value fails validation**
- **Name:** `test_validation_max_holding_bars_negative_fails`
- **Verifies:** `"max_holding_bars": -1` raises validation error
- **Fixture needed:** Strategy definition with max_holding_bars=-1
- **Assertion:** validate_rules_strategy returns errors containing "max_holding_bars"

**Test 6.5: max_holding_bars zero fails validation**
- **Name:** `test_validation_max_holding_bars_zero_fails`
- **Verifies:** `"max_holding_bars": 0` raises validation error
- **Fixture needed:** Strategy definition with max_holding_bars=0
- **Assertion:** validate_rules_strategy returns error mentioning max_holding_bars

**Test 6.6: max_holding_bars non-integer fails validation**
- **Name:** `test_validation_max_holding_bars_non_integer_fails`
- **Verifies:** `"max_holding_bars": 3.5` raises validation error (must be int)
- **Fixture needed:** Strategy definition with max_holding_bars=3.5
- **Assertion:** Validation error returned

**Test 6.7: exit_on_friday=True passes validation**
- **Name:** `test_validation_exit_on_friday_true_valid`
- **Verifies:** `"exit_on_friday": true` is valid
- **Assertion:** No validation errors

**Test 6.8: exit_on_friday=False passes validation**
- **Name:** `test_validation_exit_on_friday_false_valid`
- **Verifies:** Explicitly setting to False is valid
- **Assertion:** No validation errors

**Test 6.9: exit_on_friday non-boolean fails validation**
- **Name:** `test_validation_exit_on_friday_non_boolean_fails`
- **Verifies:** `"exit_on_friday": "yes"` (string) raises error
- **Fixture needed:** Strategy definition with exit_on_friday="yes"
- **Assertion:** Validation error returned

**Test 6.10: KNOWN_STATE_FIELDS recognized for signal fields validation**
- **Name:** `test_validation_known_state_fields_recognized`
- **Verifies:** bars_in_trade, minutes_in_trade are in KNOWN_STATE_FIELDS and don't cause "unresolved field" errors in signal context
- **Implementation detail:** If validate_signal_fields() exists, ensure it doesn't flag these as missing
- **Assertion:** No signal validation errors when bars_in_trade is referenced

---

### Category 7: End-to-End Scenario Tests (test_backtest.py + test_backtest_integration.py)

#### Assertion: Complete realistic workflow with calendar, holding time, and primitives

**Test 7.1: Full scenario — enter/hold/exit with max_holding_bars**
- **Name:** `test_e2e_max_holding_bars_realistic`
- **Scenario:**
  - Strategy: enter_long when rsi_14 < 30, exit when rsi_14 > 70 OR max_holding_bars=5
  - Dataset: 20 hourly bars with fabricated RSI values
  - RSI values: [60, 70, 28, 35, 42, 48, 55, 72, ...]
  - Entry: bar 2 (rsi=28)
  - Exit options: bar 7 (rsi=72, rule fires) OR bar 6 (max_holding_bars=5)
  - Expected: Exit on bar 6 due to max_holding_bars
- **Fixture needed:** RulesStrategy with both exit rule and max_holding_bars=5
- **Assertion:**
  - Trade count == 1
  - Trade.entry_bar_idx == 2 (or 1 if 0-indexed)
  - Trade.exit_bar_idx == 6 (or 5)
  - Trade.exit_reason == "max_holding_bars"
  - Trade.holding_period == 5

**Test 7.2: Full scenario — exit_on_friday in week**
- **Name:** `test_e2e_exit_on_friday_realistic`
- **Scenario:**
  - Strategy: enters Monday, should exit Friday (no other exit condition)
  - Bars: Mon, Tue, Wed, Thu, Fri, Mon (next week)
  - Expected: Trade exits Friday
- **Fixture needed:** RulesStrategy with exit_on_friday=True, entry on Monday, exit never true
- **Assertion:**
  - Trade count == 1
  - Trade.exit_reason == "exit_on_friday"
  - Trade.holding_period == 5

**Test 7.3: Full scenario — both max_holding_bars and exit_on_friday**
- **Name:** `test_e2e_both_primitives`
- **Scenario:**
  - max_holding_bars=3
  - exit_on_friday=True
  - Entry: Wednesday
  - Friday is day 2 of hold (Wed, Thu, Fri)
  - max_holding_bars triggers on Saturday (day 3)
  - But Friday comes first
  - Expected: Exit Friday with exit_reason="exit_on_friday"
- **Fixture needed:** RulesStrategy with both fields, entry mid-week
- **Assertion:**
  - Trade.exit_reason == "exit_on_friday" (not "max_holding_bars")
  - Trade exits on Friday

**Test 7.4: Full scenario — calendar feature in rule**
- **Name:** `test_e2e_day_of_week_in_rule`
- **Scenario:**
  - Strategy: enter when (rsi < 30) AND (day_of_week != 4) [no Friday entries]
  - Test that Friday bars never trigger entries
  - Verify entries only on Mon-Thu, Sat-Sun
- **Fixture needed:** RulesStrategy with entry rule including day_of_week condition
- **Assertion:** All trades entered on non-Friday bars

---

## Edge Cases and Gotchas

### Critical Off-by-One Issues

1. **bars_in_trade == 1 on entry bar, not 0**
   - Entry at bar 5 → bars_in_trade = 1, not 0
   - Implication: max_holding_bars=5 means hold bars 5-6-7-8-9 (exit on bar 9)
   - Test 4.1 and 3.2 specifically validate this

2. **day_of_week == 4 for Friday, not 5**
   - Python datetime.weekday(): 0=Mon, 4=Fri, 6=Sun
   - Ensure consistent with ISO/pandas conventions
   - Test 1.2 validates specific dates

3. **minutes_in_trade is a float, not int**
   - Comparison might fail if compared to int (30.0 != 30 in strict equality)
   - Test 3.6 uses 0.01 tolerance for float comparison

4. **entry_bar_idx == -1 when flat, never None**
   - Important for rules that check `bars_in_trade > 0` (avoid -1 leaking)
   - Test 2.1 ensures -1 initialization

### Data Leakage Prevention

1. **Calendar features derived only from timestamp, not bars**
   - No lookback needed; no NaN ramp-up period
   - Test 1.5 ensures no NaN

2. **bars_in_trade only computed from state.entry_bar_idx, not external data**
   - Injected BEFORE on_bar() so strategy can reference it
   - Test 3.8 verifies it's in context

3. **minutes_in_trade derived from entry_time and current timestamp only**
   - No lookback, deterministic
   - Test 3.6 validates elapsed time calculation

### State Mutation Risks

1. **reset() must clear entry_bar_idx**
   - Between backtest runs, stale entry_bar_idx corrupts next run
   - Test 2.4 ensures reset() clears it

2. **Validation must allow bars_in_trade/minutes_in_trade/day_of_week in rules**
   - Otherwise valid strategies fail validation
   - Tests 6.1, 6.2, 6.10 ensure no false errors

---

## Test Fixtures Summary

### Bar Fixtures Needed

1. **_flat_bars_with_calendar(n=10, start_day_of_week=0)**
   - Generate n bars spanning known days of week
   - Example: start Monday, span 2 weeks → Fri is bar 4, bar 11
   - Used in: Tests 1.1-1.5, 4.4-4.5, 7.2

2. **_bars_with_precise_timestamps(hours=[10, 10.5, 11, 11.25, ...])**
   - Generate bars at precise minute intervals
   - Used in: Test 3.6 (minutes_in_trade)

3. **_bars_with_rsi_values(rsi_sequence=[70, 28, 35, ...])**
   - Generate bars with fabricated RSI column
   - Used in: Test 7.1 (realistic scenario)

4. **_bars_with_bar_idx(n)**
   - Standard bars with `_bar_idx` column (0 to n-1)
   - Used in most engine tests (3.1-3.8, 4.1-4.8)

### Strategy Fixtures Needed

1. **_enter_on_bar_n_strategy(n, exit_bar=None)**
   - BaseStrategy or RulesStrategy that enters on bar n
   - Example: `_enter_on_bar_n_strategy(2, exit_bar=5)` → enter bar 2, exit bar 5

2. **_max_holding_bars_strategy(max_bars=3)**
   - RulesStrategy definition with max_holding_bars field
   - Entry rule never false, exit rule never true
   - Used in: Tests 4.1-4.3

3. **_exit_on_friday_strategy(exit_on_friday=True)**
   - RulesStrategy definition with exit_on_friday field
   - Entry rule enters on bar 0, exit rule never true
   - Used in: Tests 4.4-4.7

4. **_rsi_entry_max_holding_strategy(max_bars=5)**
   - Realistic strategy with RSI entry + max_holding_bars
   - Used in: Test 7.1

---

## Test Execution Plan

### Phase 1: Unit Tests (no engine involvement)
1. Calendar features (Category 1) — fast, deterministic
2. StrategyState (Category 2) — fast, isolated
3. Validation (Category 6) — fast, isolated
4. Rules engine (Category 5) — fast, no backtest

### Phase 2: Integration Tests (engine involved)
1. Engine lifecycle markers (Category 3) — medium, deterministic fixture
2. RulesStrategy primitives (Category 4) — medium, deterministic fixture
3. End-to-end scenarios (Category 7) — medium, complex fixture

### Expected Test Count

- Category 1: 6 tests
- Category 2: 5 tests
- Category 3: 8 tests
- Category 4: 8 tests
- Category 5: 6 tests
- Category 6: 10 tests
- Category 7: 4 tests

**Total: ~47 new tests**

---

## Success Criteria

1. All tests pass deterministically (no flaky tests)
2. No new warnings or deprecations introduced
3. Feature code version bumped (v1.0 → v1.1)
4. No regressions in existing tests (current suite passes)
5. Edge cases documented in test names and docstrings

---

## Dependencies and Implementation Order

1. **Implement calendar features first** (compute.py)
   - Unblocks: Category 1 tests, Category 5 tests (day_of_week rules)

2. **Add entry_bar_idx to StrategyState** (state.py)
   - Modify open_trade() signature or add separate field setter
   - Unblocks: Category 2 tests, Category 3 tests (bars_in_trade derivation)

3. **Engine injection of lifecycle markers** (engine.py)
   - Before on_bar() call, inject bars_in_trade and minutes_in_trade
   - Unblocks: Category 3 tests

4. **Add max_holding_bars and exit_on_friday to RulesStrategy** (rules_strategy.py)
   - Check in should_exit() BEFORE rule evaluation
   - Unblocks: Category 4 tests, Category 7 tests

5. **Update validation** (validation.py)
   - Add KNOWN_STATE_FIELDS constant
   - Update max_holding_bars/exit_on_friday validators
   - Unblocks: Category 6 tests

---

## Notes for Test Implementer

- Use absolute timestamps (datetime objects with tzinfo=UTC)
- All bar indices are 0-indexed (bar 0 is first bar)
- Test fixtures should be isolated (no file I/O, no actual repos)
- Use mock.MagicMock for repositories where needed
- Capture strategy.on_bar() calls to inspect injected fields (bars_in_trade, minutes_in_trade)
- Document which tests require frozen/deterministic bars vs. randomized
- Mark integration tests with @pytest.mark.integration if separate CI stages desired

