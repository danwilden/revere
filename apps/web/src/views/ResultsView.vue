<template>
  <div class="view-container">
    <!-- ── Header ─────────────────────────────────────────────── -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">RESULTS</h1>
      </div>
      <div class="view-header__right">
        <button
          v-if="store.run"
          class="nb-btn"
          style="font-size: 11px"
          @click="handleClear"
        >
          &larr; ALL RUNS
        </button>
        <span class="view-header__badge">BACKTEST RESULTS</span>
      </div>
    </header>

    <div class="view-body">

      <!-- ── Global error ──────────────────────────────────────── -->
      <ErrorBanner v-if="store.error" :message="store.error" />

      <!-- ── Loading ───────────────────────────────────────────── -->
      <LoadingState v-if="store.loading" message="LOADING RUN DATA..." />

      <!-- ── Run Selector (no runId, no loaded run) ─────────────── -->
      <template v-else-if="!store.run">
        <RunSelector @select="handleSelectRun" />
      </template>

      <!-- ── Results panels ─────────────────────────────────────── -->
      <template v-else>

        <!-- Section 1: Run Header -->
        <NbCard accent="yellow">
          <!-- Research grade warning — mandatory when oracle labels active -->
          <WarningBanner
            v-if="store.run.oracle_regime_labels"
            :message="RESEARCH_GRADE_WARNING"
            style="margin-bottom: 16px"
          />

          <div class="run-header">
            <div class="run-header__col">
              <span class="nb-label">RUN ID</span>
              <span class="font-mono nb-value nb-value--sm text-yellow">
                {{ store.run.id }}
              </span>
            </div>
            <div class="run-header__col">
              <span class="nb-label">INSTRUMENT</span>
              <span class="font-mono nb-value">{{ store.run.instrument_id ?? '—' }}</span>
            </div>
            <div class="run-header__col">
              <span class="nb-label">TIMEFRAME</span>
              <span class="font-mono nb-value">{{ store.run.timeframe ?? '—' }}</span>
            </div>
            <div class="run-header__col">
              <span class="nb-label">DATE RANGE</span>
              <span class="font-mono nb-value">
                {{ store.run.start_date ?? '—' }} &rarr; {{ store.run.end_date ?? '—' }}
              </span>
            </div>
            <div class="run-header__col">
              <span class="nb-label">STRATEGY</span>
              <span class="font-mono nb-value nb-value--sm">
                {{ store.run.strategy_id ?? store.run.strategy_name ?? '—' }}
              </span>
            </div>
            <div class="run-header__col">
              <span class="nb-label">STATUS</span>
              <StatusBadge :status="store.run.status ?? 'succeeded'" />
            </div>
          </div>
        </NbCard>

        <!-- Section 2: Metrics Summary -->
        <MetricsSummary :metrics="store.overallMetrics" />

        <!-- Section 3: Charts (equity + drawdown stacked) -->
        <EquityChart
          :equity="equityValues"
          :timestamps="equityTimestamps"
        />

        <DrawdownChart
          :drawdown="drawdownValues"
          :timestamps="equityTimestamps"
        />

        <!-- Section 4: Trade Log -->
        <NbCard title="TRADE LOG">
          <template #header-right>
            <span class="font-mono text-muted" style="font-size: 11px">
              {{ store.trades.length }} TRADES
            </span>
          </template>
          <TradeTable :trades="store.trades" />
        </NbCard>

        <!-- Section 5: Per-Regime Breakdown (only if data present) -->
        <RegimeBreakdown
          v-if="store.hasRegimeMetrics"
          :regime-metrics-by-label="store.regimeMetricsByLabel"
        />

      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useResultsStore } from '@/stores/results.js'

// UI primitives
import NbCard from '@/components/ui/NbCard.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import WarningBanner from '@/components/ui/WarningBanner.vue'
import TradeTable from '@/components/ui/TradeTable.vue'

// Results sub-components
import MetricsSummary from '@/components/results/MetricsSummary.vue'
import EquityChart from '@/components/results/EquityChart.vue'
import DrawdownChart from '@/components/results/DrawdownChart.vue'
import RegimeBreakdown from '@/components/results/RegimeBreakdown.vue'
import RunSelector from '@/components/results/RunSelector.vue'

// Constants
import { RESEARCH_GRADE_WARNING } from '@/utils/constants.js'

const route = useRoute()
const router = useRouter()
const store = useResultsStore()

// Safely unpack equity data from the store.
// Backend returns { run_id, equity_curve: [{ timestamp, equity, drawdown }] }
const equityCurve = computed(() => store.equity?.equity_curve ?? [])
const equityValues = computed(() => equityCurve.value.map((p) => p.equity))
const drawdownValues = computed(() => equityCurve.value.map((p) => p.drawdown))
const equityTimestamps = computed(() => equityCurve.value.map((p) => p.timestamp))

onMounted(() => {
  const runId = route.query.runId
  if (runId) {
    store.loadRun(runId)
  }
  // If no runId, fall through to RunSelector
})

function handleSelectRun(runId) {
  // Update the URL query param and load the run
  router.replace({ query: { runId } })
  store.loadRun(runId)
}

function handleClear() {
  store.clear()
  router.replace({ query: {} })
}
</script>

<style scoped>
.view-container {
  padding: 28px 32px;
  min-height: 100%;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
  gap: 16px;
}

.view-header__title {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.view-header__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.view-header__badge {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.15em;
  color: var(--clr-text-dim);
  border: 1px solid var(--clr-border);
  padding: 4px 10px;
  text-transform: uppercase;
}

.view-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Run Header layout ─────────────────────────────────────── */

.run-header {
  display: flex;
  flex-wrap: wrap;
  gap: 20px 32px;
}

.run-header__col {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 120px;
}
</style>
