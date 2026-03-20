# Repo Reconnaissance Report
# Medallion Platform — Phase 5 Pre-Implementation Scan
# Generated: 2026-03-15

---

## CRITICAL FLAG — Dependency Conflict (MUST RESOLVE BEFORE STAGE 2)

**Conflict:** `requirements.txt` lists `strands-agents>=0.1.0` (AWS Strands SDK) but the
orchestration prompt, `prompts/stage_5_step_1.md`, and `02_platform_plan.md` §11.7.1 all
specify **LangGraph** (`StateGraph`) as the agent orchestration framework.

These are incompatible choices — pick one before implementation begins:

| Option | Dep to add | Plan alignment |
|---|---|---|
| **LangGraph** (as per platform plan + prompt) | `langgraph>=0.2`, `langchain-aws` | Full alignment with §11.7 |
| **AWS Strands** (as per requirements.txt + memory) | keep `strands-agents>=0.1.0` | Aligns with memory note but requires plan rewrite |

**Memory note:** `~/.claude/projects/.../memory/MEMORY.md` says "AWS Strands SDK for agents, boto3 + Bedrock for LLM" — recorded from a prior conversation.

**Recommendation:** Orchestrator must decide and update `requirements.txt` before Stage 2 spawns.

---

## 1. Actual File/Folder Structure

Verified via `find` on 2026-03-15. All paths confirmed on disk.

```
/Users/danwilden/Developer/Medallion/
├── apps/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                          ✅ FastAPI entry point
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── backtests.py                 ✅ Phase 4
│   │       ├── dukascopy.py                 ✅ Phase 1
│   │       ├── ingestion.py                 ✅ Phase 1
│   │       ├── instruments.py               ✅ Phase 1
│   │       ├── jobs.py                      ✅ Phase 4 (universal poller)
│   │       ├── market_data.py               ✅ Phase 1
│   │       ├── models.py                    ✅ Phase 2
│   │       ├── signals.py                   ✅ Phase 2
│   │       └── strategies.py                ✅ Phase 3
│   ├── web/                                 ✅ Vue 3 frontend (Phase 6 core built)
│   │   └── src/
│   │       ├── api/          (10 client files)
│   │       ├── components/   (feature components + ui primitives)
│   │       ├── composables/  (useDataRanges, useInstruments, useJobPoller)
│   │       ├── router/index.js
│   │       ├── stores/       (7 Pinia stores)
│   │       └── views/        (6 views — see §12.1 notes)
│   └── worker/
│       └── __init__.py                      ⚠️  EMPTY — purpose unclear
├── backend/
│   ├── agents/                              ⚠️  EXISTS but EMPTY SCAFFOLDING ONLY
│   │   ├── __init__.py                      (empty)
│   │   ├── providers/
│   │   │   └── __init__.py                  (empty)
│   │   └── tools/
│   │       └── __init__.py                  (empty)
│   ├── backtest/   (costs, data_loader, engine, fills, metrics)
│   ├── config.py
│   ├── connectors/ (dukascopy, instruments, oanda)
│   ├── data/       (aggregate, duckdb_store, local_artifacts, local_metadata,
│   │               normalize, quality, repositories)
│   ├── deps.py
│   ├── features/   (compute.py)
│   ├── jobs/       (backtest, hmm, ingestion, status)
│   ├── models/     (hmm_regime, labeling)
│   ├── schemas/    (enums, models, requests)
│   ├── signals/    (bank, materialize)
│   └── strategies/ (base, code_strategy, rules_engine, rules_strategy,
│                   sandbox, state, validation)
├── data/
│   ├── raw/        (parquet files — legacy era, not used by new backend)
│   └── reports/    (CSV/JSON walk-forward results — legacy era)
├── backend/data/
│   ├── artifacts/  (backtests/{run_id}/equity.json, models/hmm/*.joblib)
│   └── market.duckdb
├── infra/
│   ├── bin/        ⚠️  EXISTS but EMPTY
│   └── lib/        ⚠️  EXISTS but EMPTY
├── legacy/         (sequestered — do not use)
├── prompts/
│   ├── planning_phase_5.md
│   └── stage_5_step_1.md                   (this orchestration prompt)
├── scripts/
│   ├── diagnose_backtest.py
│   └── migrate_db.py
├── pyproject.toml
├── requirements.txt
└── README.md
```

**Directories mentioned in docs that DO NOT EXIST on disk:**

| Doc reference | Status |
|---|---|
| `backend/agents/supervisor.py` | Missing (dir exists, file absent) |
| `backend/agents/strategy_researcher.py` | Missing |
| `backend/agents/backtest_diagnostics.py` | Missing |
| `backend/agents/robustness_reviewer.py` | Missing |
| `backend/agents/experiment_librarian.py` | Missing |
| `backend/agents/generation_comparator.py` | Missing |
| `backend/agents/feature_researcher.py` | Missing |
| `backend/agents/model_researcher.py` | Missing |
| `backend/agents/tools/` | Dir exists, no .py files |
| `backend/agents/providers/` | Dir exists, no .py files |
| `backend/lab/experiment_registry.py` | Dir missing entirely |
| `backend/lab/evaluation.py` | Dir missing entirely |
| `backend/lab/mutation.py` | Dir missing entirely |
| `backend/automl/dataset_builder.py` | Dir missing entirely |
| `backend/automl/sagemaker_runner.py` | Dir missing entirely |
| `apps/api/routes/experiments.py` | Missing |
| `apps/api/routes/research.py` | Missing |
| `apps/api/routes/chat.py` | Missing |
| `apps/api/routes/automl.py` | Missing |
| `docs/` (entire dir) | Created during this recon pass |

---

## 2. Documentation vs Reality Drift

### Phase 6 Frontend — Drift
Plan §12 says these views are NOT YET BUILT:
- `ResearchView.vue`, `ExperimentsView.vue`, `ChatView.vue`, `AutoMLView.vue`

**Verified:** `apps/web/src/views/` contains exactly 6 files (Backtest, Coverage, Data, Models,
Results, Strategies). No research/chat/automl views. ✅ Plan is accurate.

Router `apps/web/src/router/index.js` has 6 routes — no Phase 5/6 routes yet.

### Phase 5 — Drift
Plan §11.7.4 lists backend module layout that is almost entirely absent (see table above).
Only the empty scaffolding (`backend/agents/__init__.py` + subdirs) exists.

### data/ Layout — Drift
Plan §2 and §3 say:
- Metadata JSON files: `data/metadata/` → **Actual:** `backend/data/` (not `data/metadata/`)
- Artifacts: `data/artifacts/` → **Actual:** `backend/data/artifacts/`
- DuckDB: `data/market.duckdb` → **Actual:** `backend/data/market.duckdb`

The plan consistently uses `data/` as the root but all runtime data lives under `backend/data/`.
This is a consistent naming mismatch — not a problem for Phase 5, just document it.

### `apps/worker/` — Undocumented
`apps/worker/__init__.py` exists and is not referenced anywhere in docs. Likely a stub for
future async worker processes. Do not use it in Phase 5 without clarifying its purpose.

---

## 3. Phase 5 Scaffolding Already Existing

**Verified via Glob and file reads:**

| Path | Status | Content |
|---|---|---|
| `backend/agents/__init__.py` | Exists | Empty (1 line) |
| `backend/agents/tools/__init__.py` | Exists | Empty (1 line) |
| `backend/agents/providers/__init__.py` | Exists | Empty (1 line) |

No other Phase 5 code exists. No LangGraph, boto3 agent code, or Strands code found anywhere
in `backend/` or `apps/`.

Grep confirmed: zero references to `langgraph`, `StateGraph`, `strands`, `supervisor_node`,
`AgentState` anywhere in the Python codebase.

---

## 4. Naming Mismatches

| Doc says | Reality |
|---|---|
| `data/metadata/` | `backend/data/` (LocalMetadataRepository writes here) |
| `data/artifacts/` | `backend/data/artifacts/` |
| `data/market.duckdb` | `backend/data/market.duckdb` |
| `backend/data/repositories.py` (listed in plan) | File split: `repositories.py` + `local_metadata.py` + `local_artifacts.py` |
| Plan §3 calls it `LocalMetadataRepository` in `repositories.py` | Actual: separate `backend/data/local_metadata.py` |

---

## 5. Existing Test Structure

**Path:** `backend/tests/` (10 files, no subdirectories)

```
backend/tests/
├── __init__.py
├── test_aggregate.py
├── test_backtest_integration.py
├── test_backtest.py
├── test_features.py
├── test_hmm.py
├── test_normalize.py
├── test_phase0_foundation.py
├── test_rules_engine.py
└── test_strategy.py
```

**Pytest config** (`pyproject.toml`):
```toml
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
```

**Gap:** No `tests/agent/` directory. Phase 5 tests must be added here or as
`backend/tests/test_agents*.py` files (consistent with current layout).

---

## 6. Existing Dependencies

**`requirements.txt` (installed):**

| Package | Version pin | Phase 5 relevance |
|---|---|---|
| fastapi | >=0.111 | ✅ already present |
| pydantic | >=2.7 | ✅ already present |
| boto3 | >=1.34 | ✅ already present — Bedrock calls |
| strands-agents | >=0.1.0 | ⚠️ **CONFLICTS with LangGraph plan** |
| loguru | >=0.7.2 | ✅ structured logging ready |
| pytest-asyncio | >=0.23 | ✅ async test support |
| hmmlearn, lightgbm, xgboost | present | not Phase 5 |

**`pyproject.toml`:** No `[project.dependencies]` table — all deps are in `requirements.txt`.

**Missing for LangGraph path:**
```
langgraph>=0.2.0
langchain-aws>=0.1.0   # or use boto3 directly
```

**Missing for Strands path:**
```
# strands-agents already present
# may need strands-tools or specific Strands modules
```

---

## 7. API Routes Inventory

Registered in `apps/api/main.py`:

| Prefix | File | Phase |
|---|---|---|
| `/api/ingestion` | `routes/ingestion.py` | 1 |
| `/api/instruments` | `routes/instruments.py` | 1 |
| `/api/market-data` | `routes/market_data.py` | 1 |
| `/api/models` | `routes/models.py` | 2 |
| `/api/signals` | `routes/signals.py` | 2 |
| `/api/strategies` | `routes/strategies.py` | 3 |
| `/api/backtests` | `routes/backtests.py` | 4 |
| `/api/jobs` | `routes/jobs.py` | 4 |
| `/api/dukascopy` | `routes/dukascopy.py` | 1 |
| `/health` | `main.py` inline | 0 |

**Phase 5 routes not yet registered:** `/api/experiments`, `/api/research`, `/api/chat`, `/api/automl`

---

## Summary

| Area | Status |
|---|---|
| Phase 0–4 backend | COMPLETE and confirmed |
| Phase 5 agent skeleton | EXISTS but empty |
| Phase 5 lab/automl modules | DO NOT EXIST |
| Phase 6 frontend (core) | COMPLETE |
| Phase 6 Phase 5 views | DO NOT EXIST |
| Phase 7 infra | EMPTY stubs only |
| **CRITICAL: LangGraph vs Strands conflict** | **MUST RESOLVE BEFORE STAGE 2** |
