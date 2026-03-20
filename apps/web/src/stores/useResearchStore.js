import { defineStore } from 'pinia'
import { ref } from 'vue'
import { triggerResearchRun, listResearchRuns, getResearchRun } from '@/api/research.js'

// Lab ExperimentRecord terminal statuses
const TERMINAL_STATUSES = ['succeeded', 'failed', 'archived']

export const useResearchStore = defineStore('research', () => {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  /** Lab ExperimentRecord[] from GET /api/research/runs */
  const researchRuns = ref([])

  /** ID of the run currently being polled or viewed */
  const activeRunId = ref(null)

  /** True while POST /api/research/run is in flight */
  const isSubmitting = ref(false)

  /** Error from the trigger call */
  const submitError = ref(null)

  /** True while fetching the run list */
  const loading = ref(false)

  /** Top-level list fetch error */
  const error = ref(null)

  // Internal polling timer handle
  let _pollTimer = null

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /**
   * Fetch the list of research runs.
   * @param {Object} [params] - { limit?, instrument? }
   */
  async function fetchResearchRuns(params = {}) {
    loading.value = true
    error.value = null
    try {
      const data = await listResearchRuns(params)
      researchRuns.value = Array.isArray(data) ? data : []
    } catch (err) {
      error.value = err?.normalized?.message ?? err.message ?? 'Failed to load research runs'
    } finally {
      loading.value = false
    }
  }

  /**
   * Fetch a single research run by ID and upsert into the local list.
   * @param {string} id
   * @returns {Object} The lab ExperimentRecord
   */
  async function fetchResearchRun(id) {
    try {
      const run = await getResearchRun(id)
      _upsertRun(run)
      return run
    } catch (err) {
      error.value = err?.normalized?.message ?? err.message ?? 'Failed to fetch research run'
      throw err
    }
  }

  /**
   * POST /api/research/run — trigger a new agentic research run.
   * Starts polling the new run until it reaches a terminal state.
   *
   * @param {Object} payload
   * @returns {{ experiment_id: string }} The 202 response body
   */
  async function triggerRun(payload) {
    isSubmitting.value = true
    submitError.value = null
    try {
      const response = await triggerResearchRun(payload)
      // response: { experiment_id, session_id, status, created_at }
      activeRunId.value = response.experiment_id
      startPolling(response.experiment_id)
      return response
    } catch (err) {
      submitError.value = err?.normalized?.message ?? err.message ?? 'Failed to trigger research run'
      throw err
    } finally {
      isSubmitting.value = false
    }
  }

  /**
   * Poll GET /api/research/runs/{id} every 3 seconds until the run is terminal.
   * Upserts the run into researchRuns on each tick.
   *
   * @param {string} runId
   */
  function startPolling(runId) {
    stopPolling()
    _pollTimer = setInterval(async () => {
      try {
        const run = await getResearchRun(runId)
        _upsertRun(run)
        if (TERMINAL_STATUSES.includes((run.status || '').toLowerCase())) {
          stopPolling()
        }
      } catch {
        // Transient errors — keep polling
      }
    }, 3000)
  }

  /** Stop any active polling timer. */
  function stopPolling() {
    if (_pollTimer !== null) {
      clearInterval(_pollTimer)
      _pollTimer = null
    }
  }

  /** Clear the trigger form error. */
  function clearSubmitError() {
    submitError.value = null
  }

  /** Clear the list-level error. */
  function clearError() {
    error.value = null
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  /** Upsert a run into researchRuns by ID. */
  function _upsertRun(run) {
    const idx = researchRuns.value.findIndex((r) => r.id === run.id)
    if (idx === -1) {
      researchRuns.value = [run, ...researchRuns.value]
    } else {
      researchRuns.value = researchRuns.value.map((r) => (r.id === run.id ? run : r))
    }
  }

  return {
    // state
    researchRuns,
    activeRunId,
    isSubmitting,
    submitError,
    loading,
    error,
    // actions
    fetchResearchRuns,
    fetchResearchRun,
    triggerRun,
    startPolling,
    stopPolling,
    clearSubmitError,
    clearError,
  }
})
