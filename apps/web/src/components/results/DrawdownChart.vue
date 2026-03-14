<template>
  <NbCard title="MAX DRAWDOWN %">
    <div v-if="hasData" class="chart-wrapper">
      <div ref="chartEl" class="chart-container" />
    </div>
    <EmptyState v-else message="NO DRAWDOWN DATA" />
  </NbCard>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { createChart } from 'lightweight-charts'
import NbCard from '@/components/ui/NbCard.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const props = defineProps({
  /** Array of drawdown values (expected as 0 or negative, e.g. [0, -0.5, -1.2]) */
  drawdown: {
    type: Array,
    default: () => [],
  },
  /** Array of ISO datetime strings, one per drawdown value */
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
  () => Array.isArray(props.drawdown) && props.drawdown.length > 0
)

/**
 * Build chart data. Drawdown values may be stored as negative fractions
 * (e.g. -0.05 = -5%) or negative percentage values (e.g. -5.0).
 * We render as-is — the backend stores them as negative percentages.
 */
function buildChartData() {
  if (!hasData.value) return []
  const result = []
  const seen = new Set()

  for (let i = 0; i < props.drawdown.length; i++) {
    const ts = props.timestamps[i]
    const dd = props.drawdown[i]
    if (ts === undefined || ts === null || dd === null || dd === undefined) continue

    const epoch = Math.floor(new Date(ts).getTime() / 1000)
    if (isNaN(epoch)) continue
    if (seen.has(epoch)) continue
    seen.add(epoch)

    result.push({ time: epoch, value: dd })
  }

  result.sort((a, b) => a.time - b.time)
  return result
}

function initChart() {
  if (!chartEl.value || !hasData.value) return

  chart = createChart(chartEl.value, {
    width: chartEl.value.clientWidth,
    height: 180,
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
      // Invert scale so 0 is at top and drawdown shows downward
      invertScale: false,
    },
    timeScale: {
      borderColor: '#333333',
      timeVisible: true,
      secondsVisible: false,
    },
    handleScroll: true,
    handleScale: true,
  })

  // Use AreaSeries with red coloring for drawdown
  series = chart.addAreaSeries({
    lineColor: '#FF2222',
    topColor: 'rgba(255, 34, 34, 0.0)',
    bottomColor: 'rgba(255, 34, 34, 0.25)',
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: true,
  })

  series.setData(buildChartData())
  chart.timeScale().fitContent()

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

watch(
  () => [props.drawdown, props.timestamps],
  () => {
    destroyChart()
    if (hasData.value) {
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
  height: 180px;
  background: #111111;
  border: 1px solid var(--clr-border);
}
</style>
