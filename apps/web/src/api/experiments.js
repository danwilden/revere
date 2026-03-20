import client from './client.js'

/**
 * GET /api/experiments
 * List all experiments.
 */
export const listExperiments = (params = {}) =>
  client.get('/api/experiments', { params }).then((r) => r.data)

/**
 * GET /api/experiments/{id}
 * @param {string} experimentId
 */
export const getExperiment = (experimentId) =>
  client.get(`/api/experiments/${experimentId}`).then((r) => r.data)

/**
 * PATCH /api/experiments/{id}/status
 * @param {string} experimentId
 * @param {Object} payload - { status: string }
 */
export const updateExperimentStatus = (experimentId, payload) =>
  client.patch(`/api/experiments/${experimentId}/status`, payload).then((r) => r.data)

/**
 * POST /api/experiments/{id}/promote
 * Trigger robustness battery for an experiment.
 * → 202 { job_id, status }
 * → 409 { detail } if already running, no backtest, etc.
 * @param {string} experimentId
 */
export const promoteExperiment = (experimentId) =>
  client.post(`/api/experiments/${experimentId}/promote`).then((r) => r.data)

/**
 * GET /api/experiments/{id}/robustness
 * Poll robustness battery status and result.
 * @param {string} experimentId
 */
export const getRobustnessStatus = (experimentId) =>
  client.get(`/api/experiments/${experimentId}/robustness`).then((r) => r.data)

/**
 * POST /api/experiments/{id}/approve
 * Approve a promoted experiment.
 * → 200 { experiment: {...} }
 * → 409 { detail } if not in promotable state
 * @param {string} experimentId
 */
export const approveExperiment = (experimentId) =>
  client.post(`/api/experiments/${experimentId}/approve`).then((r) => r.data)

/**
 * POST /api/experiments/{id}/discard
 * Discard a promoted experiment with a required reason.
 * → 200 { experiment: {...} }
 * → 409 { detail } if not in discardable state
 * @param {string} experimentId
 * @param {string} reason
 */
export const discardExperiment = (experimentId, reason) =>
  client.post(`/api/experiments/${experimentId}/discard`, { reason }).then((r) => r.data)
