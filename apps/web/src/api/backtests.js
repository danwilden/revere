import client from './client.js'

/**
 * POST /api/backtests/jobs
 * Start a new backtest job (202 async).
 * @param {Object} payload - { strategy_id, instrument_id, timeframe, start_date, end_date, pip_size, ... }
 *
 * IMPORTANT: pip_size must match the instrument.
 *   JPY pairs (e.g. USD_JPY): 0.01
 *   All others: 0.0001
 */
export const startBacktestJob = (payload) =>
  client.post('/api/backtests/jobs', payload).then((r) => r.data)

/**
 * GET /api/backtests/jobs/{id}
 * @param {string} jobId
 */
export const getBacktestJob = (jobId) =>
  client.get(`/api/backtests/jobs/${jobId}`).then((r) => r.data)

/**
 * GET /api/backtests/runs
 * List all completed backtest runs.
 */
export const listBacktestRuns = () =>
  client.get('/api/backtests/runs').then((r) => r.data)

/**
 * GET /api/backtests/runs/{id}
 * @param {string} runId
 */
export const getBacktestRun = (runId) =>
  client.get(`/api/backtests/runs/${runId}`).then((r) => r.data)

/**
 * GET /api/backtests/runs/{id}/trades
 * @param {string} runId
 */
export const getBacktestTrades = (runId) =>
  client.get(`/api/backtests/runs/${runId}/trades`).then((r) => r.data)

/**
 * GET /api/backtests/runs/{id}/equity
 * @param {string} runId
 */
export const getBacktestEquity = (runId) =>
  client.get(`/api/backtests/runs/${runId}/equity`).then((r) => r.data)
