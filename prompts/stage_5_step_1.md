You are the orchestrator for Phase 5 of a Forex trading platform. Your job is to 
coordinate a team of specialized subagents in parallel to design and implement the 
LangGraph-based supervisor agent layer on top of the existing FastAPI backend.

## MISSION
Implement Phase 5, Stage 1: Foundation — the supervisor graph skeleton, tool 
contracts, and typed state models that all future agent nodes will depend on.

## EXECUTION STRATEGY
Spawn the following agents in parallel where dependencies allow. Use results from 
reconnaissance agents as inputs to implementation agents. Do not write implementation 
code until recon is complete.

---

## STAGE 1: PARALLEL RECONNAISSANCE (spawn all three simultaneously)

### Agent 1 — repo-recon-agent
Task: Scan the entire repo and produce an evidence report covering:
- Actual file/folder structure vs any handoff docs or README assumptions
- Documentation drift and stale assumptions
- Missing files referenced in docs
- Naming mismatches between docs and actual code
- Any Phase 5 scaffolding that already exists
Output: structured report saved to docs/recon/repo-recon-report.md

### Agent 2 — api-jobs-contract-agent  
Task: Inspect the FastAPI backend and produce:
- Complete inventory of all real endpoints (method, path, request/response schema)
- Job contract details: how jobs are submitted, polled, and resolved
- Result artifact paths and how they're referenced
- Gaps in the contract (missing endpoints, untyped responses, inconsistent error shapes)
- Recommended minimal API toolset for the Phase 5 supervisor (≤10 tools)
- Suggested typed Python client models for each tool
Output: structured report saved to docs/recon/api-contract-report.md

### Agent 3 — phase5-agent-architect
Task: Using the repo structure and API contract (wait for Agents 1 & 2), design:
- Supervisor graph topology (nodes, edges, conditional routing)
- Full package layout for apps/agent/ or similar
- Typed LangGraph state model (AgentState TypedDict)
- Tool list with input/output contracts (Pydantic models)
- Which steps are deterministic vs LLM-driven
- Observability hooks (structured logging, trace IDs)
- Risk register and phased build order
- Explicit boundary: what Phase 5 owns vs backend vs frontend
Output: architecture doc saved to docs/architecture/phase5-architecture.md

---

## STAGE 2: PARALLEL IMPLEMENTATION (spawn after Stage 1 completes)

Use outputs from all three recon agents as your source of truth. Do not invent 
endpoints, schema shapes, or file paths — use only what was verified in Stage 1.

### Agent 4 — bedrock-implementer
Task: Implement the Bedrock/LangGraph integration layer:
- Model adapter that wraps Bedrock behind a clean interface
- LangGraph graph skeleton with supervisor node + placeholder tool nodes
- Typed AgentState as designed by phase5-agent-architect
- Structured logging on every node transition
- Unit tests with mocked Bedrock responses
Files: follow the package layout from phase5-architecture.md exactly

### Agent 5 — api-contract-engineer
Task: Harden the backend API contract for agent consumption:
- Add any missing endpoints identified in api-contract-report.md
- Ensure all job polling endpoints return consistent typed shapes
- Add/fix validation and error response shapes
- Ensure PROJECT_SPEC.md alignment
- Do NOT touch HMM internals, backtest internals, or UI
Files: apps/api/ routes and schemas only

---

## STAGE 3: REVIEW (spawn after Stage 2 completes)

### Agent 6 — code-reviewer
Task: Review all code written in Stage 2:
- Flag files over 300 lines
- Flag any duplication across the new agent layer and API layer
- Verify separation of concerns between supervisor, tools, and state
- Check naming consistency with the architecture doc
- Output a prioritized list of issues (blocker / warning / suggestion)
Output: docs/reviews/phase5-stage1-review.md

### Agent 7 — test-verification-engineer
Task: Write and run the critical test suite for Stage 2 output:
- Job polling state machine: submitted → running → complete/failed
- Tool input/output contract validation (Pydantic round-trips)
- Supervisor routing: verify correct node is selected for each input type
- Regression fixture for any bug found during review
- All tests must be deterministic with no real API calls (mock everything)
Output: tests/agent/ directory, pytest passing

---

## CONSTRAINTS FOR ALL AGENTS
1. Use only verified facts from recon reports — never assume
2. No agent touches another agent's ownership boundary
3. Every file created must have a module docstring explaining its role
4. All typed models use Pydantic v2
5. All graph state uses LangGraph TypedDict conventions
6. Log every LLM call with: model, input token count, output token count, latency
7. No secrets or credentials in code — use environment variables only
8. If you discover a conflict between the architecture doc and the actual repo, 
   STOP and report it before writing code

## SUCCESS CRITERIA FOR STAGE 1 COMPLETION
- [ ] repo-recon-report.md exists and has zero unverified assumptions
- [ ] api-contract-report.md lists all endpoints with typed schemas
- [ ] phase5-architecture.md has package layout, state model, and tool contracts
- [ ] Supervisor graph skeleton runs without error (even with all tools mocked)
- [ ] api-contract-engineer changes pass existing backend tests
- [ ] code-reviewer reports no blockers
- [ ] pytest passes in tests/agent/ with ≥80% coverage of new code