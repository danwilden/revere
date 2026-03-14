import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getDefaultInstruments } from '@/api/instruments.js'
import { listStrategies } from '@/api/strategies.js'
import { startBacktestJob, listBacktestRuns } from '@/api/backtests.js'

/**
 * backtestStore — owns all state for the backtest submission workflow.
 *
 * State shape:
 *   instruments      — list of InstrumentSpec objects from /api/instruments/defaults
 *   strategies       — list of Strategy objects from /api/strategies
 *   activeJob        — current job object: { id, status, progress_pct, stage, error_message, result_ref }
 *   activeJobId      — string job ID, set on successful POST /api/backtests/jobs
 *   completedRunId   — string backtest run ID, set from job.result_ref on SUCCEEDED
 *   recentRuns       — list of BacktestRun objects from /api/backtests/runs
 *   submitError      — string error from failed submission (not polling — polling errors go in activeJob)
 *   isSubmitting     — bool: true while POST /api/backtests/jobs is in-flight
 *   isLoadingInstruments  — bool
 *   isLoadingStrategies   — bool
 *   isLoadingRuns         — bool
 */
export const useBacktestStore = defineStore('backtest', () => {
  // --- Data ---
  const instruments = ref([])
  const strategies = ref([])
  const recentRuns = ref([])

  // --- Job state ---
  const activeJob = ref(null)
  const activeJobId = ref(null)
  const completedRunId = ref(null)

  // --- UI state ---
  const submitError = ref(null)
  const isSubmitting = ref(false)
  const isLoadingInstruments = ref(false)
  const isLoadingStrategies = ref(false)
  const isLoadingRuns = ref(false)

  // --- Actions ---

  async function fetchInstruments() {
    isLoadingInstruments.value = true
    try {
      const data = await getDefaultInstruments()
      // GET /api/instruments/defaults returns { default_pairs: [...], instruments: [...] }
      // Each instrument object uses `symbol` as the primary key (not `id` or `instrument_id`).
      // Normalise to add `id` and `instrument_id` aliases so the view template can use
      // inst.instrument_id ?? inst.id without knowing the response key name.
      const raw = Array.isArray(data) ? data : (data?.instruments ?? [])
      instruments.value = raw.map((inst) => ({
        ...inst,
        id: inst.id ?? inst.symbol,
        instrument_id: inst.instrument_id ?? inst.symbol,
        display_name: inst.display_name ?? inst.symbol,
      }))
    } catch (err) {
      console.error('[backtest] fetchInstruments failed:', err)
    } finally {
      isLoadingInstruments.value = false
    }
  }

  async function fetchStrategies() {
    isLoadingStrategies.value = true
    try {
      const data = await listStrategies()
      strategies.value = Array.isArray(data) ? data : (data.strategies ?? [])
    } catch (err) {
      console.error('[backtest] fetchStrategies failed:', err)
    } finally {
      isLoadingStrategies.value = false
    }
  }

  async function fetchRecentRuns() {
    isLoadingRuns.value = true
    try {
      const data = await listBacktestRuns()
      recentRuns.value = Array.isArray(data) ? data : (data.runs ?? [])
    } catch (err) {
      console.error('[backtest] fetchRecentRuns failed:', err)
    } finally {
      isLoadingRuns.value = false
    }
  }

  /**
   * Submit a backtest job.
   *
   * Expected payload shape (caller builds this):
   * {
   *   strategy_id:          string,
   *   instrument_id:        string,
   *   timeframe:            'M1' | 'H1' | 'H4' | 'D',
   *   start_date:           'YYYY-MM-DD',
   *   end_date:             'YYYY-MM-DD',
   *   pip_size:             number,          // REQUIRED — 0.01 for JPY, 0.0001 otherwise
   *   initial_equity:       number,          // default 10000
   *   spread_pips:          number,          // default 1.0
   *   slippage_pips:        number,          // default 0.5
   *   commission_per_unit:  number,          // default 0.0
   * }
   *
   * Returns { job_id } from the 202 response.
   * Sets activeJobId so the view can kick off the poller.
   */
  async function submitJob(payload) {
    submitError.value = null
    activeJob.value = null
    activeJobId.value = null
    completedRunId.value = null
    isSubmitting.value = true

    try {
      const response = await startBacktestJob(payload)
      // Backend 202 returns { job_id: '...' }
      const jobId = response.job_id ?? response.id
      activeJobId.value = jobId
      return { job_id: jobId }
    } catch (err) {
      const msg =
        err?.response?.data?.detail ??
        err?.message ??
        'Submission failed — check backend logs.'
      submitError.value = msg
      throw err
    } finally {
      isSubmitting.value = false
    }
  }

  /**
   * Called by the job poller on every tick.
   * Merges partial job data into activeJob.
   */
  function updateJobState(job) {
    if (!job) return
    activeJob.value = { ...(activeJob.value ?? {}), ...job }
  }

  /**
   * Called when the poller reports SUCCEEDED.
   * Extracts the run ID from result_ref and refreshes the runs list.
   */
  function onJobComplete(job) {
    updateJobState(job)
    completedRunId.value = job.result_ref ?? null
    fetchRecentRuns()
  }

  /**
   * Called when the poller reports FAILED or CANCELLED.
   */
  function onJobError(job) {
    updateJobState(job)
  }

  /**
   * Reset job state — clears active job so a new submission can begin.
   */
  function resetJob() {
    activeJob.value = null
    activeJobId.value = null
    completedRunId.value = null
    submitError.value = null
  }

  return {
    // state
    instruments,
    strategies,
    recentRuns,
    activeJob,
    activeJobId,
    completedRunId,
    submitError,
    isSubmitting,
    isLoadingInstruments,
    isLoadingStrategies,
    isLoadingRuns,
    // actions
    fetchInstruments,
    fetchStrategies,
    fetchRecentRuns,
    submitJob,
    updateJobState,
    onJobComplete,
    onJobError,
    resetJob,
  }
})
