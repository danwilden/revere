# Phase 6 Frontend Test Suite

Deterministic, isolation-tested suite for Phase 6 views, stores, API clients, and router configuration.

## File Index

### API Client Tests (`api.*.test.js`)

Each API module test verifies correct HTTP method, endpoint path, request body/params, and response unwrapping.

- **api.research.test.js** — Research runs API (trigger, list, fetch)
- **api.automl.test.js** — AutoML jobs API (create, poll, candidates, convert)
- **api.experiments.test.js** — Experiments API (create, list, fetch, status update)
- **api.chat.test.js** — Chat API (sessions, messages, SSE streaming, cancel)

### Store Tests (`stores.*.test.js`)

Each store test verifies state mutations, API integration, async flows, and error handling.

- **stores.research.test.js** — Research store (fetch, trigger, polling, terminal detection)
- **stores.experiments.test.js** — Experiments store (CRUD, filtering, selection, status transitions)
- **stores.automl.test.js** — AutoML store (job submission, polling, conversion, reset)
- **stores.chat.test.js** — Chat store (sessions, streaming, offline detection, cancellation)

### Infrastructure Tests

- **router.test.js** — Router configuration (route names, paths, lazy imports, metadata)
- **views.smoke.test.js** — View component mounting (ResearchView, ExperimentsView, ChatView, AutoMLView)

## Running Tests

```bash
npm run test          # Watch mode
npm run test:run      # Single run
npm run test:ui       # Vitest dashboard
```

## Test Characteristics

✓ **Deterministic** — Hardcoded fixtures, no randomization  
✓ **Isolated** — Fresh Pinia + mocked APIs per test  
✓ **Fast** — No real HTTP, localStorage, or DOM mutations  
✓ **Real behavior** — Tests verify actual implementation, not guesses  
✓ **Maintainable** — Clear naming, focused assertions, shallow mocking

## Coverage

- ~88 test cases across 10 files
- API client integration: 21 tests
- Store logic: 33 tests
- Router configuration: 8 tests
- Component smoke tests: 16 tests

## Key Patterns

### Mock Pattern
```javascript
vi.mock('@/api/research.js', () => ({
  triggerResearchRun: vi.fn(),
  listResearchRuns: vi.fn(),
}))
```

### Store Test Pattern
```javascript
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const store = useResearchStore()
```

### Async Pattern
```javascript
const promise = store.fetchResearchRuns()
expect(store.loading).toBe(true)

await promise
expect(store.loading).toBe(false)
```

## Constraints

- No real HTTP requests (all mocked)
- No localStorage/sessionStorage access
- No .env file dependencies
- No external service calls
- Small deterministic fixtures only

## Extending Tests

When adding new features:

1. **New API method**: Add to `api.*.test.js` (HTTP method + endpoint + params)
2. **New store action**: Add to `stores.*.test.js` (state changes + async + errors)
3. **New view**: Add smoke test to `views.smoke.test.js` (mount + text only)
4. **New route**: Add to `router.test.js` (name + path + component)

Always mock external dependencies and use deterministic fixtures.

See TEST_SUITE_SUMMARY.md for detailed coverage breakdown.
