import client from './client.js'

/**
 * POST /api/automl/jobs
 * Launch a new AutoML signal mining job.
 * @param {Object} payload - { instrument_id, timeframe, feature_run_id, model_id?, target_type }
 * @returns {Promise<JobRun>}
 */
export const createAutoMLJob = (payload) =>
  client.post('/api/automl/jobs', payload).then((r) => r.data)

/**
 * GET /api/automl/jobs/{jobId}
 * Get full status response for an AutoML job including automl_record.
 * @param {string} jobId
 * @returns {Promise<AutoMLJobStatusResponse>}
 */
export const getAutoMLJob = (jobId) =>
  client.get(`/api/automl/jobs/${jobId}`).then((r) => r.data)

/**
 * GET /api/automl/jobs/{jobId}/candidates
 * Fetch candidate models for a completed AutoML job.
 * Returns 409 if job not yet completed.
 * @param {string} jobId
 * @returns {Promise<Array>}
 */
export const getAutoMLCandidates = (jobId) =>
  client.get(`/api/automl/jobs/${jobId}/candidates`).then((r) => r.data)

/**
 * POST /api/automl/jobs/{jobId}/convert?signal_name=
 * Convert an accepted AutoML job result into a Signal bank entry.
 * Returns 409 if job not completed or evaluation.accept !== true.
 * @param {string} jobId
 * @param {string} signalName - optional; auto-generated if blank
 * @returns {Promise<Signal>}
 */
export const convertToSignal = (jobId, signalName) =>
  client
    .post(`/api/automl/jobs/${jobId}/convert`, null, {
      params: signalName ? { signal_name: signalName } : {},
    })
    .then((r) => r.data)
