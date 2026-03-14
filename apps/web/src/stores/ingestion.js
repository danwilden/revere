import { defineStore } from 'pinia'
import { ref } from 'vue'
import { startIngestionJob } from '@/api/ingestion.js'

/**
 * useIngestionStore
 *
 * Owns all state for the data ingestion workflow:
 *   activeJob      — current job object or null
 *   activeJobId    — string job ID set after successful POST
 *   isSubmitting   — true while POST is in-flight
 *   submitError    — error message from failed submission
 *
 * The store does NOT manage instruments — that is handled by useInstruments()
 * composable in the view layer to avoid duplicating fetch logic across stores.
 *
 * Job polling is initiated in the view using useJobPoller; the store only
 * receives updates via updateJobState / onJobComplete / onJobError callbacks.
 */
export const useIngestionStore = defineStore('ingestion', () => {
  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------

  /** Active job object: { id, status, progress_pct, stage_label, error_message, result_ref } */
  const activeJob = ref(null)

  /** The job ID returned by POST /api/ingestion/jobs */
  const activeJobId = ref(null)

  /** True while the POST request is in-flight */
  const isSubmitting = ref(false)

  /** Error message from a failed submission (not a polling error) */
  const submitError = ref(null)

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  /**
   * Submit a new ingestion job.
   *
   * Expected payload shape:
   *   { instruments: string[], source: 'OANDA'|'DUKASCOPY', start_date: 'YYYY-MM-DD', end_date: 'YYYY-MM-DD' }
   *
   * Returns the job_id string on success, throws on API error.
   */
  async function submitJob(payload) {
    submitError.value = null
    activeJob.value = null
    activeJobId.value = null
    isSubmitting.value = true

    try {
      const response = await startIngestionJob(payload)
      const jobId = response.job_id ?? response.id
      activeJobId.value = jobId
      // Seed the job panel immediately so the UI can show QUEUED state
      activeJob.value = {
        id: jobId,
        status: response.status ?? 'queued',
        progress_pct: 0,
        stage_label: null,
        error_message: null,
        result_ref: null,
      }
      return jobId
    } catch (err) {
      const msg =
        err?.response?.data?.detail ??
        err?.message ??
        'Submission failed — check API connection.'
      submitError.value = msg
      throw err
    } finally {
      isSubmitting.value = false
    }
  }

  /**
   * Called by the job poller on every tick with the latest job object.
   * Transient poll errors (_pollError key present) are silently ignored to
   * preserve last-known state.
   */
  function updateJobState(job) {
    if (!job || job._pollError) return
    activeJob.value = {
      id: job.id ?? activeJobId.value,
      status: job.status ?? activeJob.value?.status,
      progress_pct: job.progress_pct ?? 0,
      stage_label: job.stage_label ?? null,
      error_message: job.error_message ?? null,
      result_ref: job.result_ref ?? null,
    }
  }

  /** Called by the poller when job reaches SUCCEEDED. */
  function onJobComplete(job) {
    updateJobState(job)
  }

  /** Called by the poller when job reaches FAILED or CANCELLED. */
  function onJobError(job) {
    updateJobState(job)
  }

  /** Reset job state so the form can accept a new submission. */
  function resetJob() {
    activeJob.value = null
    activeJobId.value = null
    submitError.value = null
  }

  return {
    // state
    activeJob,
    activeJobId,
    isSubmitting,
    submitError,
    // actions
    submitJob,
    updateJobState,
    onJobComplete,
    onJobError,
    resetJob,
  }
})
