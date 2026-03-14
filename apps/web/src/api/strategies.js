import client from './client.js'

/**
 * POST /api/strategies
 * Create a new strategy (rules or code-based).
 * @param {Object} payload - { name, strategy_type, definition_json, ... }
 */
export const createStrategy = (payload) =>
  client.post('/api/strategies', payload).then((r) => r.data)

/**
 * GET /api/strategies
 * List all strategies.
 */
export const listStrategies = () =>
  client.get('/api/strategies').then((r) => r.data)

/**
 * GET /api/strategies/{id}
 * @param {string} strategyId
 */
export const getStrategy = (strategyId) =>
  client.get(`/api/strategies/${strategyId}`).then((r) => r.data)

/**
 * POST /api/strategies/{id}/validate
 * Validate a strategy definition.
 * @param {string} strategyId
 */
export const validateStrategy = (strategyId) =>
  client.post(`/api/strategies/${strategyId}/validate`).then((r) => r.data)
