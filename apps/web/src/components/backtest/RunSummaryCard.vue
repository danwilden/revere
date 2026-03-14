<template>
  <div :class="['run-summary-card', 'nb-panel', statusAccentClass]">
    <!-- Header row: run ID + status badge -->
    <div class="run-summary-card__header">
      <div class="run-summary-card__id">
        <span class="nb-label">RUN</span>
        <span class="font-mono run-summary-card__id-value">{{ shortId }}</span>
      </div>
      <StatusBadge :status="run.status ?? 'unknown'" />
    </div>

    <!-- Core meta: instrument / timeframe / date range -->
    <div class="run-summary-card__meta">
      <div class="meta-row">
        <span class="nb-label">INSTRUMENT</span>
        <span class="font-mono meta-value">{{ run.instrument_id ?? '—' }}</span>
      </div>
      <div class="meta-row">
        <span class="nb-label">TIMEFRAME</span>
        <span class="font-mono meta-value">{{ run.timeframe ?? '—' }}</span>
      </div>
      <div class="meta-row">
        <span class="nb-label">RANGE</span>
        <span class="font-mono meta-value date-range">{{ dateRange }}</span>
      </div>
      <div class="meta-row">
        <span class="nb-label">STRATEGY</span>
        <span class="font-mono meta-value strategy-id">{{ run.strategy_id ?? '—' }}</span>
      </div>
    </div>

    <!-- Quick stats (only when run has metrics) -->
    <div v-if="hasMetrics" class="run-summary-card__stats">
      <div class="stat-tile">
        <span class="nb-label">NET RETURN</span>
        <span :class="['font-mono', 'stat-value', netReturnClass]">
          {{ formatPct(run.metrics?.net_return_pct) }}
        </span>
      </div>
      <div class="stat-tile">
        <span class="nb-label">TRADES</span>
        <span class="font-mono stat-value">{{ run.metrics?.total_trades ?? '—' }}</span>
      </div>
      <div class="stat-tile">
        <span class="nb-label">WIN RATE</span>
        <span class="font-mono stat-value">{{ formatPct(run.metrics?.win_rate) }}</span>
      </div>
      <div class="stat-tile">
        <span class="nb-label">MAX DD</span>
        <span class="font-mono stat-value text-red">
          {{ formatPct(run.metrics?.max_drawdown_pct) }}
        </span>
      </div>
    </div>

    <!-- Created at timestamp -->
    <div class="run-summary-card__footer">
      <span class="nb-label">{{ createdAt }}</span>
      <button
        class="nb-btn nb-btn--primary run-summary-card__cta"
        :disabled="!isSucceeded"
        @click="handleViewResults"
      >
        VIEW RESULTS
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const props = defineProps({
  /**
   * BacktestRun object from /api/backtests/runs.
   * Expected fields: id, status, instrument_id, timeframe, start_date, end_date,
   *   strategy_id, created_at, metrics (optional)
   */
  run: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['view-results'])

const router = useRouter()

const shortId = computed(() => {
  const id = props.run?.id ?? ''
  return id.length > 12 ? `${id.slice(0, 8)}...` : id
})

const isSucceeded = computed(() =>
  (props.run?.status ?? '').toLowerCase() === 'succeeded'
)

const statusAccentClass = computed(() => {
  const s = (props.run?.status ?? '').toLowerCase()
  if (s === 'succeeded') return 'run-summary-card--success'
  if (s === 'failed') return 'run-summary-card--error'
  if (s === 'running') return 'run-summary-card--running'
  return ''
})

const dateRange = computed(() => {
  const s = props.run?.start_date ?? ''
  const e = props.run?.end_date ?? ''
  if (!s && !e) return '—'
  return `${s} → ${e}`
})

const hasMetrics = computed(() => isSucceeded.value && !!props.run?.metrics)

const netReturnClass = computed(() => {
  const v = props.run?.metrics?.net_return_pct
  if (v === null || v === undefined) return ''
  return v >= 0 ? 'text-green' : 'text-red'
})

const createdAt = computed(() => {
  const raw = props.run?.created_at
  if (!raw) return 'CREATED —'
  try {
    const d = new Date(raw)
    return `CREATED ${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`
  } catch {
    return raw
  }
})

function formatPct(val) {
  if (val === null || val === undefined) return '—'
  const n = Number(val)
  return isNaN(n) ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function handleViewResults() {
  emit('view-results', props.run.id)
  router.push({ name: 'Results', query: { runId: props.run.id } })
}
</script>

<style scoped>
.run-summary-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  border: 1px solid var(--clr-border);
  transition: border-color 100ms;
}

.run-summary-card--success {
  border-color: rgba(0, 255, 65, 0.3);
}

.run-summary-card--error {
  border-color: rgba(255, 34, 34, 0.3);
}

.run-summary-card--running {
  border-color: rgba(255, 230, 0, 0.3);
}

/* Header */
.run-summary-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.run-summary-card__id {
  display: flex;
  align-items: center;
  gap: 8px;
}

.run-summary-card__id-value {
  font-size: 13px;
  color: var(--clr-yellow);
  letter-spacing: 0.05em;
}

/* Meta rows */
.run-summary-card__meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-top: 4px;
  border-top: 1px solid var(--clr-border);
}

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}

.meta-value {
  font-size: 12px;
  color: var(--clr-text);
  text-align: right;
}

.date-range {
  font-size: 11px;
  color: var(--clr-text-muted);
}

.strategy-id {
  font-size: 11px;
  color: var(--clr-text-muted);
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Quick stats grid */
.run-summary-card__stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid var(--clr-border);
}

.stat-tile {
  background: var(--clr-bg);
  border: 1px solid var(--clr-border);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.stat-value {
  font-size: 14px;
  font-weight: 700;
  color: var(--clr-text);
}

/* Footer */
.run-summary-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid var(--clr-border);
  flex-wrap: wrap;
  gap: 8px;
}

.run-summary-card__cta {
  font-size: 11px;
  padding: 6px 14px;
}
</style>
