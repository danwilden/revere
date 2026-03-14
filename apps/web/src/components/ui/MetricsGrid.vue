<template>
  <div class="metrics-grid">
    <div
      v-for="metric in metrics"
      :key="metric.label"
      :class="['metric-tile', metric.highlight && 'metric-tile--highlight']"
    >
      <span class="nb-label">{{ metric.label }}</span>
      <span
        :class="[
          'nb-value nb-value--lg',
          metric.positive === true && 'nb-value--positive',
          metric.positive === false && 'nb-value--negative',
          metric.highlight && 'nb-value--accent',
        ]"
      >
        {{ formatValue(metric) }}
      </span>
      <span v-if="metric.unit" class="nb-label" style="margin-top: 2px">
        {{ metric.unit }}
      </span>
    </div>
  </div>
</template>

<script setup>
defineProps({
  /**
   * Array of metric objects:
   * {
   *   label: string,
   *   value: number | string | null,
   *   unit?: string,
   *   highlight?: boolean,     — yellow accent tile
   *   positive?: boolean,      — true = green, false = red, undefined = default
   *   decimals?: number,       — decimal places for numeric values (default 2)
   * }
   */
  metrics: {
    type: Array,
    required: true,
  },
})

function formatValue(metric) {
  if (metric.value === null || metric.value === undefined) return '—'
  if (typeof metric.value === 'number') {
    const dec = metric.decimals ?? 2
    return metric.value.toFixed(dec)
  }
  return String(metric.value)
}
</script>
