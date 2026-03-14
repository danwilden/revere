import client from './client.js'

/**
 * POST /api/ingestion/jobs
 * Start a new data ingestion job.
 * @param {Object} payload - { instruments, source, start_date, end_date }
 */
export const startIngestionJob = (payload) =>
  client.post('/api/ingestion/jobs', payload).then((r) => r.data)

/**
 * GET /api/ingestion/jobs/{id}
 * @param {string} jobId
 */
export const getIngestionJob = (jobId) =>
  client.get(`/api/ingestion/jobs/${jobId}`).then((r) => r.data)

/**
 * GET /api/ingestion/jobs
 * List all ingestion jobs.
 */
export const listIngestionJobs = () =>
  client.get('/api/ingestion/jobs').then((r) => r.data)
