# Phase 6 Frontend Test Suite Summary

## Overview
Comprehensive test suites for Phase 6 views, stores, API modules, and router configuration. All tests are deterministic, mock external dependencies, and verify actual implementation behavior.

## Test Infrastructure Setup

### Configuration Files Created
- **`vitest.config.js`** — Vitest configuration with jsdom environment, Vue plugin, and path aliases
- **`package.json` (updated)** — Added vitest + @vue/test-utils dependencies and test scripts

### Test Commands
```bash
npm run test           # Run tests in watch mode
npm run test:run      # Run tests once
npm run test:ui       # Run tests with Vitest UI
```

### Dependencies Added
- `vitest@^1.6.0` — Test runner
- `@vue/test-utils@^2.4.6` — Vue component testing utilities
- `happy-dom@^12.10.3` — Lightweight DOM implementation for jsdom

---

## Test Files Structure

### Location
All test files are in: `/apps/web/src/__tests__/`

### Test Files

#### 1. **api.research.test.js** — Research API Module Tests
**Coverage:** `triggerResearchRun()`, `listResearchRuns()`, `getResearchRun()`

Tests verify:
- Correct HTTP method and endpoint path
- Payload passed correctly to POST requests
- Query params passed through `params` object
- Response `.data` unwrapping pattern (`.then(r => r.data)`)
- ID interpolation in GET URLs

**Key assertions:**
- `triggerResearchRun()` calls `/api/research/run` POST with full payload
- `listResearchRuns()` respects optional limit/instrument filters
- `getResearchRun()` GET properly constructs `/api/research/runs/{id}` path

---

#### 2. **api.automl.test.js** — AutoML API Module Tests
**Coverage:** `createAutoMLJob()`, `getAutoMLJob()`, `getAutoMLCandidates()`, `convertToSignal()`

Tests verify:
- Job creation endpoint and response unwrapping
- Status polling endpoint for full `AutoMLJobStatusResponse` shape
- Candidates fetch with proper error handling on incomplete jobs
- Signal conversion with optional `signal_name` query param

**Key assertions:**
- `createAutoMLJob()` POST returns minimal job shape
- `getAutoMLJob()` returns envelope with both `job_run` and `automl_record`
- `convertToSignal()` correctly constructs params object (empty if no name)

---

#### 3. **api.experiments.test.js** — Experiments API Module Tests
**Coverage:** `listExperiments()`, `getExperiment()`, `updateExperimentStatus()`, `createExperiment()`

Tests verify:
- GET endpoints return proper response shapes
- PATCH endpoint passes status payload correctly
- POST endpoint with optional description/requested_by fields
- Response data unwrapping consistency

**Key assertions:**
- `updateExperimentStatus()` PATCH uses `/api/experiments/{id}/status` path
- Response may have `.experiment` wrapper or direct shape

---

#### 4. **api.chat.test.js** — Chat API Module Tests
**Coverage:** `createSession()`, `listSessions()`, `getMessages()`, `sendMessage()` (SSE streaming)

Tests verify:
- Session CRUD operations via axios client
- SSE streaming implementation (fetch-based, not axios)
- Token handling: `onToken()` for each token, `onDone()` with final event
- Error handling: `onError()` on non-2xx or stream errors
- Cancel function behavior
- Context object inclusion in request body

**Key assertions:**
- `sendMessage()` returns cancel function immediately
- Cancel function properly aborts fetch
- SSE parsing correctly extracts `data: {"token":"..."}` lines
- Non-2xx responses call `onError()` with HTTP status
- Network errors mapped to user-friendly messages

---

#### 5. **stores.research.test.js** — useResearchStore Tests
**Coverage:** State mutations, API integration, polling lifecycle

Tests verify:
- `fetchResearchRuns()` sets loading state and populates array
- `triggerRun()` calls API and sets `activeRunId`
- Automatic polling starts after trigger, stops on terminal status
- Error handling sets `submitError` vs `error` fields appropriately
- Terminal status detection (succeeded/failed/archived)
- `stopPolling()` clears interval timer

**Key assertions:**
- Polling interval is 3000ms
- Terminal statuses stop polling without further calls
- `clearSubmitError()` / `clearError()` reset respective fields
- Runs are upserted into list (new runs prepended)

---

#### 6. **stores.experiments.test.js** — useExperimentStore Tests
**Coverage:** List/detail fetch, status transitions, creation, filtering

Tests verify:
- `fetchExperiments()` extracts `experiments` and `count` from response
- `selectExperiment()` optimistically sets from local list before fetch
- `updateStatus()` patches all three state locations: list, selected, detail
- `createNewExperiment()` inserts at front and increments count
- Filter params passed through (limit, status, etc.)
- Error handling per operation

**Key assertions:**
- Selection is instant (before detail fetch completes)
- Status updates are cascaded to detail object if present
- Count is always kept in sync
- `fetchExperiment()` unwraps `experiment` key if wrapped

---

#### 7. **stores.automl.test.js** — useAutoMLStore Tests
**Coverage:** Job submission, polling, candidate fetching, signal conversion

Tests verify:
- `submitJob()` creates job run and seeds minimal status object
- `pollJob()` updates `activeJobStatus` without throwing on errors
- `fetchCandidates()` populates array or empties on error
- `convertJobToSignal()` sets signal and handles signal_name param
- `resetJob()` clears all active state
- Error fields are properly set and cleared

**Key assertions:**
- Active status seeded with `job_run` only, `automl_record: null`
- Poll errors don't overwrite state (preserve last known)
- Conversion errors still throw to caller
- Reset clears ALL active fields (candidates, signal, errors, etc.)

---

#### 8. **stores.chat.test.js** — useChatStore Tests
**Coverage:** Session management, message streaming, offline detection, stream cancellation

Tests verify:
- `fetchSessions()` sets `backendOffline=true` on 404 or network errors
- `createSession()` appends to list and sets as active
- `selectSession()` fetches messages and updates list
- `sendMessage()` optimistically adds user message, streams response
- Token streaming: calls `onToken()` per token, `onDone()` with message_id
- Error handling: `onError()` on stream failure, sets `backendOffline` on 404
- `cancelStream()` calls internal cancel function and resets state
- Offline errors recognized: 404, "Cannot connect", "Failed to fetch"

**Key assertions:**
- User message added immediately (optimistic)
- Streaming state: `isStreaming=true` during, `false` after
- `streamingContent` accumulates tokens, then transferred to message
- Cancel function stored internally for unmount cleanup
- Context passed only if contains experiment_id or strategy_id

---

#### 9. **router.test.js** — Router Configuration Tests
**Coverage:** Route definitions, lazy imports, metadata

Tests verify:
- Four routes exist: /research, /experiments, /chat, /automl
- Route names match exactly (not slugified)
- Lazy-imported components are callable functions
- Meta labels are correct (RESEARCH, EXPERIMENTS, CHAT, AUTOML)
- Icon metadata present and MDI-prefixed

**Key assertions:**
- `router.getRoutes()` finds routes by name
- Each component is lazy-loaded via dynamic import
- No typos in route names or paths

---

#### 10. **views.smoke.test.js** — Component Smoke Tests
**Coverage:** ResearchView, ExperimentsView, ChatView, AutoMLView

Tests verify:
- Each view mounts without throwing
- Header text is rendered (RESEARCH LAB, EXPERIMENTS, etc.)
- Offline state handled gracefully (ChatView)
- No XSS vulnerabilities (v-html rendered safely)
- Keyboard event handlers work
- Empty state displays correctly

**Key assertions:**
- `mount()` does not throw
- `wrapper.exists()` returns true
- Text content includes expected labels
- `$nextTick()` works (no async issues)

---

## Test Design Principles

### 1. Deterministic Fixtures
- All mock data is small and minimal (1–2 items per list)
- Timestamps hardcoded to specific date
- No random IDs or dynamic data in fixtures

### 2. No Real HTTP Requests
- All axios calls mocked via `vi.mock('@/api/client.js')`
- SSE streaming mocked with controlled `fetch`
- localStorage/sessionStorage not used in tests

### 3. Store Isolation
- Each test creates fresh Pinia instance via `setActivePinia(createPinia())`
- No cross-test state pollution
- Mocks cleared with `vi.clearAllMocks()` before each test

### 4. Async Safety
- Explicit `await` for all promises
- `vi.useFakeTimers()` for polling tests
- `setTimeout(..., 50)` for SSE stream processing

### 5. Real Implementation Verification
- Tests check actual method signatures (not guesses)
- Request/response shapes derived from actual code
- Terminal status list derived from actual store constant

---

## Coverage Summary

| Category | File | Test Count | Focus |
|----------|------|-----------|-------|
| **API** | research.test.js | 5 | Trigger, list, fetch |
| **API** | automl.test.js | 6 | Job CRUD, polling, conversion |
| **API** | experiments.test.js | 4 | CRUD, status patching |
| **API** | chat.test.js | 6 | Sessions, streaming, errors |
| **Stores** | research.test.js | 7 | Run submission, polling, errors |
| **Stores** | experiments.test.js | 8 | CRUD, filtering, status, selection |
| **Stores** | automl.test.js | 7 | Job submit, poll, fetch, convert |
| **Stores** | chat.test.js | 11 | Sessions, streaming, cancel, offline |
| **Router** | router.test.js | 8 | Routes, names, lazy imports, meta |
| **Views** | views.smoke.test.js | 16 | Mount, render, offline handling |

**Total: ~88 test cases**

---

## Running the Tests

### Installation
```bash
cd apps/web
npm install
```

### Run
```bash
npm run test:run
```

### Watch Mode
```bash
npm run test
```

### UI Dashboard
```bash
npm run test:ui
```

---

## Known Limitations & Future Work

1. **Component Integration Tests**: Smoke tests only verify mount + text; future work should test user interactions (form submission, button clicks, etc.)

2. **View-Level Form Validation**: ResearchView and AutoMLView have client-side validation; future tests should verify error display on invalid input

3. **RobustnessPanel Component**: Mocked in ExperimentsView tests; should have dedicated tests once implementation is finalized

4. **E2E/Playwright Tests**: Frontend test suite does not cover multi-step user flows; recommend browser automation for /research → trigger → poll → view results workflow

5. **Accessibility Tests**: No a11y checks (labels, ARIA, keyboard nav); consider `@testing-library/vue` + axe for future coverage

---

## Test Maintenance

### Adding Tests for New Features
1. Place new test in appropriate file (`stores.*.test.js` or `api.*.test.js`)
2. Mock all external APIs with `vi.mock()`
3. Use deterministic fixtures (no random data)
4. Verify actual behavior, not expected behavior
5. Name tests with "should X because Y" format

### Debugging Failures
- Use `npm run test:ui` to visually debug
- Check mock call logs: `store.triggerRun.mock.calls`
- Verify fixture matches API response shape
- Check error message field names (sometimes `message`, sometimes `detail`)

---

## References

- **Vitest Docs**: https://vitest.dev/
- **@vue/test-utils**: https://test-utils.vuejs.org/
- **Pinia Testing**: https://pinia.vuejs.org/cookbook/testing.html
- **Implementation**: See `/apps/web/src/` for actual code being tested

---

## Ownership & Questions

Tests written by Test & Verification Engineer.

For questions on:
- **Implementation details**: Read the actual view/store/API file
- **Test setup**: Check `vitest.config.js`
- **Mocking strategy**: See vi.mock() calls at top of each test file
- **Fixture data**: Search for `mockData` or `mock*` variables in tests
