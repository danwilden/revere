import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getDefaultInstruments } from '@/api/instruments.js'
import {
  startHmmTrainingJob,
  listHmmModels,
  applyHmmLabels,
} from '@/api/models.js'

export const useModelsStore = defineStore('models', () => {
  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------

  /** Default instruments from /api/instruments/defaults */
  const instruments = ref([])

  /** All persisted HMM model records from /api/models/hmm */
  const hmmModels = ref([])

  /** Current active job object — shape mirrors JobResponse schema:
   *  { id, status, progress_pct, stage_label, error_message, result_ref, ... }
   */
  const activeJob = ref(null)

  /** ID of the currently polling job */
  const activeJobId = ref(null)

  /** Set when a job succeeds — holds the model_id from result_ref */
  const trainedModelId = ref(null)

  /** Loading flags for async operations */
  const loadingInstruments = ref(false)
  const loadingModels = ref(false)

  /** Top-level error message for non-job errors (e.g., fetch failures) */
  const fetchError = ref(null)

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  async function fetchInstruments() {
    loadingInstruments.value = true
    fetchError.value = null
    try {
      const data = await getDefaultInstruments()
      // GET /api/instruments/defaults returns { default_pairs: [...], instruments: [...] }
      // Each instrument object has `symbol` as the primary key (not `id`)
      const raw = Array.isArray(data) ? data : (data?.instruments ?? [])
      // Normalise to a consistent shape: add `id` alias for symbol so downstream
      // consumers (ModelsView instrumentItems, BacktestView instrument selector) can
      // use inst.id without knowing the response key name.
      instruments.value = raw.map((inst) => ({
        ...inst,
        id: inst.id ?? inst.symbol,
        display_name: inst.display_name ?? inst.symbol,
      }))
    } catch (err) {
      fetchError.value = err?.normalized?.message ?? err?.message ?? 'Failed to load instruments'
    } finally {
      loadingInstruments.value = false
    }
  }

  async function fetchModels() {
    loadingModels.value = true
    try {
      const data = await listHmmModels()
      hmmModels.value = Array.isArray(data) ? data : []
    } catch (err) {
      // Non-fatal — show stale list, don't overwrite fetchError
      console.error('[useModelsStore] fetchModels error:', err)
    } finally {
      loadingModels.value = false
    }
  }

  /**
   * Submit a new HMM training job.
   * Returns the job_id string on success, throws on API error.
   *
   * @param {Object} payload - { instrument, timeframe, train_start, train_end, num_states, feature_set_name }
   */
  async function submitTrainingJob(payload) {
    // Reset previous job state before submitting
    activeJob.value = null
    activeJobId.value = null
    trainedModelId.value = null

    const response = await startHmmTrainingJob(payload)
    // response shape: { job_id, status }
    activeJobId.value = response.job_id
    activeJob.value = {
      id: response.job_id,
      status: response.status ?? 'queued',
      progress_pct: 0,
      stage_label: null,
      error_message: null,
      result_ref: null,
    }
    return response.job_id
  }

  /**
   * Called by the poller on each tick with the latest job object.
   * @param {Object} job - job record from GET /api/jobs/{id}
   */
  function updateJobState(job) {
    if (!job || job._pollError) {
      // Transient poll failure — preserve last known state, don't clobber
      return
    }
    activeJob.value = {
      id: job.id ?? activeJobId.value,
      status: job.status ?? activeJob.value?.status,
      progress_pct: job.progress_pct ?? 0,
      stage_label: job.stage_label ?? null,
      error_message: job.error_message ?? null,
      result_ref: job.result_ref ?? null,
    }
  }

  /**
   * Apply a semantic label map to a model.
   * @param {string} modelId
   * @param {Object} labelMap - { "0": "TREND_BULL_LOW_VOL", ... }
   */
  async function applyLabels(modelId, labelMap) {
    const result = await applyHmmLabels(modelId, { label_map: labelMap })
    // Patch the local model record so the UI updates without a full refresh
    const idx = hmmModels.value.findIndex((m) => m.id === modelId)
    if (idx !== -1) {
      hmmModels.value[idx] = {
        ...hmmModels.value[idx],
        label_map_json: labelMap,
      }
    }
    return result
  }

  /** Clear job state after the user dismisses the panel */
  function clearJob() {
    activeJob.value = null
    activeJobId.value = null
  }

  return {
    // state
    instruments,
    hmmModels,
    activeJob,
    activeJobId,
    trainedModelId,
    loadingInstruments,
    loadingModels,
    fetchError,
    // actions
    fetchInstruments,
    fetchModels,
    submitTrainingJob,
    updateJobState,
    applyLabels,
    clearJob,
  }
})
