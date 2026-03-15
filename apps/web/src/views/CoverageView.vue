<template>
  <div class="view-container">
    <!-- Header -->
    <div class="view-header">
      <div class="view-header__title">
        <span class="nb-page-title">DATA COVERAGE</span>
        <span class="nb-label">MARKET DATA AVAILABILITY MATRIX</span>
      </div>
      <div class="view-header__actions">
        <span v-if="store.lastUpdated" class="updated-label">
          UPDATED {{ fmtTime(store.lastUpdated) }}
        </span>
        <button class="nb-btn nb-btn--sm" @click="store.reload()" :disabled="store.loading">
          <v-icon icon="mdi-refresh" size="14" />
          REFRESH
        </button>
      </div>
    </div>

    <!-- Body -->
    <div class="view-body">
      <!-- Loading -->
      <LoadingState v-if="store.loading" message="Loading coverage data..." />

      <!-- Error -->
      <ErrorBanner v-else-if="store.error" :message="store.error" />

      <!-- Empty -->
      <EmptyState
        v-else-if="!store.loading && store.ranges.length === 0"
        message="No market data has been ingested yet."
        sub="Run a data ingestion job to populate coverage."
      />

      <!-- Content -->
      <template v-else>
        <!-- Matrix -->
        <NbCard title="COVERAGE MATRIX" icon="mdi-table">
          <CoverageMatrix :rows="store.coverageRows" :timeframes="TIMEFRAMES" />
        </NbCard>

        <!-- Timeline -->
        <NbCard title="TIMELINE" icon="mdi-chart-gantt" class="mt-4">
          <CoverageTimeline :rows="store.coverageRows" :globalBounds="store.globalDateBounds" />
        </NbCard>
      </template>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useCoverageStore } from '@/stores/coverage.js'
import NbCard from '@/components/ui/NbCard.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import CoverageMatrix from '@/components/coverage/CoverageMatrix.vue'
import CoverageTimeline from '@/components/coverage/CoverageTimeline.vue'

const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']

const store = useCoverageStore()

onMounted(() => {
  store.load()
})

function fmtTime(d) {
  if (!d) return ''
  return d.toLocaleTimeString('en-US', { hour12: false })
}
</script>

<style scoped>
.view-container {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 100%;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
  gap: 16px;
}

.view-header__title {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.view-header__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.updated-label {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-text-dim);
  letter-spacing: 0.1em;
}

.view-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mt-4 {
  margin-top: 0;
}
</style>
