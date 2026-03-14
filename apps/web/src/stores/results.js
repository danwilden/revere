import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getBacktestRun, getBacktestTrades, getBacktestEquity } from '@/api/backtests.js'

export const useResultsStore = defineStore('results', () => {
  // --- State ---
  const currentRunId = ref(null)
  const run = ref(null)      // BacktestRun object (not the response wrapper)
  const metricsData = ref([]) // metrics array from response top-level
  const trades = ref([])
  const equity = ref(null)   // raw equity response: { run_id, equity_curve: [{timestamp, equity, drawdown}] }
  const loading = ref(false)
  const error = ref(null)

  // --- Computed ---

  /**
   * All metrics — flat array of PerformanceMetric objects:
   * { metric_name, metric_value, segment_type, segment_key }
   * Source: top-level `metrics` from GET /api/backtests/runs/{id} response.
   */
  const metrics = computed(() => metricsData.value ?? [])

  /**
   * Overall metrics only (segment_type === 'overall')
   */
  const overallMetrics = computed(() =>
    metrics.value.filter((m) => m.segment_type === 'overall')
  )

  /**
   * Per-regime metrics (segment_type === 'regime')
   */
  const regimeMetrics = computed(() =>
    metrics.value.filter((m) => m.segment_type === 'regime')
  )

  /**
   * True if any regime metrics are present
   */
  const hasRegimeMetrics = computed(() => regimeMetrics.value.length > 0)

  /**
   * Regime metrics grouped by segment_key (regime label)
   * Returns Map<string, PerformanceMetric[]>
   */
  const regimeMetricsByLabel = computed(() => {
    const map = new Map()
    for (const m of regimeMetrics.value) {
      const key = m.segment_key ?? 'UNKNOWN'
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(m)
    }
    return map
  })

  /**
   * Look up a single overall metric value by name.
   * Returns null if not present.
   * @param {string} name
   */
  function getOverallMetric(name) {
    const found = overallMetrics.value.find((m) => m.metric_name === name)
    return found?.metric_value ?? null
  }

  // --- Actions ---

  /**
   * Load all run data in parallel (run, trades, equity).
   * Updates currentRunId, run, trades, equity, loading, error.
   * @param {string} runId
   */
  async function loadRun(runId) {
    currentRunId.value = runId
    loading.value = true
    error.value = null
    run.value = null
    trades.value = []
    equity.value = null

    try {
      const [runData, tradesData, equityData] = await Promise.all([
        getBacktestRun(runId),
        getBacktestTrades(runId),
        getBacktestEquity(runId),
      ])
      // GET /api/backtests/runs/{id} returns { run: BacktestRun, metrics: PerformanceMetric[] }
      run.value = runData?.run ?? runData
      metricsData.value = runData?.metrics ?? []
      // GET /api/backtests/runs/{id}/trades returns { trades: [...], count: N }
      trades.value = Array.isArray(tradesData) ? tradesData : (tradesData?.trades ?? [])
      // GET /api/backtests/runs/{id}/equity returns { run_id, equity_curve: [{timestamp, equity, drawdown}] }
      equity.value = equityData
    } catch (err) {
      error.value = err?.normalized?.message ?? err?.message ?? 'Failed to load backtest run'
    } finally {
      loading.value = false
    }
  }

  /**
   * Reset all state
   */
  function clear() {
    currentRunId.value = null
    run.value = null
    metricsData.value = []
    trades.value = []
    equity.value = null
    loading.value = false
    error.value = null
  }

  return {
    // state
    currentRunId,
    run,
    trades,
    equity,
    loading,
    error,
    // computed
    metrics,
    overallMetrics,
    regimeMetrics,
    hasRegimeMetrics,
    regimeMetricsByLabel,
    // helpers
    getOverallMetric,
    // actions
    loadRun,
    clear,
  }
})
