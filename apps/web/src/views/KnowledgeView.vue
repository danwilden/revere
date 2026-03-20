<template>
  <div class="knowledge-view">
    <!-- Header bar -->
    <div class="knowledge-header">
      <span class="header-title">KNOWLEDGE GRAPH</span>
      <span class="header-stats" v-if="store.graphData.stats">
        {{ store.graphData.stats.total_memories }} MEMORIES ·
        {{ store.graphData.stats.total_experiments }} EXPERIMENTS ·
        {{ store.graphData.stats.total_edges }} EDGES
      </span>
      <button class="nb-btn" @click="refresh" :disabled="store.loading">
        {{ store.loading ? 'LOADING...' : 'REFRESH' }}
      </button>
    </div>

    <!-- 3-column layout -->
    <div class="knowledge-body">
      <MemoryFilters @filter-change="onFilterChange" />

      <GraphCanvas
        :nodes="store.graphData.nodes || []"
        :edges="store.graphData.edges || []"
        @node-select="onNodeSelect"
      />

      <Transition name="card-slide">
        <MemoryCard v-if="store.selectedMemoryId" />
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useMemoryStore } from '@/stores/useMemoryStore.js'
import MemoryFilters from '@/components/knowledge/MemoryFilters.vue'
import GraphCanvas from '@/components/knowledge/GraphCanvas.vue'
import MemoryCard from '@/components/knowledge/MemoryCard.vue'

const store = useMemoryStore()

async function refresh() {
  await Promise.all([
    store.fetchGraph(),
    store.fetchMemories(),
  ])
}

function onFilterChange(filters) {
  store.fetchMemories({
    instrument: filters.instrument || undefined,
    outcome: filters.outcome || undefined,
  })
}

function onNodeSelect(nodeId) {
  store.selectNode(nodeId)
}

onMounted(refresh)
</script>

<style scoped>
.knowledge-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--clr-bg);
}

.knowledge-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 20px;
  border-bottom: 2px solid var(--clr-border);
  background: var(--clr-panel);
  flex-shrink: 0;
}

.header-title {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--clr-yellow);
  letter-spacing: 0.15em;
}

.header-stats {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--clr-text-dim);
  letter-spacing: 0.1em;
}

.nb-btn {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  padding: 6px 14px;
  border: 2px solid var(--clr-yellow);
  background: transparent;
  color: var(--clr-yellow);
  cursor: pointer;
  text-transform: uppercase;
}

.nb-btn:hover { background: var(--clr-yellow); color: var(--clr-bg); }
.nb-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.knowledge-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.card-slide-enter-active,
.card-slide-leave-active {
  transition: width 0.2s ease, opacity 0.2s ease;
  overflow: hidden;
}

.card-slide-enter-from,
.card-slide-leave-to {
  width: 0;
  opacity: 0;
}

.card-slide-enter-to,
.card-slide-leave-from {
  width: 320px;
  opacity: 1;
}
</style>
