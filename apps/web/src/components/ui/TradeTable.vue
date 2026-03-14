<template>
  <v-data-table
    class="nb-table"
    :headers="headers"
    :items="trades"
    :items-per-page="25"
    density="comfortable"
    :row-props="rowProps"
    item-value="id"
  >
    <!-- Entry time -->
    <template #item.entry_time="{ item }">
      <span class="font-mono" style="font-size: 12px">{{ formatTs(item.entry_time) }}</span>
    </template>

    <!-- Exit time -->
    <template #item.exit_time="{ item }">
      <span class="font-mono" style="font-size: 12px">{{ formatTs(item.exit_time) }}</span>
    </template>

    <!-- Side -->
    <template #item.side="{ item }">
      <span
        :class="['font-mono', item.side === 'long' ? 'text-green' : 'text-red']"
        style="font-weight: 700; font-size: 12px; text-transform: uppercase"
      >
        {{ item.side }}
      </span>
    </template>

    <!-- Entry price -->
    <template #item.entry_price="{ item }">
      <span class="font-mono" style="font-size: 12px">{{ fmt(item.entry_price, 5) }}</span>
    </template>

    <!-- Exit price -->
    <template #item.exit_price="{ item }">
      <span class="font-mono" style="font-size: 12px">{{ fmt(item.exit_price, 5) }}</span>
    </template>

    <!-- PnL -->
    <template #item.pnl="{ item }">
      <span
        :class="['font-mono', pnlClass(item.pnl)]"
        style="font-weight: 700; font-size: 12px"
      >
        {{ item.pnl !== null && item.pnl !== undefined ? (item.pnl >= 0 ? '+' : '') + item.pnl.toFixed(2) : '—' }}
      </span>
    </template>

    <!-- Exit reason -->
    <template #item.exit_reason="{ item }">
      <span class="font-mono text-muted" style="font-size: 11px; text-transform: uppercase">
        {{ item.exit_reason ?? '—' }}
      </span>
    </template>

    <!-- Regime at entry -->
    <template #item.regime_at_entry="{ item }">
      <span
        v-if="item.regime_at_entry"
        :style="{ color: regimeColor(item.regime_at_entry), fontFamily: 'var(--font-mono)', fontSize: '11px' }"
      >
        {{ item.regime_at_entry }}
      </span>
      <span v-else class="text-dim font-mono" style="font-size: 11px">—</span>
    </template>

    <!-- Empty state -->
    <template #no-data>
      <div style="padding: 32px; text-align: center; font-family: var(--font-mono); color: var(--clr-text-muted)">
        NO TRADES
      </div>
    </template>
  </v-data-table>
</template>

<script setup>
import { REGIME_COLORS } from '@/utils/constants.js'

defineProps({
  trades: {
    type: Array,
    default: () => [],
  },
})

const headers = [
  { title: 'Entry', key: 'entry_time', sortable: true },
  { title: 'Exit', key: 'exit_time', sortable: true },
  { title: 'Side', key: 'side', sortable: true },
  { title: 'Entry Px', key: 'entry_price', sortable: false },
  { title: 'Exit Px', key: 'exit_price', sortable: false },
  { title: 'PnL', key: 'pnl', sortable: true },
  { title: 'Reason', key: 'exit_reason', sortable: false },
  { title: 'Regime', key: 'regime_at_entry', sortable: true },
]

function formatTs(ts) {
  if (!ts) return '—'
  try {
    return new Date(ts).toISOString().replace('T', ' ').slice(0, 16)
  } catch {
    return ts
  }
}

function fmt(val, decimals = 2) {
  if (val === null || val === undefined) return '—'
  return Number(val).toFixed(decimals)
}

function pnlClass(pnl) {
  if (pnl === null || pnl === undefined) return 'text-muted'
  return pnl >= 0 ? 'text-green' : 'text-red'
}

function regimeColor(label) {
  return REGIME_COLORS[label] ?? REGIME_COLORS.UNKNOWN
}

function rowProps({ item }) {
  const pnl = item.pnl
  if (pnl === null || pnl === undefined) return {}
  return { class: pnl >= 0 ? 'row--positive' : 'row--negative' }
}
</script>
