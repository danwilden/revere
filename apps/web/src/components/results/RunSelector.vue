<template>
  <NbCard title="SELECT BACKTEST RUN">
    <LoadingState v-if="loading" message="LOADING RUNS..." />
    <ErrorBanner v-else-if="error" :message="error" />
    <div v-else-if="runs.length" class="run-list">
      <button
        v-for="run in runs"
        :key="run.id"
        class="run-item nb-btn"
        @click="emit('select', run.id)"
      >
        <div class="run-item__top">
          <span class="font-mono run-item__id text-yellow">{{ run.id }}</span>
          <StatusBadge :status="run.status ?? 'succeeded'" />
        </div>
        <div class="run-item__meta">
          <span class="font-mono text-muted">
            {{ run.instrument_id }} &bull; {{ run.timeframe }}
          </span>
          <span class="font-mono text-dim">
            {{ run.start_date }} → {{ run.end_date }}
          </span>
        </div>
        <div v-if="run.strategy_id" class="run-item__strategy font-mono text-dim">
          STRATEGY: {{ run.strategy_id }}
        </div>
      </button>
    </div>
    <EmptyState v-else message="NO BACKTEST RUNS FOUND" />
  </NbCard>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import NbCard from '@/components/ui/NbCard.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import { listBacktestRuns } from '@/api/backtests.js'

const emit = defineEmits(['select'])

const runs = ref([])
const loading = ref(false)
const error = ref(null)

onMounted(async () => {
  loading.value = true
  error.value = null
  try {
    const data = await listBacktestRuns()
    // API may return { runs: [] } or a plain array — handle both
    runs.value = Array.isArray(data) ? data : (data?.runs ?? [])
    // Sort by created_at desc if available, so most recent is first
    runs.value.sort((a, b) => {
      const ta = a.created_at ?? ''
      const tb = b.created_at ?? ''
      return tb.localeCompare(ta)
    })
  } catch (err) {
    error.value = err?.normalized?.message ?? err?.message ?? 'Failed to load runs'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.run-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.run-item {
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb-sm);
  padding: 12px 14px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.1s, box-shadow 0.1s;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.run-item:hover {
  border-color: var(--clr-yellow);
  box-shadow: var(--shadow-nb-yellow);
}

.run-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.run-item__id {
  font-size: 11px;
  letter-spacing: 0.05em;
  word-break: break-all;
}

.run-item__meta {
  display: flex;
  gap: 16px;
  font-size: 11px;
}

.run-item__strategy {
  font-size: 10px;
  letter-spacing: 0.08em;
}
</style>
