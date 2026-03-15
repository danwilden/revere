import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getMarketDataRanges, getInstruments } from '@/api/instruments.js'

const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']

const MAJOR_PAIRS = ['EUR_USD', 'GBP_USD', 'USD_JPY', 'USD_CHF', 'USD_CAD', 'AUD_USD', 'NZD_USD']

export const useCoverageStore = defineStore('coverage', () => {
  const ranges = ref([])
  const instruments = ref([])
  const loading = ref(false)
  const error = ref(null)
  const lastUpdated = ref(null)

  // O(1) lookup: "EUR_USD::M1" -> range record
  const coverageByKey = computed(() => {
    const map = new Map()
    for (const r of ranges.value) {
      map.set(`${r.instrument_id}::${r.timeframe}`, r)
    }
    return map
  })

  // min/max timestamps across all has_data records, in ms
  const globalDateBounds = computed(() => {
    let minTs = Infinity
    let maxTs = -Infinity
    for (const r of ranges.value) {
      if (!r.has_data) continue
      const s = new Date(r.start).getTime()
      const e = new Date(r.end).getTime()
      if (s < minTs) minTs = s
      if (e > maxTs) maxTs = e
    }
    if (minTs === Infinity) return { minTs: null, maxTs: null }
    return { minTs, maxTs }
  })

  // Pre-joined rows for both matrix and timeline components
  const coverageRows = computed(() => {
    // Instruments use `symbol` as their identifier
    const instrMap = new Map(instruments.value.map(i => [i.symbol, i]))
    // Collect unique instrument IDs from ranges
    const ids = [...new Set(ranges.value.map(r => r.instrument_id))]
    // Also include instruments even if no ranges yet
    for (const i of instruments.value) {
      if (!ids.includes(i.symbol)) ids.push(i.symbol)
    }

    const rows = ids.map(id => {
      const meta = instrMap.get(id) || { symbol: id, category: 'minor' }
      const cells = TIMEFRAMES.map(tf => {
        const key = `${id}::${tf}`
        const rec = coverageByKey.value.get(key)
        if (rec && rec.has_data) {
          const startTs = new Date(rec.start).getTime()
          const endTs = new Date(rec.end).getTime()
          const daysSpan = Math.round((endTs - startTs) / 86400000)
          return { timeframe: tf, has_data: true, start: rec.start, end: rec.end, daysSpan }
        }
        return { timeframe: tf, has_data: false, start: null, end: null, daysSpan: 0 }
      })

      // Timeline uses M1 span (widest coverage)
      const m1 = cells.find(c => c.timeframe === 'M1')
      const timeline = m1 && m1.has_data
        ? { has_data: true, start: m1.start, end: m1.end, startTs: new Date(m1.start).getTime(), endTs: new Date(m1.end).getTime() }
        : { has_data: false, start: null, end: null, startTs: null, endTs: null }

      return {
        instrument_id: id,
        display_name: meta.symbol || id,
        category: meta.category || 'minor',
        cells,
        timeline,
      }
    })

    // Sort: major pairs first, then alphabetical
    rows.sort((a, b) => {
      const ai = MAJOR_PAIRS.indexOf(a.instrument_id)
      const bi = MAJOR_PAIRS.indexOf(b.instrument_id)
      if (ai !== -1 && bi !== -1) return ai - bi
      if (ai !== -1) return -1
      if (bi !== -1) return 1
      return a.instrument_id.localeCompare(b.instrument_id)
    })

    return rows
  })

  async function load() {
    loading.value = true
    error.value = null
    try {
      const [rangesData, instrumentsData] = await Promise.all([
        getMarketDataRanges(),
        getInstruments(),
      ])
      ranges.value = Array.isArray(rangesData) ? rangesData : (rangesData.ranges ?? [])
      instruments.value = Array.isArray(instrumentsData) ? instrumentsData : (instrumentsData.instruments ?? [])
      lastUpdated.value = new Date()
    } catch (e) {
      error.value = e?.message || 'Failed to load coverage data'
    } finally {
      loading.value = false
    }
  }

  async function reload() {
    lastUpdated.value = null
    await load()
  }

  return {
    ranges,
    instruments,
    loading,
    error,
    lastUpdated,
    coverageByKey,
    globalDateBounds,
    coverageRows,
    load,
    reload,
  }
})
