<template>
  <div class="timeline-wrap">
    <!-- Year tick axis -->
    <div class="tick-axis" v-if="yearTicks.length">
      <div
        v-for="tick in yearTicks"
        :key="tick.year"
        class="tick"
        :style="{ left: tick.pct + '%' }"
      >
        <span class="tick-label">{{ tick.year }}</span>
      </div>
    </div>

    <!-- Instrument rows -->
    <div
      v-for="row in rows"
      :key="row.instrument_id"
      class="tl-row"
    >
      <div class="tl-label">
        <span class="tl-pair-id">{{ row.instrument_id.replace('_', '/') }}</span>
      </div>
      <div class="tl-track">
        <div
          v-if="row.timeline.has_data && globalBounds.minTs !== null"
          class="tl-bar"
          :style="barStyle(row)"
          :title="`${row.instrument_id}: ${row.timeline.start} → ${row.timeline.end}`"
        ></div>
        <div v-else class="tl-bar tl-bar--empty" style="width: 100%">
          <span class="no-data-label">NO DATA</span>
        </div>
      </div>
    </div>

    <div v-if="!rows.length" class="tl-empty">No coverage data available.</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  rows: { type: Array, required: true },
  globalBounds: { type: Object, required: true },
})

const COLORS = {
  EUR_USD: '#FFE600',
  GBP_USD: '#00FF41',
  USD_JPY: '#FF6B00',
  AUD_USD: '#4FC3F7',
  USD_CHF: '#FF2222',
  USD_CAD: '#CC66FF',
  NZD_USD: '#FF9900',
  EUR_GBP: '#00FFFF',
  EUR_JPY: '#FF66CC',
  GBP_JPY: '#99FF66',
}

function getColor(id) {
  return COLORS[id] || '#888888'
}

function barStyle(row) {
  const { minTs, maxTs } = props.globalBounds
  const totalSpan = maxTs - minTs
  if (!totalSpan) return { left: '0%', width: '100%', background: getColor(row.instrument_id) }
  let left = ((row.timeline.startTs - minTs) / totalSpan) * 100
  let width = ((row.timeline.endTs - row.timeline.startTs) / totalSpan) * 100
  if (width < 0.5) width = 0.5
  return {
    left: left + '%',
    width: width + '%',
    background: getColor(row.instrument_id),
  }
}

const yearTicks = computed(() => {
  const { minTs, maxTs } = props.globalBounds
  if (!minTs || !maxTs) return []
  const totalSpan = maxTs - minTs
  const startYear = new Date(minTs).getFullYear()
  const endYear = new Date(maxTs).getFullYear()
  const ticks = []
  for (let y = startYear; y <= endYear; y++) {
    const ts = new Date(y, 0, 1).getTime()
    const pct = ((ts - minTs) / totalSpan) * 100
    if (pct >= 0 && pct <= 100) {
      ticks.push({ year: y, pct: Math.max(0, pct) })
    }
  }
  return ticks
})
</script>

<style scoped>
.timeline-wrap {
  font-family: var(--font-mono);
  font-size: 11px;
  position: relative;
}

.tick-axis {
  position: relative;
  height: 24px;
  margin-left: 120px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--clr-border);
}

.tick {
  position: absolute;
  bottom: 0;
  transform: translateX(-50%);
}

.tick-label {
  font-size: 9px;
  color: var(--clr-text-dim);
  letter-spacing: 0.08em;
  white-space: nowrap;
}

.tl-row {
  display: flex;
  align-items: center;
  height: 32px;
  margin-bottom: 3px;
}

.tl-label {
  width: 120px;
  flex-shrink: 0;
  padding-right: 12px;
  text-align: right;
}

.tl-pair-id {
  font-size: 12px;
  font-weight: 700;
  color: var(--clr-text);
  letter-spacing: 0.05em;
}

.tl-track {
  flex: 1;
  position: relative;
  height: 20px;
  background: var(--clr-bg);
  border: 1px solid var(--clr-border);
}

.tl-bar {
  position: absolute;
  top: 0;
  height: 100%;
  opacity: 0.8;
  transition: opacity 0.15s;
  cursor: default;
}

.tl-bar:hover {
  opacity: 1;
}

.tl-bar--empty {
  background: var(--clr-surface);
  display: flex;
  align-items: center;
  justify-content: center;
}

.no-data-label {
  font-size: 9px;
  color: var(--clr-text-dim);
  letter-spacing: 0.1em;
}

.tl-empty {
  color: var(--clr-text-dim);
  padding: 16px 0;
  text-align: center;
}
</style>
