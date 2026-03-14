import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createStrategy,
  listStrategies,
  getStrategy,
  validateStrategy as apiValidateStrategy,
} from '@/api/strategies.js'

export const useStrategiesStore = defineStore('strategies', () => {
  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  /** All persisted strategy records from GET /api/strategies */
  const strategies = ref([])

  /** Strategy currently being viewed or edited. Null when creating new. */
  const activeStrategy = ref(null)

  /**
   * UI mode:
   *   'list'   — strategy list view
   *   'create' — new strategy editor (activeStrategy is null)
   *   'edit'   — edit existing (activeStrategy is populated)
   *   'detail' — read-only view of a strategy
   */
  const editorMode = ref('list')

  /**
   * Which editor tab is showing in create/edit mode.
   * 'rules' | 'code'
   */
  const strategyType = ref('rules')

  /**
   * Last validation result from POST /api/strategies/{id}/validate.
   * Shape: { valid: bool, errors: string[] }
   */
  const validationResult = ref(null)

  /** True while any async API call is in flight */
  const loading = ref(false)

  /** Top-level error message for non-validation API failures */
  const error = ref(null)

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  /**
   * Load all strategies from GET /api/strategies.
   */
  async function fetchStrategies() {
    loading.value = true
    error.value = null
    try {
      const data = await listStrategies()
      strategies.value = Array.isArray(data) ? data : []
    } catch (err) {
      error.value = err?.normalized?.message ?? err?.message ?? 'Failed to load strategies'
    } finally {
      loading.value = false
    }
  }

  /**
   * Persist a new strategy via POST /api/strategies.
   * Refreshes the strategy list on success.
   *
   * @param {Object} payload - { name, strategy_type, definition_json }
   * @returns {Object} Created strategy record
   */
  async function saveStrategy(payload) {
    loading.value = true
    error.value = null
    validationResult.value = null
    try {
      const created = await createStrategy(payload)
      // Insert at front of list so it appears immediately
      strategies.value = [created, ...strategies.value]
      activeStrategy.value = created
      return created
    } catch (err) {
      error.value = err?.normalized?.message ?? err?.message ?? 'Failed to save strategy'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * Load a single strategy by ID into activeStrategy.
   *
   * @param {string} id - Strategy UUID
   */
  async function loadStrategy(id) {
    loading.value = true
    error.value = null
    try {
      const data = await getStrategy(id)
      activeStrategy.value = data
      return data
    } catch (err) {
      error.value = err?.normalized?.message ?? err?.message ?? 'Failed to load strategy'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * Validate the active strategy via POST /api/strategies/{id}/validate.
   * Assumes activeStrategy is set and has an .id.
   *
   * @param {string} id - Strategy UUID
   * @returns {Object} { valid: bool, errors: string[] }
   */
  async function validateStrategy(id) {
    loading.value = true
    error.value = null
    validationResult.value = null
    try {
      const result = await apiValidateStrategy(id)
      // Backend returns { valid, errors } — normalise defensively
      validationResult.value = {
        valid: result.valid ?? false,
        errors: Array.isArray(result.errors) ? result.errors : [],
      }
      return validationResult.value
    } catch (err) {
      error.value = err?.normalized?.message ?? err?.message ?? 'Validation request failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * Switch the editor mode. Clears validation result on mode change.
   *
   * @param {'list'|'create'|'edit'|'detail'} mode
   */
  function setEditorMode(mode) {
    editorMode.value = mode
    validationResult.value = null
    if (mode === 'create') {
      activeStrategy.value = null
    }
  }

  /** Clear the top-level error banner. */
  function clearError() {
    error.value = null
  }

  /** Clear validation result. */
  function clearValidation() {
    validationResult.value = null
  }

  return {
    // state
    strategies,
    activeStrategy,
    editorMode,
    strategyType,
    validationResult,
    loading,
    error,
    // actions
    fetchStrategies,
    saveStrategy,
    loadStrategy,
    validateStrategy,
    setEditorMode,
    clearError,
    clearValidation,
  }
})
