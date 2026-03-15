import client from './client.js'

/**
 * POST /api/dukascopy/jobs
 * Start a Dukascopy download + ingest job.
 * @param {{ instruments: string[], start_date: string, end_date: string }} payload
 */
export const startDukascopyDownloadJob = (payload) =>
  client.post('/api/dukascopy/jobs', payload).then((r) => r.data)

/**
 * GET /api/dukascopy/jobs/{id}
 */
export const getDukascopyJob = (jobId) =>
  client.get(`/api/dukascopy/jobs/${jobId}`).then((r) => r.data)

/**
 * GET /api/dukascopy/jobs
 */
export const listDukascopyJobs = () =>
  client.get('/api/dukascopy/jobs').then((r) => r.data)
