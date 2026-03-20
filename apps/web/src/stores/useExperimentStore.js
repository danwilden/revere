import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listExperiments,
  getExperiment,
  createExperiment,
  updateExperimentStatus,
} from '@/api/research.js'

export const useExperimentStore = defineStore('experiments', () => {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  /** API-layer ExperimentRecord[] */
  const experiments = ref([])

  /** Total count from list response */
  const count = ref(0)

  /** The experiment row that is selected in the left-column list */
  const selectedExperiment = ref(null)

  /**
   * Full detail response for the selected experiment.
   * Shape: { experiment: ExperimentRecord, iterations: ExperimentIteration[] }
   */
  const selectedExperimentDetail = ref(null)

  /** True while any async API call is in flight */
  const loading = ref(false)

  /** Top-level error */
  const error = ref(null)

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /**
   * GET /api/experiments with optional filters.
   * @param {Object} [params] - { limit?, status? }
   */
  async function fetchExperiments(params = {}) {
    loading.value = true
    error.value = null
    try {
      const data = await listExperiments(params)
      // Response: { experiments: [], count: n }
      experiments.value = Array.isArray(data.experiments) ? data.experiments : []
      count.value = data.count ?? experiments.value.length
    } catch (err) {
      error.value = err?.normalized?.message ?? err.message ?? 'Failed to load experiments'
    } finally {
      loading.value = false
    }
  }

  /**
   * GET /api/experiments/{id} — fetch detail with iterations.
   * Populates selectedExperimentDetail.
   * @param {string} id
   */
  async function fetchExperiment(id) {
    loading.value = true
    error.value = null
    try {
      const data = await getExperiment(id)
      // Response: { experiment: {...}, iterations: [...] }
      selectedExperimentDetail.value = data
      return data
    } catch (err) {
      error.value = err?.normalized?.message ?? err.message ?? 'Failed to load experiment'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * POST /api/experiments — create a new API-layer experiment.
   * Inserts it at the front of the experiments list on success.
   * @param {Object} payload
   * @returns {Object} { experiment: ExperimentRecord }
   */
  async function createNewExperiment(payload) {
    loading.value = true
    error.value = null
    try {
      const data = await createExperiment(payload)
      const created = data.experiment ?? data
      experiments.value = [created, ...experiments.value]
      count.value += 1
      return data
    } catch (err) {
      error.value = err?.normalized?.message ?? err.message ?? 'Failed to create experiment'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * PATCH /api/experiments/{id}/status
   * @param {string} id
   * @param {string} status
   * @returns {Object} { experiment: ExperimentRecord }
   */
  async function updateStatus(id, status) {
    loading.value = true
    error.value = null
    try {
      const data = await updateExperimentStatus(id, { status })
      const updated = data.experiment ?? data
      // Patch in list
      experiments.value = experiments.value.map((e) => (e.id === id ? updated : e))
      // Patch selected
      if (selectedExperiment.value?.id === id) {
        selectedExperiment.value = updated
      }
      if (selectedExperimentDetail.value?.experiment?.id === id) {
        selectedExperimentDetail.value = {
          ...selectedExperimentDetail.value,
          experiment: updated,
        }
      }
      return data
    } catch (err) {
      error.value = err?.normalized?.message ?? err.message ?? 'Failed to update experiment status'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * Convenience: fetch a single experiment and set it as selectedExperiment.
   * Also fetches the full detail (iterations, etc.).
   * @param {string} id
   */
  async function selectExperiment(id) {
    // Optimistically set from local list for instant feedback
    const local = experiments.value.find((e) => e.id === id)
    if (local) selectedExperiment.value = local

    const detail = await fetchExperiment(id)
    selectedExperiment.value = detail.experiment ?? detail
  }

  /** Clear selected experiment and its detail. */
  function clearSelection() {
    selectedExperiment.value = null
    selectedExperimentDetail.value = null
  }

  /** Clear the top-level error. */
  function clearError() {
    error.value = null
  }

  return {
    // state
    experiments,
    count,
    selectedExperiment,
    selectedExperimentDetail,
    loading,
    error,
    // actions
    fetchExperiments,
    fetchExperiment,
    createNewExperiment,
    updateStatus,
    selectExperiment,
    clearSelection,
    clearError,
  }
})
