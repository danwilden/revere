import client from './client.js'

/**
 * POST /api/signals
 * Create a new signal from an HMM model.
 * @param {Object} payload - { name, model_id, feature_run_id, ... }
 */
export const createSignal = (payload) =>
  client.post('/api/signals', payload).then((r) => r.data)

/**
 * GET /api/signals
 * List all signals.
 */
export const listSignals = () =>
  client.get('/api/signals').then((r) => r.data)

/**
 * GET /api/signals/{id}
 * @param {string} signalId
 */
export const getSignal = (signalId) =>
  client.get(`/api/signals/${signalId}`).then((r) => r.data)

/**
 * POST /api/signals/{id}/materialize
 * Materialize a signal over a date range.
 * @param {string} signalId
 * @param {Object} payload - { instrument_id, timeframe, start_date, end_date }
 */
export const materializeSignal = (signalId, payload) =>
  client.post(`/api/signals/${signalId}/materialize`, payload).then((r) => r.data)
