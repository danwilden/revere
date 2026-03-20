# Chat System Test Suite — Summary

## Overview

Critical test suite for the Medallion chat system covering:
- Chat session and message persistence
- HTTP API endpoints with SSE streaming
- Agent mode detection and node routing
- Multi-turn conversation flows and JSON extraction

**Total: 148 deterministic tests, all passing.**

---

## Test Files

### 1. `backend/tests/test_chat_repository.py` (29 tests)

File-backed persistence layer for chat sessions and messages.

**Test Categories:**
- Session creation: id, timestamps, message_count initialization
- Message persistence: user/assistant roles, tokens, actions, context
- Message retrieval: ordering (oldest first), field preservation
- Session listing: newest-first ordering, limit respect
- Concurrent access: thread-safe creation and message addition

**Key Assertions:**
- `ChatSession` has `id`, `created_at`, `updated_at`, `message_count`, `title`
- Messages preserve all fields when round-tripped through disk
- `list_sessions()` returns newest first (by `updated_at`)
- Concurrent writes don't corrupt state

**Critical Features Tested:**
- Threading lock guards all writes
- Session `message_count` increments atomically
- `updated_at` refreshes when messages are added

---

### 2. `backend/tests/test_chat_routes.py` (25 tests)

FastAPI HTTP endpoints and SSE streaming responses.

**Test Categories:**
- Session CRUD: 201 creation, 200 GET, correct response shape
- Message listing: 404 for unknown session, correct count/ordering
- SSE stream format: exact byte-for-byte validation of streaming protocol
- Error handling: 404s, validation, exception handling in stream

**Key Assertions:**

**Stream Format (parsed by frontend `apps/web/src/api/chat.js`):**
```
data: {"token": "word"}
data: {"token": " or"}
...
data: {"action": "run_strategy", "payload": {...}}
...
data: {"message_id": "uuid-123", "total_tokens": 42, "actions": [...]}
data: [DONE]
```

**Critical Validations:**
- Content-Type is `text/event-stream`
- Each event is `data: <json>\n\n` (exact format)
- Done event has `message_id` (string, non-empty)
- Done event has `total_tokens` (int)
- Done event has `actions` (list)
- Stream ends with `data: [DONE]\n\n`
- User message persisted before stream opens
- Assistant message persisted after streaming
- Context passed in request is stored
- Exceptions produce `{"error": "..."}` SSE event

---

### 3. `backend/tests/test_chat_agent.py` (38 tests)

Core agent logic: mode detection, confirmation parsing, node routing.

**Test Categories:**

**Mode Detection (12 tests):**
- Ideation: "build", "create", "strategy", "entry", indicator names
- Failure Analysis: failure keywords ("why", "failed", "drawdown") + context (experiment_id, backtest_id)
- Research: default fallback, no keywords
- Sticky: once set, mode persists unless overridden

**Confirmation Parsing (13 tests):**
- Confirm: "yes", "y", "run it", "ok", "okay", "sure", "yep", "yeah"
- Reject: "no", "not sure", "maybe", empty string
- Case-insensitive, whitespace-stripped

**Intake Node Routing (7 tests):**
- Active → ideation_node (ideation mode)
- Active → analysis_node (failure_analysis mode)
- Active → research_node (research mode)
- Awaiting_confirmation + "yes" → confirm_node
- Awaiting_confirmation + rejection → decline_node
- Stores detected_mode in state
- Preserves message_context through state

**Placeholder Tests (6 tests):**
- Confirm/decline node behavior (documentation of expected behavior)
- invoke_chat_agent signature (return tuple of 4 elements)

---

### 4. `backend/tests/test_chat_agent_flows.py` (29 tests)

Multi-turn flows and strategy JSON extraction (marker-based).

**Test Categories:**

**Strategy JSON Extraction (7 tests):**
- Valid JSON between markers `===STRATEGY_JSON_START===` and `===STRATEGY_JSON_END===`
- Returns None: no markers, invalid JSON, whitespace handling
- Multiline JSON support

**Ideation Flow:**
- Mode detection → ideation_node call
- LLM response with strategy JSON is extractable
- Confirmation needed after proposal

**Failure Analysis Flow:**
- Mode detection with experiment_id or backtest_id context
- Tool call to get_backtest_result
- Mutation proposal extraction

**Confirmation/Cancellation Flow (documentation):**
- Pending action cleared after confirmation
- Job ID in reply text
- Action list in response

**Mode Stickiness (3 tests):**
- Ideation persists across turns
- Research persists across turns
- Failure analysis persists in context

**Message Context Propagation (2 tests):**
- Context available to read tools
- Updated each turn

**Error Handling (4 tests):**
- Bedrock throttle retry logic
- Tool call error handling
- Malformed JSON ignored
- Max tool calls limit enforced

---

## Coverage Matrix

| Module | Layer | Test File | Tests |
|--------|-------|-----------|-------|
| `chat_repository.py` | Data | `test_chat_repository.py` | 29 |
| `routes/chat.py` | HTTP | `test_chat_routes.py` | 25 |
| `chat_agent.py` (helpers) | Agent | `test_chat_agent.py` | 38 |
| `chat_agent.py` (flows) | Agent | `test_chat_agent_flows.py` | 29 |
| **Total** | | | **148** |

---

## Critical Test Decisions

### 1. SSE Stream Validation
The frontend parser (`apps/web/src/api/chat.js`) splits on `\n\n` and expects:
- Lines starting with `data: ` prefix
- JSON payload after prefix
- `[DONE]` sentinel at end

Tests validate exact byte format to catch serialization bugs early.

### 2. Mode Persistence
Keyword-based detection can flip modes mid-conversation unless sticky.
Tests verify that once set, mode persists until explicitly changed.
This prevents "why did my strategy fail" from switching to research mid-analysis.

### 3. Marker-Based Strategy Extraction
LLM outputs are unpredictable, so extraction uses explicit markers:
```
===STRATEGY_JSON_START===
{valid JSON}
===STRATEGY_JSON_END===
```

Tests verify graceful degradation (None return) on malformed output.

### 4. Concurrent Access
ChatRepository uses threading.Lock to guard writes.
Tests verify that simultaneous session creation and message addition don't corrupt state.

### 5. Intake Node Routing
The intake_node is deterministic (no LLM) and pure — it only returns the fields it updates.
Tests verify correct next_node selection for all mode/stage combinations.

---

## Known Limitations & Future Work

**Not Tested (documented as placeholders):**
- Full confirm_node execution (would require mocking execute_proposed_action)
- Full decline_node execution (would require mocking)
- Full invoke_chat_agent execution (would require mocking Bedrock + graph)
- Tool call execution (get_experiment, get_backtest_result, etc.)

These require LLM/HTTP mocking and are integration-level — prefer end-to-end tests once system is stable.

**Test Assumptions:**
- ChatRepository stores sessions/messages on disk (mocked in route tests)
- All agent state transitions are idempotent
- SSE parsing behavior matches frontend exactly
- Mode keywords are English-only (lowercase matching)

---

## Running the Tests

```bash
# All chat tests
.venv/bin/python -m pytest backend/tests/test_chat_*.py -v

# Single test file
.venv/bin/python -m pytest backend/tests/test_chat_repository.py -v

# Specific test class
.venv/bin/python -m pytest backend/tests/test_chat_routes.py::TestSendMessage -v

# With coverage
.venv/bin/python -m pytest backend/tests/test_chat_*.py --cov=backend.agents --cov=backend.data --cov=apps.api.routes.chat
```

---

## Key Files

- Test implementation: `/Users/danwilden/Developer/Medallion/backend/tests/test_chat_*.py`
- ChatRepository: `/Users/danwilden/Developer/Medallion/backend/data/chat_repository.py`
- Routes: `/Users/danwilden/Developer/Medallion/apps/api/routes/chat.py`
- Agent: `/Users/danwilden/Developer/Medallion/backend/agents/chat_agent.py`
- Frontend SSE parser: `/Users/danwilden/Developer/Medallion/apps/web/src/api/chat.js`
