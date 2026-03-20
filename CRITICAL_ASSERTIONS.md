# Critical Assertions — Phase 7 Test Plan

**These tests catch the most likely bugs.** Run these first if time is limited.

---

## 1. bars_in_trade = 1 on Entry Bar (Not 0) — HIGHEST RISK

**Test:** `test_engine_bars_in_trade_on_entry_bar` (test_backtest.py, Category 3.2)

**Why critical:** Off-by-one bugs corrupt all holding-period logic.

**Assertion:**
```python
# Entry on bar 2, bars_in_trade should be 1 (counts current bar)
assert captured_bars[2]["bars_in_trade"] == 1
assert captured_bars[3]["bars_in_trade"] == 2
assert captured_bars[4]["bars_in_trade"] == 3
```

**What fails silently:** If bars_in_trade=0 on entry, max_holding_bars=5 would allow 6-bar holds (0-1-2-3-4-5).

**Detection:** Run this first. If it fails, all Category 3 & 4 tests will fail.

---

## 2. max_holding_bars Actually Closes Trade

**Test:** `test_engine_max_holding_bars_force_exit` (test_backtest.py, Category 4.1)

**Why critical:** Entire feature is useless if primitive doesn't actually trigger exit.

**Assertion:**
```python
# Strategy enters on bar 1, exit rule never fires
# But max_holding_bars=3 should force exit
trades = run_backtest(strategy, bars, ...)
assert len(trades) == 1
assert trades[0].holding_period == 3
assert trades[0].exit_reason == "max_holding_bars"
```

**What fails silently:** If max_holding_bars is parsed but not checked in should_exit(), trade never closes.

---

## 3. entry_bar_idx = -1 When Flat (Not None, Not 0)

**Test:** `test_strategy_state_entry_bar_idx_init` (test_strategy.py, Category 2.1)

**Why critical:** Prevents "-1 leakage" into bars_in_trade calculations. If None or 0, engine logic breaks.

**Assertion:**
```python
state = StrategyState()
assert state.entry_bar_idx == -1
assert state.entry_bar_idx is not None
```

**What fails silently:** If -1 isn't enforced, bars_in_trade = bar_idx - None throws TypeError or gives wrong result.

---

## 4. Calendar Features Have No NaN

**Test:** `test_calendar_features_no_nan` (test_feature_compute.py, Category 1.5)

**Why critical:** NaN in features breaks rules evaluation and can crash backtester.

**Assertion:**
```python
features_df = compute_features(bars)
assert not features_df["day_of_week"].isna().any()
assert not features_df["is_friday"].isna().any()
assert not features_df["hour_of_day"].isna().any()
```

**What fails silently:** If lookback window isn't handled, first N rows are NaN. Rules comparing day_of_week fail silently with NaN.

---

## 5. exit_on_friday Actually Exits on Friday

**Test:** `test_engine_exit_on_friday_force_close` (test_backtest.py, Category 4.4)

**Why critical:** If exit_on_friday=True doesn't work, entire weekend-risk-management feature fails.

**Assertion:**
```python
# Entry Monday (bar 0), Friday is bar 4
# No other exit condition
trades = run_backtest(strategy, bars_mon_to_fri, ...)
assert len(trades) == 1
assert trades[0].exit_time.weekday() == 4  # Friday
assert trades[0].exit_reason == "exit_on_friday"
```

**What fails silently:** If exit_on_friday isn't checked in should_exit(), trade holds past Friday.

---

## 6. Lifecycle Markers Are Injected Before on_bar() Call

**Test:** `test_engine_lifecycle_markers_in_context` (test_backtest.py, Category 3.8)

**Why critical:** If markers aren't in bar dict, rules can't reference them (ValueError: field not found).

**Assertion:**
```python
# Capture bar dict in strategy.on_bar() call
# Verify bars_in_trade is present
assert "bars_in_trade" in captured_bar_dict
assert "minutes_in_trade" in captured_bar_dict
assert captured_bar_dict["bars_in_trade"] == 3  # (example: bar 3 of trade)
```

**What fails silently:** If engine doesn't inject fields, strategy rules fail with "field not found" error on evaluation.

---

## 7. Validation Allows bars_in_trade in Exit Rules

**Test:** `test_validation_bars_in_trade_no_error` (test_validation.py, Category 6.1)

**Why critical:** Otherwise, valid strategies fail validation with spurious "unresolved field" errors.

**Assertion:**
```python
definition_json = {
    "entry_long": {"field": "rsi_14", "op": "lt", "value": 30},
    "exit": {"field": "bars_in_trade", "op": "gte", "value": 5},
}
errors = validate_rules_strategy(definition_json)
assert len(errors) == 0  # Must pass, not fail
```

**What fails silently:** If KNOWN_STATE_FIELDS isn't added, every strategy using bars_in_trade/minutes_in_trade in rules fails validation.

---

## 8. FEATURE_CODE_VERSION Bumped to v1.1

**Test:** `test_feature_code_version_v1_1` (test_feature_compute.py, Category 1.6)

**Why critical:** Forgetting to bump invalidates old feature runs; backtests use stale features without calendar columns.

**Assertion:**
```python
from backend.features.compute import FEATURE_CODE_VERSION
assert FEATURE_CODE_VERSION == "v1.1"
```

**What fails silently:** Old feature runs (v1.0) without calendar columns are used in new backtests, rules referencing day_of_week fail.

---

## 9. day_of_week = 4 for Friday (Not 5)

**Test:** `test_day_of_week_values_correct` (test_feature_compute.py, Category 1.2)

**Why critical:** Timezone or convention mismatch (ISO weekday vs. calendar module) causes exit_on_friday to trigger wrong day.

**Assertion:**
```python
# 2024-01-05 is a Friday
bar_ts = datetime(2024, 1, 5, 10, 0, 0, tzinfo=UTC)
bar = make_bar_with_timestamp(bar_ts)
features = compute_features([bar])
assert features["day_of_week"].iloc[0] == 4
```

**What fails silently:** If day_of_week=5 for Friday, exit_on_friday rule `day_of_week == 4` never fires.

---

## 10. minutes_in_trade Reflects Correct Elapsed Time

**Test:** `test_engine_minutes_in_trade_elapsed_time` (test_backtest.py, Category 3.6)

**Why critical:** Incorrect calculation breaks holding-time rules (e.g., "exit after 120 minutes").

**Assertion:**
```python
# Entry at 10:00, bar at 11:30
# minutes_in_trade should be 90.0
entry_ts = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
bar_ts = datetime(2024, 1, 1, 11, 30, 0, tzinfo=UTC)
elapsed = (bar_ts - entry_ts).total_seconds() / 60.0
assert captured_bar["minutes_in_trade"] == 90.0
assert abs(captured_bar["minutes_in_trade"] - elapsed) < 0.01
```

**What fails silently:** If calculation is (bar_idx - entry_bar_idx) instead, minutes_in_trade becomes integer bar count, breaking float-based rules.

---

## Run These Tests First (10-Minute Subset)

```bash
.venv/bin/python -m pytest \
  backend/tests/test_backtest.py::test_engine_bars_in_trade_on_entry_bar \
  backend/tests/test_backtest.py::test_engine_max_holding_bars_force_exit \
  backend/tests/test_strategy.py::test_strategy_state_entry_bar_idx_init \
  backend/tests/test_feature_compute.py::test_calendar_features_no_nan \
  backend/tests/test_backtest.py::test_engine_exit_on_friday_force_close \
  backend/tests/test_backtest.py::test_engine_lifecycle_markers_in_context \
  backend/tests/test_validation.py::test_validation_bars_in_trade_no_error \
  backend/tests/test_feature_compute.py::test_feature_code_version_v1_1 \
  backend/tests/test_feature_compute.py::test_day_of_week_values_correct \
  backend/tests/test_backtest.py::test_engine_minutes_in_trade_elapsed_time \
  -v
```

**Expected:** All 10 pass. If any fail, implementation has a critical bug.

---

## Silent Failure Risk Matrix

| Test | Symptom if Fails | Impact | Severity |
|------|------------------|--------|----------|
| bars_in_trade=1 | Trades hold N+1 bars instead of N | Portfolio risk blows | **CRITICAL** |
| max_holding_bars | Trades never close | Unlimited holding | **CRITICAL** |
| entry_bar_idx=-1 | TypeError or wrong bars_in_trade | All engine tests fail | **CRITICAL** |
| No NaN in calendar | NaN in rules context | Rules silently fail | **HIGH** |
| exit_on_friday | Friday risk exposure | Regulatory breach | **HIGH** |
| Lifecycle markers | Field not found error | Rules can't run | **HIGH** |
| Validation allows state fields | Strategies rejected | Feature unusable | **MEDIUM** |
| FEATURE_CODE_VERSION | Stale features used | Wrong backtest results | **HIGH** |
| day_of_week=4 | Wrong day exit | Exit_on_friday broken | **HIGH** |
| minutes_in_trade calc | Wrong holding time | Rules misfire | **MEDIUM** |

