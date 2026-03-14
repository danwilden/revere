<template>
  <NbCard title="EQUITY CURVE">
    <div v-if="hasData" class="chart-wrapper">
      <div ref="chartEl" class="chart-container" />
    </div>
    <EmptyState v-else message="NO EQUITY DATA" />
    <WarningBanner :message="EQUITY_CURVE_NOTE" style="margin-top: 12px" />
  </NbCard>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { createChart } from 'lightweight-charts'
import NbCard from '@/components/ui/NbCard.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import WarningBanner from '@/components/ui/WarningBanner.vue'
import { EQUITY_CURVE_NOTE } from '@/utils/constants.js'

const props = defineProps({
  /** Array of equity values, e.g. [10000, 10050, 10120, ...] */
  equity: {
    type: Array,
    default: () => [],
  },
  /** Array of ISO datetime strings, one per equity value */
  timestamps: {
    type: Array,
    default: () => [],
  },
})

const chartEl = ref(null)
let chart = null
let series = null
let resizeObserver = null

const hasData = computed(
  () => Array.isArray(props.equity) && props.equity.length > 0
)

/**
 * Convert equity + timestamps to lightweight-charts data format.
 * lightweight-charts v4 accepts 'YYYY-MM-DD' strings as time values.
 * For intraday data (multiple bars per day), we use Unix timestamps (seconds).
 */
function buildChartData() {
  if (!hasData.value) return []
  const result = []
  const seen = new Set()

  for (let i = 0; i < props.equity.length; i++) {
    const ts = props.timestamps[i]
    const eq = props.equity[i]
    if (ts === undefined || ts === null || eq === null || eq === undefined) continue

    // Convert ISO string to Unix seconds for intraday precision
    const epoch = Math.floor(new Date(ts).getTime() / 1000)
    if (isNaN(epoch)) continue
    // lightweight-charts requires strictly ascending time — deduplicate
    if (seen.has(epoch)) continue
    seen.add(epoch)

    result.push({ time: epoch, value: eq })
  }

  // Ensure ascending order
  result.sort((a, b) => a.time - b.time)
  return result
}

function initChart() {
  if (!chartEl.value || !hasData.value) return

  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: 260,
    layout: {
      background: { color: '#111111' },
      textColor: '#888888',
      fontFamily: "'Share Tech Mono', monospace",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: '#1e1e1e' },
      horzLines: { color: '#1e1e1e' },
    },
    crosshair: {
      vertLine: { color: '#444444', width: 1, style: 3 },
      horzLine: { color: '#444444', width: 1, style: 3 },
    },
    rightPriceScale: {
      borderColor: '#333333',
    },
    timeScale: {
      borderColor: '#333333',
      timeVisible: true,
      secondsVisible: false,
    },
    handleScroll: true,
    handleScale: true,
  })

  series = chart.addAreaSeries({
    lineColor: '#00FF41',
    topColor: 'rgba(0, 255, 65, 0.18)',
    bottomColor: 'rgba(0, 255, 65, 0.0)',
    lineWidth: 2,
    priceLineVisible: true,
    priceLineColor: '#00FF41',
    priceLineStyle: 3,
    lastValueVisible: true,
  })

  series.setData(buildChartData())
  chart.timeScale().fitContent()

  // ResizeObserver for responsive width
  resizeObserver = new ResizeObserver((entries) => {
    if (!chart || !entries.length) return
    const { width } = entries[0].contentRect
    chart.applyOptions({ width })
  })
  resizeObserver.observe(chartEl.value)
}

function destroyChart() {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chart) {
    chart.remove()
    chart = null
    series = null
  }
}

onMounted(() => {
  if (hasData.value) initChart()
})

// Re-initialise when data arrives after mount (async load)
watch(
  () => [props.equity, props.timestamps],
  () => {
    destroyChart()
    if (hasData.value) {
      // Wait one tick for the DOM to reflect the chart-container
      setTimeout(initChart, 0)
    }
  },
  { deep: false }
)

onUnmounted(destroyChart)
</script>

<style scoped>
.chart-wrapper {
  width: 100%;
  overflow: hidden;
}

.chart-container {
  width: 100%;
  height: 260px;
  background: #111111;
  border: 1px solid var(--clr-border);
}
</style>
