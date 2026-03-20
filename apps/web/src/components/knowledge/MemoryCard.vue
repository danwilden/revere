<template>
  <div class="memory-card" v-if="memory">
    <div class="card-header">
      <div class="card-title nb-label">MEMORY DETAIL</div>
      <button class="close-btn" @click="store.selectNode(null)">&#x2715;</button>
    </div>

    <div class="card-section">
      <div class="nb-label">PAIR / TIMEFRAME</div>
      <div class="card-value">{{ memory.instrument }} {{ memory.timeframe }}</div>
    </div>

    <div class="card-section">
      <div class="nb-label">OUTCOME</div>
      <span :class="['outcome-badge', `outcome-badge--${memory.outcome.toLowerCase()}`]">
        {{ memory.outcome }}
      </span>
      <span v-if="memory.sharpe != null" class="metric-inline">
        SHARPE {{ memory.sharpe.toFixed(3) }}
      </span>
      <span v-if="memory.total_trades != null" class="metric-inline">
        {{ memory.total_trades }} TRADES
      </span>
    </div>

    <div class="card-section">
      <div class="nb-label">THEORY</div>
      <div class="card-text">{{ memory.theory }}</div>
    </div>

    <div class="card-section">
      <div class="nb-label">RESULTS REASONING</div>
      <div class="card-text card-text--dim">{{ memory.results_reasoning }}</div>
    </div>

    <div class="card-section" v-if="memory.learnings?.length">
      <div class="nb-label">LEARNINGS</div>
      <ul class="learnings-list">
        <li v-for="(learning, i) in memory.learnings" :key="i" class="learning-item">
          {{ learning }}
        </li>
      </ul>
    </div>

    <div class="card-section" v-if="memory.tags?.length">
      <div class="nb-label">TAGS</div>
      <div class="tags-row">
        <span v-for="tag in memory.tags" :key="tag" class="tag-chip">{{ tag }}</span>
      </div>
    </div>

    <div class="card-section" v-if="memory.experiment_ids?.length">
      <div class="nb-label">EXPERIMENTS</div>
      <div v-for="eid in memory.experiment_ids" :key="eid" class="exp-id">{{ eid }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useMemoryStore } from '@/stores/useMemoryStore.js'

const store = useMemoryStore()
const memory = computed(() => store.selectedMemory)
</script>

<style scoped>
.memory-card {
  width: 320px;
  flex-shrink: 0;
  background: var(--clr-surface);
  border-left: 2px solid var(--clr-border);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  font-family: var(--font-mono);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 2px solid var(--clr-border);
  background: var(--clr-panel);
}

.card-title {
  color: var(--clr-yellow);
  font-size: 11px;
  letter-spacing: 0.15em;
}

.close-btn {
  background: none;
  border: none;
  color: var(--clr-text-dim);
  font-size: 14px;
  cursor: pointer;
  font-family: var(--font-mono);
}

.close-btn:hover { color: var(--clr-yellow); }

.card-section {
  padding: 10px 16px;
  border-bottom: 1px solid var(--clr-border);
}

.nb-label {
  font-size: 9px;
  letter-spacing: 0.15em;
  color: var(--clr-text-dim);
  text-transform: uppercase;
  margin-bottom: 4px;
}

.card-value {
  font-size: 13px;
  font-weight: 700;
  color: var(--clr-yellow);
  letter-spacing: 0.08em;
}

.card-text {
  font-size: 11px;
  line-height: 1.5;
  color: var(--clr-text, #e0e0e0);
}

.card-text--dim {
  color: var(--clr-text-dim);
}

.outcome-badge {
  display: inline-block;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.12em;
  padding: 2px 6px;
  border: 2px solid;
  margin-right: 8px;
}

.outcome-badge--positive { border-color: #4caf50; color: #4caf50; }
.outcome-badge--negative { border-color: #f44336; color: #f44336; }
.outcome-badge--neutral { border-color: #ffeb3b; color: #ffeb3b; }

.metric-inline {
  font-size: 9px;
  color: var(--clr-text-dim);
  margin-right: 8px;
}

.learnings-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.learning-item {
  font-size: 10px;
  line-height: 1.5;
  color: var(--clr-text, #e0e0e0);
  padding: 2px 0;
  padding-left: 12px;
  position: relative;
}

.learning-item::before {
  content: '\25B8';
  position: absolute;
  left: 0;
  color: var(--clr-yellow);
}

.tags-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tag-chip {
  font-size: 9px;
  padding: 2px 6px;
  border: 1px solid var(--clr-border);
  color: var(--clr-text-dim);
  letter-spacing: 0.08em;
}

.exp-id {
  font-size: 9px;
  color: var(--clr-text-dim);
  font-family: var(--font-mono);
  word-break: break-all;
  padding: 2px 0;
}
</style>
