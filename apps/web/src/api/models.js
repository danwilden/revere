import client from './client.js'

/**
 * POST /api/models/hmm/jobs
 * Start a new HMM training job (202 async).
 * @param {Object} payload - { instrument_id, timeframe, start_date, end_date, n_states, ... }
 */
export const startHmmTrainingJob = (payload) =>
  client.post('/api/models/hmm/jobs', payload).then((r) => r.data)

/**
 * GET /api/models/hmm/jobs/{id}
 * @param {string} jobId
 */
export const getHmmTrainingJob = (jobId) =>
  client.get(`/api/models/hmm/jobs/${jobId}`).then((r) => r.data)

/**
 * GET /api/models/hmm
 * List all HMM models.
 */
export const listHmmModels = () =>
  client.get('/api/models/hmm').then((r) => r.data)

/**
 * GET /api/models/hmm/{id}
 * @param {string} modelId
 */
export const getHmmModel = (modelId) =>
  client.get(`/api/models/hmm/${modelId}`).then((r) => r.data)

/**
 * POST /api/models/hmm/{id}/label
 * Apply semantic labels to HMM states.
 * @param {string} modelId
 * @param {Object} payload - { label_map: { "0": "TREND_BULL_LOW_VOL", ... } }
 */
export const applyHmmLabels = (modelId, payload) =>
  client.post(`/api/models/hmm/${modelId}/label`, payload).then((r) => r.data)
