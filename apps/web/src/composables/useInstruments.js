import { ref, onMounted } from 'vue'
import { getDefaultInstruments } from '@/api/instruments.js'

/**
 * useInstruments
 *
 * Fetches the default instrument list from GET /api/instruments/defaults on
 * mount and exposes a normalised instrument array, a loading flag, and any
 * fetch error.
 *
 * Normalisation: each instrument receives `id`, `instrument_id`, and
 * `display_name` aliases so consumers don't have to know the exact key
 * returned by the backend (which varies between `symbol`, `id`, and
 * `instrument_id` in different contexts).
 *
 * @returns {{
 *   instruments: import('vue').Ref<Array>,
 *   loading:     import('vue').Ref<boolean>,
 *   error:       import('vue').Ref<string|null>,
 *   refresh:     () => Promise<void>,
 * }}
 */
export function useInstruments() {
  const instruments = ref([])
  const loading = ref(false)
  const error = ref(null)

  async function refresh() {
    loading.value = true
    error.value = null
    try {
      const data = await getDefaultInstruments()
      const raw = Array.isArray(data) ? data : (data?.instruments ?? [])
      instruments.value = raw.map((inst) => ({
        ...inst,
        id: inst.id ?? inst.symbol,
        instrument_id: inst.instrument_id ?? inst.symbol,
        display_name: inst.display_name ?? inst.symbol,
      }))
    } catch (err) {
      error.value =
        err?.normalized?.message ??
        err?.response?.data?.detail ??
        err?.message ??
        'Failed to load instruments'
    } finally {
      loading.value = false
    }
  }

  onMounted(refresh)

  return { instruments, loading, error, refresh }
}
