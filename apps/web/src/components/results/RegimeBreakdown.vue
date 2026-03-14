<template>
  <NbCard title="PER-REGIME BREAKDOWN">
    <div v-if="rows.length" class="regime-table-wrapper">
      <table class="nb-table regime-table">
        <thead>
          <tr>
            <th class="col-regime">REGIME</th>
            <th class="col-num">TRADES</th>
            <th class="col-num">WIN RATE</th>
            <th class="col-num">NET P&L</th>
            <th class="col-num">EXPECTANCY</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.label">
            <td>
              <span
                class="regime-label font-mono"
                :style="{ color: regimeColor(row.label), borderColor: regimeColor(row.label) }"
              >
                {{ row.label }}
              </span>
            </td>
            <td class="font-mono text-right">
              {{ fmt(row.total_trades, 0) }}
            </td>
            <td class="font-mono text-right">
              <span :class="winRateClass(row.win_rate)">
                {{ fmtPct(row.win_rate) }}
              </span>
            </td>
            <td class="font-mono text-right">
              <span :class="pnlClass(row.net_pnl)">
                {{ fmtPnl(row.net_pnl) }}
              </span>
            </td>
            <td class="font-mono text-right">
              <span :class="pnlClass(row.expectancy)">
                {{ fmt(row.expectancy, 4) }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else message="NO REGIME DATA" />
  </NbCard>
</template>

<script setup>
import { computed } from 'vue'
import NbCard from '@/components/ui/NbCard.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { REGIME_COLORS } from '@/utils/constants.js'

const props = defineProps({
  /**
   * Map<string, PerformanceMetric[]> — regime label → metrics array.
   * Each PerformanceMetric: { metric_name, metric_value, segment_type, segment_key }
   */
  regimeMetricsByLabel: {
    type: Map,
    required: true,
  },
})

function getMetric(metricsArr, name) {
  const m = metricsArr.find((x) => x.metric_name === name)
  return m?.metric_value ?? null
}

const rows = computed(() => {
  const result = []
  for (const [label, metricsArr] of props.regimeMetricsByLabel) {
    result.push({
      label,
      total_trades: getMetric(metricsArr, 'total_trades'),
      win_rate: getMetric(metricsArr, 'win_rate'),
      net_pnl: getMetric(metricsArr, 'net_pnl'),
      expectancy: getMetric(metricsArr, 'expectancy'),
    })
  }
  // Sort by total_trades descending so the most active regime is first
  result.sort((a, b) => (b.total_trades ?? 0) - (a.total_trades ?? 0))
  return result
})

function regimeColor(label) {
  return REGIME_COLORS[label] ?? REGIME_COLORS.UNKNOWN
}

function fmt(val, decimals = 2) {
  if (val === null || val === undefined) return '—'
  return Number(val).toFixed(decimals)
}

function fmtPct(val) {
  if (val === null || val === undefined) return '—'
  const pct = val <= 1.0 ? val * 100 : val
  return pct.toFixed(1) + '%'
}

function fmtPnl(val) {
  if (val === null || val === undefined) return '—'
  const n = Number(val)
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}

function pnlClass(val) {
  if (val === null || val === undefined) return 'text-muted'
  return Number(val) >= 0 ? 'text-green' : 'text-red'
}

function winRateClass(val) {
  if (val === null || val === undefined) return 'text-muted'
  const pct = val <= 1.0 ? val * 100 : val
  return pct >= 50 ? 'text-green' : 'text-red'
}
</script>

<style scoped>
.regime-table-wrapper {
  overflow-x: auto;
}

.regime-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.regime-table th {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--clr-text-dim);
  text-transform: uppercase;
  text-align: left;
  padding: 8px 12px;
  border-bottom: 2px solid var(--clr-border);
  white-space: nowrap;
}

.regime-table th.col-num,
.regime-table td.text-right {
  text-align: right;
}

.regime-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #1e1e1e;
  font-size: 12px;
  white-space: nowrap;
}

.regime-table tbody tr:hover {
  background: #1a1a1a;
}

.regime-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 2px 6px;
  border: 1px solid;
  display: inline-block;
}
</style>
