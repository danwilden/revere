import client from './client.js'

// ---------------------------------------------------------------------------
// Research routes — /api/research
// ---------------------------------------------------------------------------

/**
 * POST /api/research/run
 * Trigger a new agentic research run.
 * Returns 202 { experiment_id, session_id, status, created_at }
 *
 * @param {Object} payload
 * @param {string} payload.instrument
 * @param {string} payload.timeframe
 * @param {string} payload.test_start
 * @param {string} payload.test_end
 * @param {string} payload.task - "discover" | "mutate"
 * @param {string} [payload.requested_by]
 * @param {string} [payload.model_id]
 * @param {string} [payload.feature_run_id]
 * @param {string} [payload.parent_experiment_id]
 */
export const triggerResearchRun = (payload) =>
  client.post('/api/research/run', payload).then((r) => r.data)

/**
 * GET /api/research/runs
 * List research runs (lab ExperimentRecord[]).
 *
 * @param {Object} [params] - { limit?: number, instrument?: string }
 */
export const listResearchRuns = (params = {}) =>
  client.get('/api/research/runs', { params }).then((r) => r.data)

/**
 * GET /api/research/runs/{experiment_id}
 * Fetch a single lab ExperimentRecord by ID.
 *
 * @param {string} id
 */
export const getResearchRun = (id) =>
  client.get(`/api/research/runs/${id}`).then((r) => r.data)

// ---------------------------------------------------------------------------
// Experiment routes — /api/experiments  (re-exported from experiments.js)
// ---------------------------------------------------------------------------

export {
  listExperiments,
  getExperiment,
  updateExperimentStatus,
  promoteExperiment,
  getRobustnessStatus,
  approveExperiment,
  discardExperiment,
} from './experiments.js'

/**
 * POST /api/experiments
 * Create a new API-layer experiment record.
 *
 * @param {Object} payload
 * @param {string} payload.name
 * @param {string} [payload.description]
 * @param {string} payload.instrument
 * @param {string} payload.timeframe
 * @param {string} payload.test_start
 * @param {string} payload.test_end
 * @param {string} [payload.model_id]
 * @param {string} [payload.feature_run_id]
 * @param {string} [payload.requested_by]
 * @param {string[]} [payload.tags]
 */
export const createExperiment = (payload) =>
  client.post('/api/experiments', payload).then((r) => r.data)
