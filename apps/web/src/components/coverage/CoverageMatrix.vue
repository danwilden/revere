<template>
  <div class="matrix-wrap">
    <!-- Header row -->
    <div class="matrix-grid">
      <div class="cell cell--header cell--label">PAIR</div>
      <div v-for="tf in timeframes" :key="tf" class="cell cell--header cell--tf">{{ tf }}</div>

      <!-- Data rows -->
      <template v-for="row in rows" :key="row.instrument_id">
        <div class="cell cell--pair">
          <span class="pair-id">{{ row.instrument_id.replace('_', '/') }}</span>
          <span :class="['cat-badge', `cat-badge--${row.category}`]">{{ row.category.toUpperCase() }}</span>
        </div>
        <div
          v-for="cell in row.cells"
          :key="cell.timeframe"
          :class="['cell', 'cell--data', cell.has_data ? 'cell--has-data' : 'cell--no-data']"
        >
          <template v-if="cell.has_data">
            <span class="data-badge data-badge--ok">HAS DATA</span>
            <span class="date-range">{{ fmtDate(cell.start) }}</span>
            <span class="date-range">{{ fmtDate(cell.end) }}</span>
            <span class="day-span">{{ cell.daysSpan }}d</span>
          </template>
          <template v-else>
            <span class="data-badge data-badge--none">NO DATA</span>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
defineProps({
  rows: { type: Array, required: true },
  timeframes: { type: Array, default: () => ['M1', 'H1', 'H4', 'D'] },
})

function fmtDate(iso) {
  if (!iso) return '—'
  return iso.slice(0, 10)
}
</script>

<style scoped>
.matrix-wrap {
  overflow-x: auto;
}

.matrix-grid {
  display: grid;
  grid-template-columns: 160px repeat(4, 1fr);
  gap: 2px;
  min-width: 600px;
}

.cell {
  padding: 10px 12px;
  border: 1px solid var(--clr-border);
  background: var(--clr-surface);
  font-family: var(--font-mono);
  font-size: 11px;
  transition: border-color 0.15s;
}

.cell:hover {
  border-color: var(--clr-yellow);
}

.cell--header {
  background: var(--clr-panel);
  color: var(--clr-text-dim);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.cell--tf {
  text-align: center;
}

.cell--label {
  text-align: left;
}

.cell--pair {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pair-id {
  font-size: 13px;
  font-weight: 700;
  color: var(--clr-text);
  letter-spacing: 0.05em;
}

.cat-badge {
  font-size: 9px;
  letter-spacing: 0.1em;
  padding: 1px 5px;
  border: 1px solid;
  display: inline-block;
  width: fit-content;
}

.cat-badge--major {
  color: var(--clr-yellow);
  border-color: var(--clr-yellow);
}

.cat-badge--minor {
  color: var(--clr-text-dim);
  border-color: var(--clr-border);
}

.cell--has-data {
  background: rgba(0, 255, 65, 0.04);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.cell--no-data {
  background: var(--clr-bg);
  display: flex;
  align-items: center;
  justify-content: center;
}

.data-badge {
  font-size: 9px;
  letter-spacing: 0.1em;
  padding: 1px 5px;
  border: 1px solid;
  display: inline-block;
  width: fit-content;
}

.data-badge--ok {
  color: #00ff41;
  border-color: #00ff41;
}

.data-badge--none {
  color: var(--clr-text-dim);
  border-color: var(--clr-border);
}

.date-range {
  font-size: 10px;
  color: var(--clr-text-dim);
  font-family: var(--font-mono);
}

.day-span {
  font-size: 10px;
  color: var(--clr-text);
  font-weight: 600;
}
</style>
