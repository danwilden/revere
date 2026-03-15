import { ref, watch, computed } from 'vue'
import { getMarketDataRanges } from '../api/marketData.js'

/**
 * useDataRanges
 *
 * Reactively fetches available market data coverage for a given instrument and
 * exposes computed date bounds filtered to the selected timeframe.
 *
 * One API call fetches all timeframe records for the instrument. Timeframe
 * changes re-filter in memory without a new network request.
 *
 * @param {import('vue').Ref<string>} instrumentId - reactive instrument id (e.g. "EUR_USD")
 * @param {import('vue').Ref<string>} timeframe    - reactive timeframe (e.g. "H1", "H4", "D")
 *
 * @returns {{
 *   minDate:      import('vue').ComputedRef<string>,
 *   maxDate:      import('vue').ComputedRef<string>,
 *   rangeLabel:   import('vue').ComputedRef<string>,
 *   hasData:      import('vue').ComputedRef<boolean>,
 *   isLoading:    import('vue').Ref<boolean>,
 *   fetchRanges:  () => Promise<void>,
 * }}
 */
export function useDataRanges(instrumentId, timeframe) {
  // Full coverage array for the current instrument — all timeframes in one fetch.
  const ranges = ref([])
  const isLoading = ref(false)

  async function fetchRanges() {
    const id = instrumentId.value
    if (!id) {
      ranges.value = []
      return
    }

    isLoading.value = true
    try {
      const data = await getMarketDataRanges(id)
      ranges.value = Array.isArray(data?.ranges) ? data.ranges : []
    } catch (err) {
      console.error('[useDataRanges] Failed to fetch ranges:', err?.normalized?.message ?? err?.message ?? err)
      ranges.value = []
    } finally {
      isLoading.value = false
    }
  }

  // Re-fetch whenever the instrument changes. Clear immediately on empty id.
  watch(
    () => instrumentId.value,
    (newId) => {
      if (!newId) {
        ranges.value = []
        return
      }
      fetchRanges()
    },
    { immediate: false }
  )

  // Filtered record for the current instrument + timeframe combination.
  const activeRecord = computed(() => {
    const id = instrumentId.value
    const tf = timeframe.value
    if (!id || !tf) return null
    return (
      ranges.value.find(
        (r) => r.instrument_id === id && r.timeframe === tf && r.has_data === true
      ) ?? null
    )
  })

  // Extract "YYYY-MM-DD" from an ISO datetime string, or return "" on null/missing.
  const isoToDate = (isoStr) => (isoStr ? isoStr.slice(0, 10) : '')

  const minDate = computed(() => isoToDate(activeRecord.value?.start ?? null))
  const maxDate = computed(() => isoToDate(activeRecord.value?.end ?? null))

  const hasData = computed(() => activeRecord.value !== null)

  const rangeLabel = computed(() => {
    if (!hasData.value) return 'No data loaded'
    return `Data: ${minDate.value} \u2192 ${maxDate.value}`
  })

  return {
    minDate,
    maxDate,
    rangeLabel,
    hasData,
    isLoading,
    fetchRanges,
  }
}
