# Phase 5C Code Review — Feature Discovery Agent & Sandbox

**Review Date:** 2026-03-15
**Reviewer:** Code Quality Agent
**Scope:** Sandbox isolation, Feature discovery agent, LangGraph integration, API routes

---

## Summary

Phase 5C adds a complete feature discovery pipeline:
- **sandbox.py**: Subprocess-based code isolation (multiprocessing.Process)
- **compute.py**: Feature computation pipeline + FeatureComputer class
- **evaluate.py**: ANOVA-based feature evaluation
- **feature_library.py**: Persistence + registration logic with F-statistic threshold
- **feature.py (tools)**: Four async tool executors with session caching
- **feature_researcher.py**: Bedrock-powered agentic discovery node
- **supervisor.py**: Extended routing with feature discovery priority
- **graph.py**: LangGraph registration of feature_researcher_node
- **features.py (API)**: Discovery job launch, library query, and polling routes

**Overall Quality Assessment**: PASSED with no blockers. Code is well-structured, security controls are in place, constants are properly defined, and integration is clean.

---

## BLOCKERS

None. All critical constraints are met.

---

## WARNINGS

### 1. **compute.py Line Count: 342 Lines — Close to Threshold**

**File:** `/Users/danwilden/Developer/Medallion/backend/features/compute.py`
**Lines:** 342
**Issue:** The file is just above the soft 300-line threshold, though justified by the inclusion of both the pipeline functions AND the FeatureComputer class.

**Why This Matters:**
- Future maintenance: if feature computation logic expands further, this file may exceed reasonable bounds
- The FeatureComputer class (lines 283–342) is well-isolated but could be moved to a dedicated module if it grows

**Recommendation:**
Watch for growth. If additional FeatureComputer methods are added (e.g., batch_compute, cache management), consider moving the class to `backend/features/computer.py` and importing it in compute.py. Current separation between indicator functions and the Computer class is clean, but not yet split.

**Priority:** Low. No refactoring required now, but flag for future rounds.

---

### 2. **feature_researcher.py Line Count: 518 Lines — Exceeds 300-Line Threshold**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/feature_researcher.py`
**Lines:** 518
**Issue:** This file exceeds the 300-line architectural threshold. However, the justification is strong.

**Why This Matters:**
- The file contains: SYSTEM_PROMPT, RESEARCHER_TOOLS, _build_user_message, _extract_json_from_text, _dispatch_tool, _run_feature_researcher (async main), _collect_session_results, and feature_researcher_node (sync wrapper)
- These are all interdependent — the node requires the tools, tools require the dispatcher, dispatcher requires message builders
- Splitting the file would fragment the workflow across multiple modules and reduce cohesion

**Justification Accepted:**
This mirrors strategy_researcher.py (a tested 5B pattern) and is a single cohesive workflow. The size is acceptable because:
1. All functions serve one purpose: the feature discovery loop
2. Extraction would create import complexity and circular dependencies
3. The functions are logically grouped and documented
4. Tests would become harder to read

**Recommendation:**
Accept the 518-line size for feature_researcher.py. Consider it a baseline for multi-turn LLM agent nodes in this codebase (strategy_researcher is ~500+ lines; backtest_diagnostics is ~300 lines).

**Priority:** Low. Size is justified.

---

### 3. **Shallow Copy in feature_library.upsert() — Preservation of History**

**File:** `/Users/danwilden/Developer/Medallion/backend/features/feature_library.py` (line 180)
**Issue:** In `upsert()`, existing records are shallow-copied: `record = dict(existing)`. This is correct for JSON serialization, but if feature objects ever contain nested mutable structures, mutations would affect the original.

**Current Status:** Safe — all stored fields are primitives, strings, or lists of dicts.

**Recommendation:**
- Document that this is intentional shallow copy for JSON records
- If nested objects are added in the future, upgrade to `deepcopy` or explicit field copying
- No change required now

**Priority:** Very Low. Future-proofing comment only.

---

## OK

### ✅ **sandbox.py: Subprocess Isolation is REAL**

**File:** `/Users/danwilden/Developer/Medallion/backend/features/sandbox.py`
**Verdict:** PASS

- Uses `multiprocessing.Process` (line 107), not exec() in main process
- Pipe-based IPC: pickled DataFrame to child, Series back to parent
- Import whitelist enforced BEFORE spawning (lines 35–43)
- `_check_imports()` regex validates only numpy/pandas imports allowed
- Timeout management: `process.terminate()` on timeout (lines 114–117)
- exec() runs in child process namespace with restricted local_ns = {pd, np, df}
- NaN validation (20% threshold, line 132)
- Clean exception handling and error propagation
- Documentation is clear and security notes present

**Isolation Quality:** Excellent. Safe for LLM-generated code.

---

### ✅ **compute.py: Indirect Sandbox Execution**

**File:** `/Users/danwilden/Developer/Medallion/backend/features/compute.py`
**Verdict:** PASS

- FeatureComputer.compute() **never calls exec() directly**
- Delegates to `execute_feature_code()` from sandbox module (line 340–342)
- Loads bars, builds DataFrame, passes to sandbox
- Returns Series aligned to original index
- Error handling properly re-raises SandboxError, SandboxTimeoutError, SandboxValidationError
- No bypass paths

**Code Isolation Quality:** Excellent. No direct exec() in the feature computation flow.

---

### ✅ **evaluate.py: ANOVA Feature Evaluation**

**File:** `/Users/danwilden/Developer/Medallion/backend/features/evaluate.py`
**Verdict:** PASS

- Straightforward scipy.stats.f_oneway usage
- Joins feature Series to regime labels by timestamp
- Handles edge cases: no labels, fewer than 2 regime classes
- Returns FeatureEvalResult with registered=False (library decides registration)
- No mutations, no side effects
- Logging appropriate for warnings

**Quality:** Clean, focused, minimal.

---

### ✅ **feature_library.py: REGISTRATION_THRESHOLD Constant**

**File:** `/Users/danwilden/Developer/Medallion/backend/features/feature_library.py`
**Verdict:** PASS

- **REGISTRATION_THRESHOLD = 2.0** defined at module level (line 15)
- Registration check uses **strict `>` operator** (line 68): `if eval_result.f_statistic <= REGISTRATION_THRESHOLD`
- This is the correct interpretation: only accept F > 2.0 (not >=)
- Constant is importable from other modules (verified in feature.py line 214)
- Logging clearly documents threshold enforcement (line 70)

**Threshold Logic:** Correct and consistently applied.

---

### ✅ **feature.py (tools): Family Allowlist Check**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/tools/feature.py`
**Verdict:** PASS

- **Family allowlist check is FIRST in propose_feature()** (lines 72–77)
- Happens BEFORE any Bedrock call, compute, or eval
- Uses ALLOWED_FAMILIES from schemas (set of 6 values)
- Hard block on invalid families (returns ProposeFeatureOutput with valid=False)
- Leakage risk is validated but as a warning (not a hard block in propose_feature — library.register() enforces "high" block)
- Caching of specs and eval results happens AFTER validation

**Safety:** Excellent. Invalid families rejected at entry point.

---

### ✅ **feature_researcher.py: research_mode Cleared**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/feature_researcher.py`
**Verdict:** PASS

- **research_mode is cleared to None in all return paths**
  - Line 367: Bedrock error (first call)
  - Line 379: Bedrock throttle exhaustion (first call)
  - Line 450: Bedrock error (tool loop)
  - Line 462: Bedrock throttle exhaustion (tool loop)
  - Line 481: Happy path (final return)
- Ensures supervisor doesn't re-route to feature_researcher after completion
- Task always set to "done" to prevent re-triggering
- Comment on line 481 explicitly documents this intent

**State Management:** Correct. No risk of re-triggering feature discovery.

---

### ✅ **feature_researcher.py: MAX_FEATURES_PER_SESSION Constant**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/feature_researcher.py`
**Verdict:** PASS

- **MAX_FEATURES_PER_SESSION = 5** defined at module level (line 45)
- Used in SYSTEM_PROMPT substitution (line 84)
- Used in tool loop max call calculation (line 332): `MAX_FEATURES_PER_SESSION * 6`
- Consistent across all references
- Documented in workflow comments

**Constant Definition:** Correct and centralized.

---

### ✅ **feature_researcher.py: BedrockAdapter.converse() Used Correctly**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/feature_researcher.py`
**Verdict:** PASS

- **Uses `await adapter.converse()` (not `adapter.invoke()`)**
  - Line 338: First Bedrock call
  - Line 421: Loop Bedrock call
- converse() is the correct Bedrock Converse API method
- Returns ConverseResult with stop_reason, tool_use, content, input_tokens, output_tokens
- Properly awaited in async context
- Tool use extraction via BedrockAdapter.extract_tool_use() is correct pattern

**LLM Integration:** Correct. No invoke() method used.

---

### ✅ **supervisor.py: Feature Discovery Routing Priority**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/supervisor.py`
**Verdict:** PASS

- **research_mode == "discover_features" is at priority 2** (lines 50–52)
- Routing order:
  1. Hard stops: task == "done" OR iteration >= MAX (lines 47–48)
  2. **Feature discovery: research_mode == "discover_features"** ← PRIORITY 2
  3. Seed generation: task == "generate_seed" (lines 55–56)
  4. Backtest diagnosis (lines 59–60)
  5. Comparison (lines 68–75)
  6. Mutation (lines 78–79)
  7. Fallback (lines 86–87)
- Deterministic: no random paths, no conditional dependencies that could cause flapping

**Routing Logic:** Correct priority, deterministic evaluation.

---

### ✅ **supervisor.py: Routing Determinism**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/supervisor.py`
**Verdict:** PASS

- All conditions are exclusive (if-elif chain)
- No simultaneous evaluation of conflicting paths
- Each condition is a pure function of state fields
- No external I/O, no randomness, no side effects
- Returns only {next_node, iteration} — state mutation-safe

**Determinism:** Excellent. Routes are predictable and reproducible.

---

### ✅ **graph.py: feature_researcher_node Registered**

**File:** `/Users/danwilden/Developer/Medallion/backend/agents/graph.py`
**Verdict:** PASS

- **feature_researcher_node imported** (line 7)
- **Registered as a node** (line 37): `graph.add_node("feature_researcher", feature_researcher_node)`
- **Added to conditional edge path map** (line 52): `"feature_researcher": "feature_researcher"`
- **Added edge back to supervisor** (line 61): `graph.add_edge("feature_researcher", "supervisor")`
- All integration points present

**Graph Registration:** Complete and correct.

---

### ✅ **features.py (API): Clean Route Implementation**

**File:** `/Users/danwilden/Developer/Medallion/apps/api/routes/features.py`
**Verdict:** PASS

**Routes Implemented:**
- **POST /discover** (line 37): Launch discovery job, fire background task, return 202
- **GET /discover/{job_id}** (line 77): Poll job status and collect feature_eval_results from library on completion
- **GET /library** (line 120): Query feature library with filters (family, max_leakage, min_f_statistic)
- **GET /library/{name}** (line 141): Retrieve single feature by name

**Implementation Quality:**
- Proper FastAPI Depends() for job_manager and feature_library
- Background task pattern matches research.py (proven 5B implementation)
- Job status polling is correct: awaits loop.run_in_executor for sync feature_researcher_node
- Results collection: feature_library.upsert() called per result
- Error handling: job_manager.fail() on exception, proper logging
- Response models all defined: JobCreatedResponse, FeatureDiscoverJobResponse, FeatureLibraryResponse, FeatureSpec

**API Quality:** Excellent. Consistent with research.py patterns.

---

### ✅ **Integration: get_feature_library Dependency**

**File:** `/Users/danwilden/Developer/Medallion/backend/deps.py`
**Verdict:** PASS

- **get_feature_library() defined** (line 44–46)
- Returns FeatureLibrary(get_metadata_repo())
- LRU cache semantics (dependency provider pattern)
- Used by feature.py, feature_researcher.py, and features.py

**Dependency Injection:** Correct and consistent with other singletons.

---

### ✅ **Integration: FEATURE_DISCOVERY JobType Enum**

**File:** `/Users/danwilden/Developer/Medallion/backend/schemas/enums.py`
**Verdict:** PASS

- **FEATURE_DISCOVERY = "FEATURE_DISCOVERY"** defined in JobType enum
- Used in features.py POST /discover (line 52): `job_type=JobType.FEATURE_DISCOVERY`
- Type guard in GET /discover/{job_id} (line 91): check `job.get("job_type") != JobType.FEATURE_DISCOVERY.value`

**Enum Integration:** Correct and properly typed.

---

### ✅ **Integration: main.py Route Registration**

**File:** `/Users/danwilden/Developer/Medallion/apps/api/main.py`
**Verdict:** PASS

- **features_routes imported** (line 102)
- **Router included with /api/features prefix** (line 103): `app.include_router(features_routes.router, prefix="/api/features", tags=["features"])`
- Routes exposed as:
  - POST /api/features/discover
  - GET /api/features/discover/{job_id}
  - GET /api/features/library
  - GET /api/features/library/{name}

**Route Registration:** Clean and consistent.

---

## SUMMARY TABLE

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| Sandbox isolation | sandbox.py | ✅ PASS | multiprocessing.Process, import whitelist, timeout management |
| Feature compute | compute.py | ✅ PASS | Delegates to sandbox; line count 342 (acceptable for this unit) |
| Feature eval | evaluate.py | ✅ PASS | ANOVA, edge case handling, clean |
| Feature library | feature_library.py | ✅ PASS | REGISTRATION_THRESHOLD=2.0, strict > check, persistence |
| Tool executors | tools/feature.py | ✅ PASS | Family check first, caching, error handling |
| Feature researcher node | feature_researcher.py | ✅ PASS (warning: 518 lines but justified) | Bedrock loop, research_mode cleared, MAX_FEATURES_PER_SESSION=5 |
| Supervisor routing | supervisor.py | ✅ PASS | Priority 2 for discover_features, deterministic, no conflicts |
| Graph registration | graph.py | ✅ PASS | feature_researcher_node integrated, edges wired |
| API routes | features.py | ✅ PASS | 4 routes, job launch, polling, library query |
| Dependencies | deps.py | ✅ PASS | get_feature_library() defined and used |
| Enums | enums.py | ✅ PASS | FEATURE_DISCOVERY JobType present |
| Main registration | main.py | ✅ PASS | /api/features prefix, routes exposed |

---

## RECOMMENDATIONS FOR NEXT PHASE

1. **If compute.py exceeds 350 lines:** Consider moving FeatureComputer to a dedicated `backend/features/computer.py` module.

2. **If feature_researcher.py approaches 600 lines:** Extract tool dispatching to `backend/agents/_tool_dispatch.py` to reduce cognitive load.

3. **Performance monitoring:** Log feature discovery job duration and Bedrock throttling rates. Adjust BEDROCK_THROTTLE_BACKOFF_SECONDS if needed.

4. **Feature leakage audit:** Periodically review registered features to ensure leakage_risk self-assessment is accurate. Consider adding a post-registration audit tool.

5. **REGISTRATION_THRESHOLD tuning:** Monitor F-statistic distributions in evaluation runs. Current threshold of 2.0 may need adjustment based on feature discovery results.

---

## CONCLUSION

**Phase 5C Feature Discovery Agent is APPROVED for merge.**

- **Blockers:** 0
- **Warnings:** 2 (file sizes — justified, monitoring recommended)
- **Critical constraints:** All met
- **Security:** Excellent (subprocess isolation is real)
- **Integration:** Clean (LangGraph, API, dependencies all correct)
- **Code quality:** High (clear patterns, documentation, error handling)

The implementation follows established patterns from Phase 5B (strategy_researcher, backtest_diagnostics) and extends them correctly to feature discovery. Ready for production.

---

**Review signed off:** 2026-03-15 00:00 UTC
