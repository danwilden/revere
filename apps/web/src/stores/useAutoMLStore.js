import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createAutoMLJob,
  getAutoMLJob,
  getAutoMLCandidates,
  convertToSignal as apiConvertToSignal,
} from '@/api/automl.js'

export const useAutoMLStore = defineStore('automl', () => {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  /** List of all AutoML job status objects seen this session */
  const jobs = ref([])

  /** ID of the job currently being tracked */
  const activeJobId = ref(null)

  /**
   * Full AutoMLJobStatusResponse for the active job.
   * Shape: { job_run: { id, status, progress_pct, result_ref, error_message, created_at },
   *          automl_record: { id, job_id, instrument_id, timeframe, feature_run_id, model_id,
   *                           target_type, status, candidates?, evaluation?, signal_id?, created_at } }
   */
  const activeJobStatus = ref(null)

  /** Candidate model list — populated once automl_record.status === 'completed' */
  const candidates = ref([])

  /** Signal object returned by convert-to-signal */
  const convertedSignal = ref(null)

  /** True while the launch form POST is in flight */
  const isSubmitting = ref(false)

  /** True while fetching the candidates list */
  const isLoadingCandidates = ref(false)

  /** True while the convert-to-signal POST is in flight */
  const isConverting = ref(false)

  /** Error surfaced by submitJob or pollJob */
  const submitError = ref(null)

  /** General error (candidates fetch, convert, etc.) */
  const error = ref(null)

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /**
   * POST /api/automl/jobs — submit a new AutoML job.
   * Sets activeJobId on success and returns the job ID string.
   * @param {Object} payload - { instrument_id, timeframe, feature_run_id, model_id?, target_type }
   * @returns {Promise<string>} jobId
   */
  async function submitJob(payload) {
    isSubmitting.value = true
    submitError.value = null
    activeJobId.value = null
    activeJobStatus.value = null
    candidates.value = []
    convertedSignal.value = null

    try {
      const jobRun = await createAutoMLJob(payload)
      activeJobId.value = jobRun.id
      // Seed activeJobStatus with minimal shape so the status panel renders immediately
      activeJobStatus.value = {
        job_run: {
          id: jobRun.id,
          status: jobRun.status ?? 'queued',
          progress_pct: 0,
          result_ref: null,
          error_message: null,
          created_at: jobRun.created_at ?? null,
        },
        automl_record: null,
      }
      // Track in jobs list
      jobs.value = [jobRun, ...jobs.value]
      return jobRun.id
    } catch (err) {
      submitError.value =
        err?.normalized?.message ?? err?.message ?? 'Failed to launch AutoML job'
      throw err
    } finally {
      isSubmitting.value = false
    }
  }

  /**
   * GET /api/automl/jobs/{jobId} — refresh active job status.
   * Called by the poller on each tick (via onProgress).
   * Gracefully ignores transient poll errors (_pollError shape).
   * @param {string} jobId
   */
  async function pollJob(jobId) {
    try {
      const statusResponse = await getAutoMLJob(jobId)
      activeJobStatus.value = statusResponse
    } catch (err) {
      // Non-fatal — preserve last known state
      console.warn('[useAutoMLStore] pollJob error:', err?.message ?? err)
    }
  }

  /**
   * GET /api/automl/jobs/{jobId}/candidates
   * Fetch candidate list. Only valid after automl_record.status === 'completed'.
   * @param {string} jobId
   */
  async function fetchCandidates(jobId) {
    isLoadingCandidates.value = true
    error.value = null
    try {
      const data = await getAutoMLCandidates(jobId)
      candidates.value = Array.isArray(data) ? data : []
    } catch (err) {
      error.value =
        err?.normalized?.message ?? err?.message ?? 'Failed to fetch candidates'
      candidates.value = []
    } finally {
      isLoadingCandidates.value = false
    }
  }

  /**
   * POST /api/automl/jobs/{jobId}/convert?signal_name=
   * Convert accepted job to a Signal. Sets convertedSignal on success.
   * @param {string} jobId
   * @param {string} [signalName] - optional; auto-generated if falsy
   */
  async function convertJobToSignal(jobId, signalName) {
    isConverting.value = true
    error.value = null
    try {
      const signal = await apiConvertToSignal(jobId, signalName || undefined)
      convertedSignal.value = signal
      return signal
    } catch (err) {
      error.value =
        err?.normalized?.message ?? err?.message ?? 'Failed to convert job to signal'
      throw err
    } finally {
      isConverting.value = false
    }
  }

  /**
   * Reset all active-job state so the user can start a fresh job.
   */
  function resetJob() {
    activeJobId.value = null
    activeJobStatus.value = null
    candidates.value = []
    convertedSignal.value = null
    submitError.value = null
    error.value = null
  }

  // ---------------------------------------------------------------------------
  // Exports
  // ---------------------------------------------------------------------------

  return {
    // state
    jobs,
    activeJobId,
    activeJobStatus,
    candidates,
    convertedSignal,
    isSubmitting,
    isLoadingCandidates,
    isConverting,
    submitError,
    error,
    // actions
    submitJob,
    pollJob,
    fetchCandidates,
    convertJobToSignal,
    resetJob,
  }
})
