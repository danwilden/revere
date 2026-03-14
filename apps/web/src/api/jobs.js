import client from './client.js'

/**
 * GET /api/jobs/{id}
 * Universal job poller — works across all job types (ingestion, HMM, backtest).
 * @param {string} jobId
 */
export const getJob = (jobId) =>
  client.get(`/api/jobs/${jobId}`).then((r) => r.data)

/**
 * POST /api/jobs/{id}/cancel
 * Cancel a QUEUED or RUNNING job.
 * @param {string} jobId
 */
export const cancelJob = (jobId) =>
  client.post(`/api/jobs/${jobId}/cancel`).then((r) => r.data)
