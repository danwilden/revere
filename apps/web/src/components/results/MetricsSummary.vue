<template>
  <NbCard title="PERFORMANCE METRICS">
    <MetricsGrid :metrics="formattedMetrics" />
  </NbCard>
</template>

<script setup>
import { computed } from 'vue'
import NbCard from '@/components/ui/NbCard.vue'
import MetricsGrid from '@/components/ui/MetricsGrid.vue'
import { ANNUALIZED_RETURN_LABEL } from '@/utils/constants.js'

const props = defineProps({
  /**
   * Flat array of PerformanceMetric objects from run.metrics,
   * pre-filtered to segment_type === 'overall'
   * Shape: { metric_name, metric_value, segment_type, segment_key }
   */
  metrics: {
    type: Array,
    required: true,
  },
})

/**
 * Look up a metric value by name from the metrics array.
 * Returns null if not found.
 */
function val(name) {
  const m = props.metrics.find((m) => m.metric_name === name)
  return m?.metric_value ?? null
}

/**
 * win_rate comes back as a decimal (0.0–1.0) from the backend.
 * Multiply by 100 for display. If already > 1, treat as already a percentage.
 */
function toPercent(v) {
  if (v === null || v === undefined) return null
  return v <= 1.0 ? v * 100 : v
}

const formattedMetrics = computed(() => {
  const totalTrades = val('total_trades')
  const winRate = toPercent(val('win_rate'))
  const netPnl = val('net_pnl')
  const netReturnPct = toPercent(val('net_return_pct'))
  const annualizedReturnPct = toPercent(val('annualized_return_pct'))
  const sharpeRatio = val('sharpe_ratio')
  const sortinoRatio = val('sortino_ratio')
  const maxDrawdownPct = val('max_drawdown_pct')
  // max_drawdown_pct may be stored as a negative fraction or a negative percentage
  // Normalise to a positive display value
  const maxDdDisplay = maxDrawdownPct !== null ? Math.abs(maxDrawdownPct) : null
  const maxDdIsLarge = maxDdDisplay !== null && maxDdDisplay > 20
  const profitFactor = val('profit_factor')
  const expectancy = val('expectancy')

  return [
    {
      label: 'TOTAL TRADES',
      value: totalTrades,
      decimals: 0,
    },
    {
      label: 'WIN RATE',
      value: winRate,
      unit: '%',
      decimals: 1,
      positive: winRate !== null ? winRate >= 50 : undefined,
    },
    {
      label: 'NET P&L',
      value: netPnl,
      decimals: 2,
      positive: netPnl !== null ? netPnl >= 0 : undefined,
    },
    {
      label: 'NET RETURN',
      value: netReturnPct,
      unit: '%',
      decimals: 2,
      positive: netReturnPct !== null ? netReturnPct >= 0 : undefined,
    },
    {
      label: ANNUALIZED_RETURN_LABEL,
      value: annualizedReturnPct,
      unit: '%',
      decimals: 2,
      positive: annualizedReturnPct !== null ? annualizedReturnPct >= 0 : undefined,
    },
    {
      label: 'SHARPE RATIO',
      value: sharpeRatio,
      decimals: 3,
      positive: sharpeRatio !== null ? sharpeRatio >= 1 : undefined,
    },
    {
      label: 'SORTINO RATIO',
      value: sortinoRatio,
      decimals: 3,
      positive: sortinoRatio !== null ? sortinoRatio >= 1 : undefined,
    },
    {
      label: 'MAX DRAWDOWN',
      value: maxDdDisplay,
      unit: '%',
      decimals: 2,
      positive: false,
      highlight: maxDdIsLarge,
    },
    {
      label: 'PROFIT FACTOR',
      value: profitFactor,
      decimals: 2,
      positive: profitFactor !== null ? profitFactor >= 1 : undefined,
    },
    {
      label: 'EXPECTANCY',
      value: expectancy,
      decimals: 4,
      positive: expectancy !== null ? expectancy >= 0 : undefined,
    },
  ]
})
</script>
