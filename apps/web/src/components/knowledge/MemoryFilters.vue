<template>
  <div class="memory-filters">
    <div class="filters-header nb-label">KNOWLEDGE FILTERS</div>

    <div class="filter-group">
      <div class="nb-label">INSTRUMENT</div>
      <select class="nb-select" v-model="localInstrument" @change="emitFilters">
        <option value="">ALL PAIRS</option>
        <option v-for="inst in instruments" :key="inst" :value="inst">{{ inst }}</option>
      </select>
    </div>

    <div class="filter-group">
      <div class="nb-label">OUTCOME</div>
      <div class="outcome-toggles">
        <button
          v-for="opt in outcomeOptions"
          :key="opt.value"
          :class="['outcome-btn', localOutcome === opt.value && 'outcome-btn--active', `outcome-btn--${opt.cls}`]"
          @click="toggleOutcome(opt.value)"
        >{{ opt.label }}</button>
      </div>
    </div>

    <div class="filter-group">
      <div class="nb-label">MEMORIES ({{ store.memories.length }})</div>
      <div class="memory-list">
        <div
          v-for="mem in store.memories"
          :key="mem.id"
          :class="['memory-row', store.selectedMemoryId === mem.id && 'memory-row--active']"
          @click="store.selectNode('mem_' + mem.id)"
        >
          <span :class="['outcome-dot', `outcome-dot--${mem.outcome.toLowerCase()}`]"></span>
          <div class="memory-row-text">
            <span class="memory-pair">{{ mem.instrument }} {{ mem.timeframe }}</span>
            <span class="memory-theory">{{ mem.theory.slice(0, 60) }}{{ mem.theory.length > 60 ? '...' : '' }}</span>
          </div>
        </div>
        <div v-if="!store.memories.length" class="empty-state">NO MEMORIES YET</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useMemoryStore } from '@/stores/useMemoryStore.js'

const store = useMemoryStore()
const emit = defineEmits(['filter-change'])

const localInstrument = ref('')
const localOutcome = ref('')

const instruments = computed(() => {
  const set = new Set(store.memories.map(m => m.instrument))
  return [...set].sort()
})

const outcomeOptions = [
  { value: '', label: 'ALL', cls: 'all' },
  { value: 'POSITIVE', label: '+', cls: 'positive' },
  { value: 'NEGATIVE', label: '-', cls: 'negative' },
  { value: 'NEUTRAL', label: '~', cls: 'neutral' },
]

function toggleOutcome(val) {
  localOutcome.value = localOutcome.value === val ? '' : val
  emitFilters()
}

function emitFilters() {
  emit('filter-change', {
    instrument: localInstrument.value || null,
    outcome: localOutcome.value || null,
  })
}
</script>

<style scoped>
.memory-filters {
  width: 300px;
  flex-shrink: 0;
  background: var(--clr-surface);
  border-right: 2px solid var(--clr-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.filters-header {
  padding: 12px 16px;
  border-bottom: 2px solid var(--clr-border);
  background: var(--clr-panel);
  color: var(--clr-yellow);
  font-size: 11px;
  letter-spacing: 0.15em;
}

.filter-group {
  padding: 12px 16px;
  border-bottom: 1px solid var(--clr-border);
}

.nb-label {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.15em;
  color: var(--clr-text-dim);
  text-transform: uppercase;
  margin-bottom: 6px;
}

.nb-select {
  width: 100%;
  background: var(--clr-bg);
  border: 2px solid var(--clr-border);
  color: var(--clr-text, #e0e0e0);
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 4px 8px;
}

.outcome-toggles {
  display: flex;
  gap: 6px;
}

.outcome-btn {
  flex: 1;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  padding: 4px 0;
  border: 2px solid var(--clr-border);
  background: var(--clr-bg);
  cursor: pointer;
  letter-spacing: 0.1em;
  color: var(--clr-text-dim);
}

.outcome-btn--active.outcome-btn--positive { border-color: var(--clr-green); color: var(--clr-green); }
.outcome-btn--active.outcome-btn--negative { border-color: var(--clr-red); color: var(--clr-red); }
.outcome-btn--active.outcome-btn--neutral { border-color: var(--clr-yellow); color: var(--clr-yellow); }
.outcome-btn--active.outcome-btn--all { border-color: var(--clr-text, #e0e0e0); color: var(--clr-text, #e0e0e0); }

.memory-list {
  flex: 1;
  overflow-y: auto;
  max-height: calc(100vh - 280px);
}

.memory-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--clr-border);
  cursor: pointer;
  transition: background 0.1s;
}

.memory-row:hover, .memory-row--active {
  background: var(--clr-panel);
}

.outcome-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 3px;
}

.outcome-dot--positive { background: var(--clr-green); }
.outcome-dot--negative { background: var(--clr-red); }
.outcome-dot--neutral { background: var(--clr-yellow); }

.memory-row-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.memory-pair {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  color: var(--clr-yellow);
  letter-spacing: 0.1em;
}

.memory-theory {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--clr-text-dim);
  line-height: 1.3;
  word-break: break-word;
}

.empty-state {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-text-dim);
  text-align: center;
  padding: 24px 0;
  letter-spacing: 0.1em;
}
</style>
